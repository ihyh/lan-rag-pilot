"""解析隔离的行为测试：坏文件只应让**这一次解析**失败，不能拖垮主进程。

单 worker 是硬约束，所以"解析把内存吃满/卡死/段错误"就等于全站中断。这里逐条验证
子进程隔离真的兜住了：超时、崩溃、内存上限、异常、以及 ParseError 的原样回传。

注意：multiprocessing 用 spawn，子进程会**重新导入本模块**，因此：
- 所有模块级代码必须是可安全重复执行的（配置只读环境变量，不起服务）；
- 测试主体必须放在 ``if __name__ == "__main__"`` 里；
- 子进程目标函数必须是模块级函数（spawn 按引用 pickle）。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("RAG_SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("RAG_ROOT_PASSWORD", "test-password")
os.environ.setdefault("DEEPSEEK_API_KEY", "ollama")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://127.0.0.1:11434/v1")
os.environ.setdefault("RAG_EMBED_BACKEND", "mock")

from app import parse_runner  # noqa: E402
from app.config import settings  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def skip(msg: str) -> None:
    SKIP.append(msg)
    print(f"  [SKIP] {msg}")


def report(msg: str) -> None:
    print(f"  [INFO] {msg}")


# ---------------- 子进程目标函数（必须是模块级，spawn 按引用 pickle） ----------------


def child_hang(conn) -> None:
    """永不返回，模拟解析卡死。"""
    time.sleep(3600)


def child_hang_after_partial_send(conn) -> None:
    """先发半个 pickle frame 再卡死。

    只在 POSIX 上有意义：那里的管道是字节流，poll() 会在收到半个对象时就返回 True，
    随后的 recv() 会无限期等剩下的字节——这正是不能只用 poll+recv 的原因。
    Windows 的管道是消息模式，一次 send 就是一条完整消息，不存在"半个消息"状态。
    """
    conn.send_bytes(b"\x80\x05")  # 声明协议 5，但没有 STOP 结束符
    time.sleep(3600)


def child_send_garbage(conn) -> None:
    """发一条读不出来的消息，模拟子进程回传内容损坏。"""
    conn.send_bytes(b"\xff\xff\xff\xff")


def child_hard_crash(conn) -> None:
    """直接终止进程，不发任何消息，也不给解释器清理机会。"""
    os._exit(3)


def child_segfault(conn) -> None:
    """制造真正的段错误：即使这样也只应表现为一次解析失败。"""
    ctypes.string_at(0)


def child_raise_unexpected(conn) -> None:
    raise RuntimeError("内部错误示例")


def child_allocate_over_cap(conn, mem_mb: int) -> None:
    """尝试分配远超上限的内存，用来验证内存上限真的生效。"""
    try:
        parse_runner._limit_address_space(mem_mb)
        block = bytearray(mem_mb * 1024 * 1024 * 3)
        block[0] = 1  # 触发实际提交，避免只保留地址空间
        conn.send(("allocated", len(block)))
    except MemoryError:
        conn.send(("memory", mem_mb))


def child_report_ok(conn, value) -> None:
    conn.send(("echo", value))


def child_report_process_group(conn) -> None:
    conn.send(("group", os.getpid(), os.getpgrp() if os.name == "posix" else None))


def child_close_then_hang(conn) -> None:
    conn.close()
    time.sleep(3600)


def child_spawn_grandchild(conn, marker: str) -> None:
    """起一个带唯一标记的孙进程再卡死，用来验证超时会杀掉整棵进程树。

    标记是必需的：只数"子进程个数"会把别的残留混进来，断言等于没断言。
    """
    import subprocess

    subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(3600)  # {marker}"])
    time.sleep(3600)


def _pids_matching(marker: str) -> list[int] | None:
    """命令行里含 marker 的进程 PID；无法枚举时返回 None（调用方据此跳过而不是假绿）。

    标记必须经**环境变量**传给枚举命令，不能直接拼进命令行：否则执行枚举的进程
    自己的命令行里就含这个标记，会把自己也数进去，得到一个永远不为 0 的假结果。
    """
    env = dict(os.environ)
    env["RAG_ISOLATION_MARK"] = marker
    if os.name == "nt":
        import subprocess

        script = (
            "$m = $env:RAG_ISOLATION_MARK; "
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.CommandLine -and $_.CommandLine.Contains($m) } | "
            "ForEach-Object { $_.ProcessId }"
        )
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=90,
                env=env,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        out = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                out.append(int(line))
        return out
    import subprocess

    try:
        # pgrep 默认排除自身；这里不经 shell 调用，因此没有额外的包装进程会自匹配。
        proc = subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True, timeout=60, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [int(x) for x in (proc.stdout or "").split() if x.strip().isdigit()]


def _kill_pids(pids: list[int]) -> None:
    import subprocess

    for pid in pids:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass


# 本文件会刻意制造孤儿进程来验证"超时会杀掉整棵进程树"。产品一旦有缺陷，这些孤儿就会
# 真的活下来，而它们继承了父进程的管道句柄——那会让 Python 退出时等待 resource_tracker
# 而整个卡死（实测发生）。所以测试结束时必须自己清场，无论产品那边成不成功。
CREATED_MARKERS: list[str] = []


def cleanup_created_markers() -> None:
    for marker in CREATED_MARKERS:
        pids = _pids_matching(marker)
        if pids:
            _kill_pids(pids)


def main() -> None:
    from app import parsing

    tmp = Path(os.environ.get("TEMP") or "/tmp") / "rag-parse-isolation-check"
    tmp.mkdir(parents=True, exist_ok=True)
    txt = tmp / "ok.txt"
    txt.write_text("服务窗口为 09:00 至 17:00。\n\n故障代码 E123 表示风扇异常。\n", encoding="utf-8")
    empty = tmp / "empty.txt"
    empty.write_text("", encoding="utf-8")

    # ---------- A. 正常路径必须与不隔离时完全一致 ----------
    print("\n== 正常解析：隔离前后结果必须一致 ==")
    settings.parse_isolation = True
    isolated = parse_runner.parse_units("txt", txt)
    settings.parse_isolation = False
    inproc = parse_runner.parse_units("txt", txt)
    settings.parse_isolation = True
    check(len(isolated) == len(inproc) and len(isolated) > 0, f"单元数一致（{len(isolated)}）")
    check(
        [(u.text, u.page, u.paragraph) for u in isolated]
        == [(u.text, u.page, u.paragraph) for u in inproc],
        "文本、页码、段落号逐项一致",
    )
    check(isolated[0].paragraph == 1 and isolated[1].paragraph == 2, "段落号从 1 递增")

    # ---------- B. 解析器自己的错误必须原样回传 ----------
    print("\n== 解析器报错：信息与 code 原样回传，不能包装成「服务器故障」 ==")
    settings.parse_isolation = False
    try:
        parse_runner.parse_units("txt", empty)
        check(False, "空文件在非隔离模式下应报错")
        inproc_code = inproc_message = None
    except parsing.ParseError as exc:
        inproc_code, inproc_message = exc.code, exc.message
        check(True, f"非隔离模式报错：{exc.message[:40]}（code={exc.code}）")
    settings.parse_isolation = True
    try:
        parse_runner.parse_units("txt", empty)
        check(False, "空文件在隔离模式下应报错")
    except parsing.ParseError as exc:
        check(
            exc.code == inproc_code,
            f"隔离模式下 code 与不隔离时一致（{exc.code}）",
        )
        check(
            exc.message == inproc_message,
            f"隔离模式下 message 也逐字一致（{exc.message[:30]}…）",
        )
        check("解析进程异常退出" not in exc.message, "没有被误包装成进程崩溃")
        check("解析该文件时出错" not in exc.message, "也没有被误包装成解析组件故障")

    # ---------- C. 超时：必须终止并报明确错误 ----------
    print("\n== 卡死的解析必须被超时终止，而不是永久占住线程 ==")
    saved_timeout = settings.parse_timeout_s
    settings.parse_timeout_s = 3.0
    started = time.monotonic()
    outcome = parse_runner._run_isolated(child_hang, (), 3.0, name="test-hang")
    elapsed = time.monotonic() - started
    check(not outcome.got, "没有拿到结果")
    check(outcome.reason == "timeout", f"结局标记为 timeout（实际 {outcome.reason}）")
    check(elapsed < 20.0, f"在超时后很快返回（实际 {elapsed:.1f} 秒）")

    # 回传途中阻塞：POSIX 上是真实风险（字节流管道 + poll 只保证"有数据可读"），
    # Windows 的管道是消息模式，不存在"收到半个消息"的状态。
    if os.name == "posix":
        started = time.monotonic()
        outcome = parse_runner._run_isolated(
            child_hang_after_partial_send, (), 3.0, name="test-partial"
        )
        check(
            not outcome.got and outcome.reason == "timeout",
            f"只发半个对象后卡死也被判超时（实际 {outcome.reason}）",
        )
        check(time.monotonic() - started < 20.0, "该场景同样很快返回")
    else:
        skip("半个对象的回传阻塞仅 POSIX（Windows 管道为消息模式，不存在该状态）")

    # 回传内容损坏不能变成"静默成功"或永久挂起
    outcome = parse_runner._run_isolated(
        child_send_garbage, (), 10.0, name="test-garbage"
    )
    check(not outcome.got, "损坏的回传不会被当成结果")
    check(outcome.reason == "crashed", f"归为失败而不是挂起（实际 {outcome.reason}）")
    settings.parse_timeout_s = saved_timeout

    # ---------- D. 崩溃：必须与超时区分开 ----------
    print("\n== 进程崩溃必须报「崩溃」，不能报成超时（否则会去调一个没到期的超时值）==")
    outcome = parse_runner._run_isolated(child_hard_crash, (), 30.0, name="test-crash")
    check(not outcome.got, "没有拿到结果")
    check(outcome.reason == "crashed", f"结局标记为 crashed（实际 {outcome.reason}）")
    check("退出码 3" in outcome.note, f"说明里带退出码：{outcome.note}")

    started = time.monotonic()
    outcome = parse_runner._run_isolated(
        child_close_then_hang, (), 30.0, name="test-close-then-hang"
    )
    check(not outcome.got and outcome.reason == "crashed", "关闭通道后卡死的进程会被回收")
    check(time.monotonic() - started < 20.0, "该异常不会一直留在后台")

    if os.name == "posix":
        outcome = parse_runner._run_isolated(child_segfault, (), 30.0, name="test-segv")
        check(not outcome.got and outcome.reason == "crashed", "段错误同样归为 crashed")
        check("信号" in outcome.note, f"说明里指明是被信号终止：{outcome.note}")
    else:
        skip("段错误注入仅 POSIX（Windows 上 ctypes.string_at(0) 行为不同）")

    # ---------- E. 解析库抛出的意外异常 ----------
    # 分两层验证，因为它们是两件事：
    # E1 直接跑一个没有包装的目标函数 → 它没留下任何说明就死了，应归为 crashed；
    # E2 走真实的 parse_units（即子进程里的 _child_main 包装）→ 应回传成可读错误。
    print("\n== 未包装的目标崩溃：必须归为 crashed，而不是当成超时 ==")
    settings.parse_timeout_s = 30.0
    outcome = parse_runner._run_isolated(
        child_raise_unexpected, (), 30.0, name="test-raise"
    )
    check(not outcome.got, "没有拿到结果")
    check(outcome.reason == "crashed", f"归为 crashed（实际 {outcome.reason}）")
    check("退出码 1" in outcome.note, f"说明里带退出码：{outcome.note}")
    settings.parse_timeout_s = saved_timeout

    print("\n== 真实畸形文件：解析库抛意外异常时必须变成可读错误 ==")
    # 声明成 .docx 但不是有效 OOXML 包：python-docx 会抛 PackageNotFoundError，
    # 而 parse_docx 并不捕获它——正好用来验证 _child_main 的兜底与主进程存活。
    bad_docx = tmp / "broken.docx"
    bad_docx.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    settings.parse_isolation = True
    try:
        parse_runner.parse_units("docx", bad_docx)
        check(False, "畸形 docx 应报错")
    except parsing.ParseError as exc:
        check(exc.code == "parse_failed", f"归为 parse_failed 而不是进程崩溃（{exc.code}）")
        check(
            "解析该文件时出错" in exc.message and "PackageNotFoundError" in exc.message,
            f"信息里有可定位的异常类型：{exc.message[:70]}…",
        )
        check("主服务进程仍在运行" in exc.message, "信息准确说明主服务进程仍在运行")

    # ---------- F. 结果回传保真 ----------
    print("\n== 结果回传必须保真（中文、空值、大对象）==")
    payload = {"文本": "故障代码 E123", "page": None, "paragraph": 7, "列表": [1, 2, 3]}
    outcome = parse_runner._run_isolated(
        child_report_ok, (payload,), 30.0, name="test-echo"
    )
    check(outcome.got and outcome.result == ("echo", payload), "复杂对象原样回传")
    check(outcome.result[1]["文本"] == "故障代码 E123", "中文未损坏")

    big = ["x" * 1000] * 2000  # 约 2MB
    outcome = parse_runner._run_isolated(
        child_report_ok, (big,), 60.0, name="test-big"
    )
    check(outcome.got and outcome.result[1] == big, "约 2MB 的回传完整无损")

    if os.name == "posix":
        outcome = parse_runner._run_isolated(
            child_report_process_group, (), 30.0, name="test-process-group"
        )
        _, child_pid, child_pgid = outcome.result
        check(child_pid == child_pgid, "隔离目标运行前已建立独立 POSIX 进程组")
    else:
        skip("独立进程组断言仅 POSIX；Windows 使用 taskkill /T")

    # ---------- G. 内存上限 ----------
    print("\n== 内存上限：超限必须是干净的解析失败，不是把机器拖垮 ==")
    if os.name == "posix":
        outcome = parse_runner._run_isolated(
            child_allocate_over_cap, (256,), 60.0, name="test-mem"
        )
        check(outcome.got, "子进程回传了结果（没有静默死掉）")
        check(
            outcome.got and outcome.result[0] == "memory",
            f"分配 768MB 而上限 256MB 时被拦下：{outcome.result if outcome.got else outcome.note}",
        )
    else:
        skip(
            "内存上限注入仅 POSIX：RLIMIT_AS 无 Windows 等价物，"
            "Windows 上只有超时与崩溃隔离生效（已在文档中说明）"
        )

    # ---------- H. 整棵进程树都要被杀掉 ----------
    print("\n== 超时必须杀掉整棵进程树，不能留下孤儿占内存 ==")
    # 孤儿进程是本机真实发生过的事故：孤儿 llama-server 把物理内存吃到 1.4GB，
    # 整个应用被换页到无法响应。所以这里用唯一标记精确断言孙进程确实没了，
    # 而不是只数"子进程个数"（那会把无关残留混进来，等于没断言）。
    marker = f"RAG_ORPHAN_{os.getpid()}_{int(time.time())}"
    CREATED_MARKERS.append(marker)
    outcome = parse_runner._run_isolated(
        child_spawn_grandchild, (marker,), 6.0, name="test-tree"
    )
    check(not outcome.got and outcome.reason == "timeout", "带孙进程的卡死被判超时")

    pids = _pids_matching(marker)
    if pids is None:
        skip("无法枚举进程（本平台不支持），孤儿断言未执行")
    else:
        # 先证明标记确实能用：孙进程在超时前是存在的，否则下面的 0 说明不了任何事。
        survive_marker = f"RAG_CONTROL_{os.getpid()}_{int(time.time())}"
        CREATED_MARKERS.append(survive_marker)
        import subprocess

        probe = subprocess.Popen(
            [sys.executable, "-c", f"import time; time.sleep(30)  # {survive_marker}"]
        )
        try:
            detected = []
            for _ in range(20):
                time.sleep(0.5)
                detected = _pids_matching(survive_marker) or []
                if detected:
                    break
            check(
                len(detected) >= 1,
                f"标记法本身有效：能枚举到测试进程（{len(detected)} 个）",
            )
        finally:
            probe.kill()
            probe.wait(timeout=30)

        thead = _pids_matching(marker) or []
        check(len(thead) == 0, f"孙进程已被一并杀掉，没有孤儿残留（实际 {len(thead)} 个）")
        if thead:
            report(f"孤儿 PID：{thead}")

    # ---------- I. 主进程必须还活着、还能继续干活 ----------
    print("\n== 以上折腾之后，主进程必须照常工作 ==")
    check(True, f"主进程仍在运行（pid={os.getpid()}）")
    settings.parse_isolation = True
    again = parse_runner.parse_units("txt", txt)
    check(len(again) == len(isolated), "崩溃/超时之后仍能正常解析")
    settings.parse_isolation = False
    check(len(parse_runner.parse_units("txt", txt)) == len(isolated), "退回非隔离模式也能解析")
    settings.parse_isolation = True

    # ---------- J. 未知类型与配置校验 ----------
    print("\n== 边界：未知解析类型、非法配置 ==")
    try:
        parse_runner.parse_units("exe", txt)
        check(False, "未知解析类型应报错")
    except parsing.ParseError as exc:
        check(exc.code == "unsupported_kind", f"未知类型报 unsupported_kind（{exc.code}）")

    original_run_isolated = parse_runner._run_isolated
    parse_runner._run_isolated = lambda *args, **kwargs: parse_runner.IsolatedOutcome(
        got=False, reason="unavailable", note="test start failure"
    )
    try:
        try:
            parse_runner.parse_units("txt", txt)
            check(False, "隔离启动失败时不得退回主进程解析")
        except parsing.ParseError as exc:
            check(
                exc.code == "parse_isolation_unavailable",
                f"隔离启动失败时 fail-closed（实际 {exc.code}）",
            )
            check("test start failure" in exc.message, "错误信息保留可操作的启动失败原因")
    finally:
        parse_runner._run_isolated = original_run_isolated

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败" + (f", {len(SKIP)} 跳过" if SKIP else ""))
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.stdout.flush()
        return 1
    return 0


if __name__ == "__main__":
    # 清场必须无条件执行：本测试会故意制造孤儿进程，产品一旦有缺陷它们就会真的活下来，
    # 并因继承了管道句柄而让解释器退出时挂住（实测发生过，整个注入验证被卡死）。
    # 先 flush 再返回退出码，避免清场过程中的任何异常盖掉测试结果。
    try:
        code = main()
    finally:
        try:
            cleanup_created_markers()
        except BaseException:  # noqa: BLE001
            pass
        sys.stdout.flush()
        sys.stderr.flush()
    sys.exit(code)
