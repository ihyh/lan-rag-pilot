"""应用重启后的最小持久化检查，由 smoke_runner.ps1 调用。"""
from __future__ import annotations

import os
import sys

import httpx


def main() -> None:
    base_url = os.environ.get("RAG_SMOKE_URL", "http://127.0.0.1:8090")
    password = os.environ.get("RAG_ROOT_PASSWORD", "fcd123")
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        login = client.post("/api/login", json={"username": "root", "password": password})
        docs = client.get("/api/admin/documents")
        chats = client.get("/api/admin/chats")
        conversations = client.get("/api/admin/conversations")
    ok = (
        login.status_code == 200
        and docs.status_code == 200
        and len(docs.json().get("items", [])) >= 1
        and chats.status_code == 200
        and len(chats.json().get("items", [])) >= 1
        and conversations.status_code == 200
        and len(conversations.json().get("items", [])) >= 1
    )
    print(
        "[PASS] 重启后账号、文档、切片和问答历史仍可访问"
        if ok else
        f"[FAIL] 持久化检查失败 login={login.status_code} docs={docs.status_code} "
        f"chats={chats.status_code} conversations={conversations.status_code}"
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
