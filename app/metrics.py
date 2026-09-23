"""性能统计：把问答链路的耗时拆开，让"为什么慢"能一眼看出来。

## 为什么需要

``chats`` 表一直只记录**模型调用**的耗时（``latency_ms``），不含检索段。于是一旦有人
问"为什么很慢"，只能靠人工取证：查进程内存、``ollama ps``、手工 curl 测吞吐——上一轮
排查孤儿进程把内存吃满的事故就是这么做的，花了很多时间。

这里把耗时按阶段记录并在管理概览里给出统计，目的是把那次调查压缩成一眼可判：

- **检索耗时**（``retrieval_ms``）：从构造检索问题到选出最终来源。它只依赖本机嵌入模型
  与内存索引，正常情况下是几十到几百毫秒。
- **模型耗时**（``latency_ms``）：模型调用本身。纯 CPU 机器上它远大于检索，且**包含模型
  冷加载**（闲置卸载后再问，实测多约 25 秒）。

对照同一次问答：检索几百毫秒而模型调用几十秒，应优先检查生成模型；检索也到秒级，
应检查索引或嵌入。两段均不包含并发排队和完整 HTTP 请求时间。

## 口径（刻意写明，避免误读）

- 模型耗时只统计 **``status='ok' AND latency_ms IS NOT NULL``** 的轮次。
  拒答（知识库为空、无匹配、无权访问）也会写入 ``chats`` 且 ``status='ok'``，但它没有
  模型调用、``latency_ms`` 为 NULL；若按 ``status='ok'`` 取样本，等于把无耗时的行混进
  统计，百分位会失去意义。
- 拒答与失败**单独计数**，因为它们反映的是完全不同的问题：拒答多说明语料或阈值需要
  调整，失败多说明链路有问题。
- 两段耗时的样本口径**刻意不同**：``model_ms`` 只统计成功作答的轮次——失败轮次的耗时
  没有可比性，混进去会把"一次断链"误读成"系统变慢"；而 ``retrieval_ms`` 统计所有发生
  过检索的轮次（含随后生成失败的），因为检索成本与后续是否失败无关，它反映的是索引与
  嵌入的健康度。两段都各自带 ``samples``，便于看清样本量差异。
- 百分位用**最近秩**（nearest-rank）：升序排列后取第 ``ceil(p/100 * n)`` 个。样本量小
  时它比插值法更保守，也不会在 n=1 时产生"插值出来的"中间值。
- 统计窗口是**最近 N 轮**而不是固定时长：这样查询成本与数据库大小无关，也不会因为长
  期没人提问而返回空统计。
"""
from __future__ import annotations

import math

# 概览默认统计的轮次上限。取有界值是有意的：查询成本不随数据库增长而增长。
DEFAULT_WINDOW = 500


def percentile(values: list[int], p: float) -> int | None:
    """最近秩百分位。``values`` 可为未排序序列；空序列返回 None。

    p=0 返回最小值，p=100 返回最大值；其余按 ``ceil(p/100 * n)`` 取第几个（1 起始）。
    """
    if not values:
        return None
    if not math.isfinite(p):
        raise ValueError(f"百分位必须是有限数：{p!r}")
    if p < 0 or p > 100:
        raise ValueError(f"百分位必须在 0~100 之间：{p!r}")
    ordered = sorted(values)
    if p == 0:
        return ordered[0]
    # ceil 而非 round：样本量小时宁可取更保守（更大）的那个观测值。
    index = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(index, len(ordered)) - 1]


def _series(values: list[int]) -> dict | None:
    if not values:
        return None
    return {
        "samples": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values),
    }


def latency_summary(rows: list[dict], window: int = DEFAULT_WINDOW) -> dict:
    """把最近若干轮问答汇总成分阶段统计。

    ``rows`` 需含 ``latency_ms``、``retrieval_ms``、``completion_tokens``、
    ``status``、``created_at``；缺字段按 None 处理（旧数据没有检索耗时）。
    """
    answered: list[int] = []
    retrieval: list[int] = []
    tokens: list[int] = []
    refusals = 0
    errors = 0
    stamps: list[str] = []

    for row in rows:
        status = (row.get("status") or "").strip().lower()
        latency = row.get("latency_ms")
        if status == "error":
            errors += 1
        elif latency is None:
            # 拒答：有记录、没有模型调用，因此没有耗时可统计。
            refusals += 1
        else:
            answered.append(int(latency))
            if row.get("completion_tokens") is not None:
                tokens.append(int(row["completion_tokens"]))
        if row.get("retrieval_ms") is not None:
            retrieval.append(int(row["retrieval_ms"]))
        stamp = row.get("created_at")
        if isinstance(stamp, str) and stamp:
            stamps.append(stamp)

    return {
        "window": window,
        "sample_size": len(answered),
        "refusals": refusals,
        "errors": errors,
        "span": {"from": min(stamps), "to": max(stamps)} if stamps else None,
        "model_ms": _series(answered),
        "retrieval_ms": _series(retrieval),
        "completion_tokens": _series(tokens),
        "note": (
            "模型耗时是成功作答的模型调用时间，包含模型冷加载，但不包含排队和整个 HTTP 请求；"
            "检索耗时覆盖所有发生过检索"
            "的轮次（含随后生成失败的），因为检索成本与后续是否失败无关。两者对比即可"
            "判断这两段的相对耗时。拒答与失败不计入模型耗时，单独计数。"
        ),
    }
