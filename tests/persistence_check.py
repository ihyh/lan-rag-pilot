"""应用重启后的持久化检查，由 smoke_runner 在重启后调用。

与旧版的区别：旧版只断言“接口还返回 200、列表非空”，因此即使切片表和向量
全部丢失、检索彻底失效，它依然会打印 PASS。本版改为三层核对：

1. SQLite 层：逐项比对重启前的 baseline.json（切片数、非空向量数等）；
2. 业务层：重启后真正发起一次问答，并要求命中的切片能在库里查到；
3. 运行参数：重启后 top_k 仍生效，且 settings 表的值与重启前一致。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx

BASE_URL = os.environ.get("RAG_SMOKE_URL", "http://127.0.0.1:8090")
PASSWORD = os.environ.get("RAG_ROOT_PASSWORD", "fcd123")
DB_PATH = os.environ.get("RAG_DB_PATH", "")
BASELINE_PATH = Path(
    os.environ.get("RAG_SMOKE_BASELINE")
    or (Path(os.environ.get("RAG_DATA_DIR", ".")) / "baseline.json")
)

PASS: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def _counts() -> dict[str, int]:
    with sqlite3.connect(DB_PATH) as db:
        return {
            key: db.execute(sql).fetchone()[0]
            for key, sql in (
                ("users", "SELECT COUNT(*) FROM users"),
                ("documents_ready", "SELECT COUNT(*) FROM documents WHERE status='ready'"),
                ("chunks", "SELECT COUNT(*) FROM chunks"),
                ("chunks_with_vector", "SELECT COUNT(*) FROM chunks WHERE length(vector) > 0"),
                ("conversations", "SELECT COUNT(*) FROM conversations"),
                ("chats", "SELECT COUNT(*) FROM chats"),
                ("chat_sources", "SELECT COUNT(*) FROM chat_sources"),
            )
        }


def _chunk_document(chunk_id: int) -> int | None:
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT document_id FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    return int(row[0]) if row else None


def main() -> None:
    if not DB_PATH:
        print("[FAIL] 缺少 RAG_DB_PATH，无法核对 SQLite 层")
        sys.exit(1)
    if not BASELINE_PATH.is_file():
        print(f"[FAIL] 缺少重启基线 {BASELINE_PATH}，无法判断数据是否真的留存")
        sys.exit(1)
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    expected_counts = baseline.get("counts", {})
    expected_settings = baseline.get("settings", {})
    probe = baseline.get("probe", {})

    # ---------- 1. SQLite 层：必须与重启前完全一致 ----------
    print("\n== 重启后 SQLite 层 ==")
    actual_counts = _counts()
    for key, expected in expected_counts.items():
        actual = actual_counts.get(key)
        check(
            actual == expected,
            f"重启后 {key} 保持不变（重启前 {expected}，现在 {actual}）",
        )
    check(
        actual_counts.get("chunks", 0) > 0,
        f"重启后仍有切片（实际 {actual_counts.get('chunks')} 条）",
    )
    check(
        actual_counts.get("chunks_with_vector", 0) == actual_counts.get("chunks", -1),
        "每个切片都保留了非空向量"
        f"（切片 {actual_counts.get('chunks')}，含向量 {actual_counts.get('chunks_with_vector')}）",
    )

    with httpx.Client(base_url=BASE_URL, timeout=60.0) as client:
        login = client.post("/api/login", json={"username": "root", "password": PASSWORD})
        check(login.status_code == 200, f"重启后可登录（HTTP {login.status_code}）")

        # ---------- 2. 运行参数：重启后仍生效 ----------
        print("\n== 重启后运行参数 ==")
        settings_resp = client.get("/api/admin/settings")
        check(settings_resp.status_code == 200, "可读取运行参数")
        current = (
            settings_resp.json().get("settings", {}) if settings_resp.status_code == 200 else {}
        )
        for key, expected in expected_settings.items():
            check(
                current.get(key) == expected,
                f"重启后 {key} 仍为 {expected}（实际 {current.get(key)}）",
            )

        # ---------- 3. 业务层：检索必须真的还能工作 ----------
        print("\n== 重启后检索与引用 ==")
        question = probe.get("probe_question") or "住宿标准是多少"
        resp = client.post("/api/query", json={"question": question})
        check(resp.status_code == 200, f"重启后可完成问答（HTTP {resp.status_code}）")
        body = resp.json() if resp.status_code == 200 else {}
        sources = body.get("sources", [])
        check(len(sources) > 0, f"重启后问答仍有引用来源（实际 {len(sources)} 条）")
        if sources:
            missing = [
                s.get("chunk_id") for s in sources if _chunk_document(s.get("chunk_id")) is None
            ]
            check(not missing, f"每个引用都能在切片表中查到（缺失 {missing}）")
            mismatched = [
                s.get("chunk_id")
                for s in sources
                if _chunk_document(s.get("chunk_id")) not in (None, s.get("document_id"))
            ]
            check(not mismatched, f"引用的 document_id 与切片一致（不一致 {mismatched}）")

        # 重启前的探针对话仍可回读
        prior_conversation = probe.get("conversation_id")
        if prior_conversation:
            r = client.get(f"/api/conversations/{prior_conversation}")
            check(r.status_code == 200, f"重启前对话 #{prior_conversation} 仍可读取")
            if r.status_code == 200:
                turns = r.json().get("turns", [])
                check(len(turns) > 0, f"重启前对话仍保留轮次（实际 {len(turns)} 轮）")

        # ---------- 4. 运行参数在重启后依然驱动检索 ----------
        print("\n== 重启后 top_k 生效性 ==")
        restore_top_k = expected_settings.get("top_k", 4)
        r = client.patch("/api/admin/settings", json={"top_k": 1})
        check(r.status_code == 200, "重启后仍可修改运行参数")
        resp = client.post("/api/query", json={"question": question})
        one_k = len(resp.json().get("sources", [])) if resp.status_code == 200 else -1
        check(one_k == 1, f"重启后 top_k=1 仍然只返回 1 条来源（实际 {one_k}）")
        client.patch("/api/admin/settings", json={"top_k": restore_top_k})

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
