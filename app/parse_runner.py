"""解析隔离：把文档解析放进子进程，别让一份坏文件拖垮整个服务。

## 为什么需要

单 worker、单副本是本项目的**硬约束**——内存向量索引、限流与并发闸门都在进程内，
不能靠加 worker 扩容。而解析在上传请求里同进程执行：一份畸形文件只要让解析库在
解压时把内存吃满，就会连带杀掉唯一的进程：**所有在线用户的提问同时中断**，重启后
对同一份文件重新索引还会再撞一次。

文件内容是不可信输入，即使上传者是可信的管理员：设备手册、客户提供的 PDF/DOCX
都来自公司之外，损坏或刻意构造都可能。

现有的资源上限（解压炸弹体检、累计字符数、单元数、切片数）都作用在**提取之后**，
或在交给解析库之前做压缩包层面的体检，管不到解析库**内部**的内存膨胀。所以这里
再补一层进程级隔离：

- **硬超时**：解析超过 ``RAG_PARSE_TIMEOUT_S`` 就终止子进程，报明确错误。
- **内存上限**：POSIX 上用 ``RLIMIT_AS`` 限制子进程地址空间（``RAG_PARSE_MEMORY_MB``）。
  Windows 没有等价的地址空间限制能力，此时只有超时与崩溃隔离生效；这是如实说明的
  能力差异，不是等效实现。
- **崩溃隔离**：子进程段错误、被系统 OOM 杀掉、异常退出，都只表现为一次解析失败，
  主进程继续服务其他用户。

## 终止要杀整棵进程树

``antiword``（.doc 解析）本身还会再起一个子进程。只 terminate 解析子进程会把它变成
孤儿进程继续占内存——本机就发生过孤儿 llama-server 把物理内存吃到 1.4GB、整个应用
被换页到无法响应的事故。因此超时/异常路径统一走 ``_kill_tree``：POSIX 上让子进程
自成进程组后按组杀，Windows 上用 ``taskkill /T``。

## 失败语义

子进程里的 ``ParseError`` 会原样回传（含 code），与不隔离时行为一致；其余失败
（隔离不可用、超时、内存超限、崩溃、解析器未知异常）映射为带明确 code 的 ``ParseError``
（``parse_isolation_unavailable`` / ``parse_timeout`` / ``parse_memory`` /
``parse_crashed`` / ``parse_failed``），并写进文档的失败原因，便于管理员区分
"文件坏了"和"服务器资源不够"。
"""
from __future__ import annotations

import multiprocessing
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import parsing
from .config import settings

# 子进程回传结果的形态
_OK = "ok"
_PARSE_ERROR = "parse_error"
_MEMORY = "memory"
_FAILED = "failed"
_ISOLATION_ERROR = "isolation_error"

# 收结果的三种结局
_RECV_OK = "ok"
_RECV_CLOSED = "closed"      # 通道关闭：子进程没给结果就退出了（崩溃/被强杀）
_RECV_TIMEOUT = "timeout"    # 到点还没动静

# 强杀后等待回收的时间；被杀的子进程几乎立刻退出，这里只是给足余量。
_REAP_GRACE_S = 10.0
# 已经收到结果或 EOF 后，正常子进程只剩解释器收尾；无需为异常卡死再等完整回收窗口。
_NORMAL_EXIT_GRACE_S = 2.0


@dataclass
class IsolatedOutcome:
    """隔离执行的结果。

    ``got`` 为真时 ``result`` 是子进程回传的对象；为假时 ``reason`` 说明原因
    （``timeout`` / ``crashed`` / ``unavailable``），``note`` 是可读说明。
    """

    got: bool
    result: object = None
    reason: str = ""
    note: str = ""
    exit_note: str = field(default="")


def _limit_address_space(mem_mb: int) -> None:
    """POSIX：给子进程地址空间设上限。Windows 无对应能力，直接返回。

    已要求限制时不能静默忽略失败，否则运维看到配置存在会误以为保护已经生效。
    """
    if mem_mb <= 0 or os.name != "posix":
        return
    try:
        import resource  # noqa: PLC0415 - 仅 POSIX 存在
    except ImportError as exc:  # pragma: no cover - 极少数非标准 POSIX Python
        raise RuntimeError("当前 POSIX Python 缺少 resource，无法设置解析内存上限") from exc
    cap = mem_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ValueError, OSError) as exc:  # pragma: no cover - 取决于宿主内核策略
        raise RuntimeError(f"无法设置解析内存上限：{exc}") from exc


def _isolated_entry(target, conn, args: tuple) -> None:
    """所有隔离任务的统一入口；先建立独立进程组，再运行目标函数。"""
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            # _kill_tree 还会核对 pgid==pid；未成功独立时只杀直接子进程，绝不误杀父组。
            pass
    target(conn, *args)


def _child_main(conn, kind: str, path_str: str, mem_mb: int) -> None:
    """解析子进程入口：只做解析并回传，绝不触碰数据库、向量索引或嵌入模型。

    自成进程组（POSIX）是为了让父进程能按组杀掉整棵树，包括 antiword 这类孙进程。
    """
    try:
        try:
            _limit_address_space(mem_mb)
        except RuntimeError as exc:
            _send(conn, (_ISOLATION_ERROR, str(exc)))
            return
        units = parsing.PARSERS[kind](Path(path_str))
        payload = [(u.text, u.page, u.paragraph) for u in units]
        _send(conn, (_OK, payload))
    except parsing.ParseError as exc:
        # 解析器自己的错误原样回传，保证隔离前后错误信息与 code 一致。
        _send(conn, (_PARSE_ERROR, (exc.message, exc.code)))
    except MemoryError:
        _send(conn, (_MEMORY, mem_mb))
    except BaseException as exc:  # noqa: BLE001 - 子进程必须把任何异常变成回传，不能靠 traceback
        _send(conn, (_FAILED, f"{exc.__class__.__name__}: {exc}"))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _send(conn, obj) -> None:
    """回传结果；回传本身失败只能放弃，父进程会按"子进程没给结果"处理。"""
    try:
        conn.send(obj)
    except BaseException:  # noqa: BLE001
        pass


def exit_note(exitcode: int | None) -> str:
    """把退出码翻译成可读说明（供错误信息与诊断使用）。"""
    if exitcode is None:
        return "仍在运行"
    if exitcode == 0:
        return "正常退出"
    if exitcode < 0:
        return f"被信号 {-exitcode} 终止（很可能是段错误或被系统因内存不足杀掉）"
    return f"退出码 {exitcode}"


def _kill_tree(proc: multiprocessing.process.BaseProcess) -> None:
    """杀掉子进程**及其全部后代**，然后回收。

    只 terminate 会留下孤儿：.doc 解析会再起 antiword，孤儿进程继续占内存，
    正是本机曾经把物理内存耗到无法响应的那类故障。
    """
    pid = proc.pid
    if pid is None:  # pragma: no cover - 未成功启动
        return
    if os.name == "posix":
        import signal  # noqa: PLC0415

        try:
            pgid = os.getpgid(pid)
            if pgid != pid:
                # 子进程还没来得及 setsid；此时 killpg 会杀到父服务所在的进程组。
                # 只杀直接子进程最安全，且它尚未进入目标函数，不可能已经创建孙进程。
                proc.kill()
            else:
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            # 取不到进程组（子进程还没执行 setsid 就被杀）时退回单进程终止。
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    else:
        import subprocess  # noqa: PLC0415

        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=_REAP_GRACE_S,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            pass
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    proc.join(_REAP_GRACE_S)


def _recv_with_deadline(
    conn, timeout_s: float
) -> tuple[str, object, threading.Thread]:
    """带截止时间地收结果，返回 (结局, 值)，结局取值见 ``_RECV_*``。

    用一个读取线程而不是 ``conn.poll(timeout)`` + ``recv()``：poll 只保证"有数据可读"，
    若子进程发出半个对象后卡住，随后的 recv 会无限期阻塞。线程 join 超时能同时覆盖
    "解析卡住"和"回传卡住"。

    必须区分"通道被关闭"和"还没到点"：子进程崩溃时 recv 抛 EOFError 并立刻返回，
    若把它也算成超时，就会把崩溃误报成超时，运维会去调一个根本没到期的超时值。
    """
    holder: dict[str, object] = {}
    finished = threading.Event()

    def _reader() -> None:
        try:
            holder["value"] = conn.recv()
        except BaseException as exc:  # noqa: BLE001 - EOFError / OSError 都算没拿到结果
            holder["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(target=_reader, name="parse-recv", daemon=True)
    thread.start()
    if not finished.wait(timeout_s):
        return _RECV_TIMEOUT, None, thread
    thread.join()
    if "value" in holder:
        return _RECV_OK, holder["value"], thread
    return _RECV_CLOSED, None, thread


def _run_isolated(
    target, args: tuple, timeout_s: float, name: str = "parse"
) -> IsolatedOutcome:
    """在隔离子进程里执行 ``target(*args)`` 并回传结果。

    只负责"隔离执行"这一件事，不掺解析语义——这样超时、崩溃、内存上限三条路径都能
    单独验证，而不必先造出一个真的会让 pypdf 崩掉的文件。

    ``target`` 必须可按引用 pickle（即模块级函数），这是 multiprocessing spawn 的要求。
    它的第一个参数固定是回传用的 ``Connection``。
    """
    parent_conn = child_conn = proc = None
    try:
        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_isolated_entry,
            args=(target, child_conn, args),
            name=name,
            daemon=True,
        )
        proc.start()
    except Exception as exc:  # noqa: BLE001 - 启动失败统一转为明确的隔离不可用
        if proc is not None and proc.is_alive():
            _kill_tree(proc)
        for conn in (parent_conn, child_conn):
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
        return IsolatedOutcome(
            got=False, reason="unavailable", note=f"{exc.__class__.__name__}: {exc}"
        )

    # 父进程必须关掉自己这一端的子连接，否则 recv 永远等不到 EOF。
    try:
        child_conn.close()
    except OSError:  # pragma: no cover
        pass

    close_parent = True
    try:
        state, value, reader = _recv_with_deadline(parent_conn, timeout_s)
        if state == _RECV_TIMEOUT:
            # 先杀进程树，再（在 finally 里）关连接：子进程写端关闭后，仍阻塞在 recv 上的
            # 读取线程会立刻拿到 EOF 自然退出。顺序反了就是在一个线程仍阻塞于该句柄时
            # 把它关掉——POSIX 上被关闭的文件描述符号可能马上被复用，阻塞中的读就有读到
            # 无关数据的风险。
            _kill_tree(proc)
            reader.join(_REAP_GRACE_S)
            # 极端情况下读取线程仍未退出，就把连接留给它持有；主动 close 会重新引入
            # “另一线程仍在 recv 时关闭并复用文件描述符”的竞态。
            close_parent = not reader.is_alive()
    finally:
        if close_parent:
            try:
                parent_conn.close()
            except OSError:  # pragma: no cover
                pass

    if state == _RECV_OK:
        # 正常拿到结果；子进程此时可能仍在退出过程中，join 收尾避免僵尸。
        proc.join(_NORMAL_EXIT_GRACE_S)
        if proc.is_alive():
            _kill_tree(proc)
        return IsolatedOutcome(got=True, result=value, exit_note=exit_note(proc.exitcode))

    if state == _RECV_CLOSED:
        # 必须 join 之后再读退出码，否则拿到的还是 None（子进程尚未被回收）。
        proc.join(_NORMAL_EXIT_GRACE_S)
        if proc.is_alive():
            _kill_tree(proc)
        note = exit_note(proc.exitcode)
        return IsolatedOutcome(got=False, reason="crashed", note=note, exit_note=note)

    _kill_tree(proc)  # 内部已 join
    return IsolatedOutcome(
        got=False,
        reason="timeout",
        note=f"超过 {timeout_s:g} 秒未返回，已终止该进程树",
        exit_note=exit_note(proc.exitcode),
    )


def parse_units(kind: str, path: Path) -> list[parsing.Unit]:
    """解析文档并返回文本单元；与 ``parsing.PARSERS[kind](path)`` 等价但受隔离保护。

    只有显式关闭隔离时才在当前进程解析。隔离已开启却无法创建子进程时必须失败关闭；
    若退回当前进程，同一份不可信文件就可能重新拖垮整个服务。
    """
    parser = parsing.PARSERS.get(kind)
    if parser is None:
        raise parsing.ParseError(f"不支持的解析类型 {kind!r}", code="unsupported_kind")

    if not settings.parse_isolation:
        return parser(path)

    timeout_s = settings.parse_timeout_s
    outcome = _run_isolated(
        _child_main,
        (kind, str(path), settings.parse_memory_mb),
        timeout_s,
        name=f"parse-{kind}",
    )

    if not outcome.got:
        if outcome.reason == "unavailable":
            raise parsing.ParseError(
                "[parse_isolation_unavailable] 无法启动文档解析隔离进程。"
                "为避免不可信文件在主服务内解析，本次操作已拒绝；请检查系统进程/句柄"
                f"资源与运行权限（{outcome.note}）。",
                code="parse_isolation_unavailable",
            )
        if outcome.reason == "timeout":
            raise parsing.ParseError(
                f"[parse_timeout] 解析超过 {timeout_s:g} 秒仍未完成，已终止该进程树。"
                "文件可能损坏或构造异常；请检查该文件，或调大 RAG_PARSE_TIMEOUT_S。",
                code="parse_timeout",
            )
        # 通道关闭：子进程没留下任何说明就退出了。段错误、被系统 OOM 杀掉都走这里，
        # 与"解析器自己报错"（parse_failed）必须分开，否则运维会去找一个不存在的崩溃。
        raise parsing.ParseError(
            f"[parse_crashed] 解析进程异常退出（{outcome.note}）。文件可能损坏或格式不兼容；"
            "主服务进程仍在运行。若宿主机同时出现内存紧张，请先检查系统资源再重试。",
            code="parse_crashed",
        )

    status, detail = outcome.result  # type: ignore[misc]
    if status == _OK:
        return [
            parsing.Unit(text=text, page=page, paragraph=paragraph)
            for text, page, paragraph in detail  # type: ignore[union-attr]
        ]
    if status == _PARSE_ERROR:
        message, code = detail  # type: ignore[misc]
        raise parsing.ParseError(message, code=code)
    if status == _ISOLATION_ERROR:
        raise parsing.ParseError(
            "[parse_isolation_unavailable] 文档解析隔离环境初始化失败："
            f"{detail}。本次操作已拒绝，未退回主服务解析。",
            code="parse_isolation_unavailable",
        )
    if status == _MEMORY:
        limit = f"{detail} MB" if detail else "配置的上限"
        raise parsing.ParseError(
            f"[parse_memory] 解析该文件时内存超过上限（{limit}），已终止。"
            "文件可能损坏或为构造的超大解压文件；调大 RAG_PARSE_MEMORY_MB 前，"
            "请先确认服务器内存足够，且不会影响正在服务的其他用户。",
            code="parse_memory",
        )
    raise parsing.ParseError(
        f"[parse_failed] 解析该文件时出错（{detail}）。文件很可能损坏或格式不兼容；"
        "主服务进程仍在运行。若确认文件正常，请把该文件与这条信息"
        "一并反馈，以便定位是哪个解析组件的问题。",
        code="parse_failed",
    )
