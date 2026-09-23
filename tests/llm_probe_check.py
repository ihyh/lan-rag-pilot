"""生成模型可达性探测的行为测试。

覆盖 /api/ready 是否真的代表"能回答问题"，以及探测判定口径是否会导致误判。

用本机回环起一个可控的假模型服务，不需要外网，也不依赖真实 Ollama。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.llm_health import llm_probe  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []

TARGET_MODEL = "qwen3:1.7b"


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


class FakeModelServer:
    """可切换响应形状的假模型服务，并统计被打了几次。"""

    def __init__(self) -> None:
        self.mode = "ok_openai"
        self.hits = 0
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
                outer.hits += 1
                mode = outer.mode
                if mode == "sleep":
                    time.sleep(3.0)
                    mode = "ok_openai"
                if mode == "unauthorized":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error":"unauthorized"}')
                    return
                if mode == "gateway":
                    # 代理无法转发时的典型作答：HTTP 层"有响应"，但根本没到达模型服务。
                    raw = b'{"error":"bad gateway"}'
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if mode == "garbage":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"not json at all")
                    return
                if mode == "ok_ollama":
                    body = {"models": [{"name": TARGET_MODEL}]}
                elif mode == "other_model":
                    body = {"data": [{"id": "qwen3:4b"}]}
                else:
                    body = {"data": [{"id": TARGET_MODEL}, {"id": "other:1b"}]}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):  # 静音
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def closed_port() -> int:
    """占一个端口再释放，得到一个确定无人监听的端口号。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    saved = (
        settings.deepseek_base_url,
        settings.deepseek_model,
        settings.deepseek_api_key,
        settings.llm_probe_enabled,
        settings.llm_probe_ttl_s,
        settings.llm_probe_timeout_s,
    )

    server = FakeModelServer()
    server.start()

    try:
        settings.deepseek_model = TARGET_MODEL
        # 显式设置 key：探测现在会校验它（缺失即判未就绪），不能依赖运行环境里恰好有。
        settings.deepseek_api_key = "ollama"
        settings.llm_probe_enabled = True
        settings.llm_probe_ttl_s = 0.0
        settings.llm_probe_timeout_s = 5.0

        # ---------- A. 连接失败才是不可用 ----------
        print("\n== 连接不上：必须判为不可用，且给出可操作的说明 ==")
        settings.deepseek_base_url = f"http://127.0.0.1:{closed_port()}"
        llm_probe.reset()
        result = llm_probe.probe()
        check(not result.ok, "端口无监听时判定为不可用")
        check("无法连接" in result.message, f"信息说明是连接问题：{result.message[:60]}…")
        check("DEEPSEEK_BASE_URL" in result.message, "信息里指出该检查哪个配置项")

        # ---------- B. 有 HTTP 响应就算可达 ----------
        print("\n== 只要拿到 HTTP 响应就算可达（避免把可用实例误判为未就绪）==")
        settings.deepseek_base_url = server.base_url
        server.mode = "unauthorized"
        llm_probe.reset()
        result = llm_probe.probe()
        check(result.ok, f"401 也算可达（HTTP 层通、只是接口细节不同）：{result.message[:50]}…")

        server.mode = "garbage"
        llm_probe.reset()
        result = llm_probe.probe()
        check(result.ok, "返回非 JSON 时跳过模型校验但仍算可达")

        # ---------- C. 模型清单校验 ----------
        print("\n== 能解析出模型清单时，必须校验配置的模型存在 ==")
        server.mode = "ok_openai"
        llm_probe.reset()
        result = llm_probe.probe()
        check(result.ok and TARGET_MODEL in result.message, "OpenAI 风格清单里找到目标模型")

        server.mode = "ok_ollama"
        llm_probe.reset()
        result = llm_probe.probe()
        check(result.ok, "Ollama 原生风格清单（models[].name）同样被识别")

        server.mode = "other_model"
        llm_probe.reset()
        result = llm_probe.probe()
        check(not result.ok, "清单里没有配置的模型时判定为不可用")
        check("ollama pull" in result.message, "提示需要先 pull 该模型")
        check("qwen3:4b" in result.message, "信息里列出当前可用的模型，便于排查")

        # 这条专门守住"不做前缀匹配"：qwen3:1.7b 与 qwen3:4b 是不同模型
        check(
            not result.ok,
            "不做前缀匹配：清单里有 qwen3:4b 但配置是 qwen3:1.7b，仍判为不可用",
        )

        # ---------- D. API key 缺失/只有空白 ----------
        # 提问路径在 key 缺失时直接抛 llm_auth，所以探测必须判未就绪——
        # 否则 /api/ready 全绿而每个提问都失败，就是本次要消灭的那类假绿。
        # 同时也不能把这种情况报成"无法连接模型服务"（那是与网络无关的伪故障）。
        print("\n== key 缺失或只有空白：必须判未就绪，且不得报成网络故障 ==")
        from app.llm import model_service_headers  # noqa: PLC0415

        saved_key = settings.deepseek_api_key
        settings.deepseek_api_key = ""
        check(
            "Authorization" not in model_service_headers(content_type=True),
            "空 key 时不构造 Authorization 头（空 Bearer 是非法头）",
        )
        server.mode = "ok_openai"
        llm_probe.reset()
        result = llm_probe.probe()
        check(not result.ok, "空 key 时判定为未就绪（与提问会失败保持一致）")
        check("DEEPSEEK_API_KEY" in result.message, "信息里点明是哪个配置项缺失")
        check(
            "无法连接" not in result.message,
            f"不得把配置缺失报成网络故障：{result.message[:52]}…",
        )

        settings.deepseek_api_key = "   "
        check(
            "Authorization" not in model_service_headers(),
            "只有空白的 key 同样不构造 Authorization 头",
        )
        llm_probe.reset()
        result = llm_probe.probe()
        check(not result.ok, "只有空白的 key 也判为未就绪（与 llm_auth 口径一致）")

        settings.deepseek_api_key = "ollama"
        check(
            model_service_headers().get("Authorization") == "Bearer ollama",
            "配了 key 时照常构造 Authorization 头",
        )
        llm_probe.reset()
        check(llm_probe.probe().ok, "配了 key 且模型存在时判为就绪")
        settings.deepseek_api_key = saved_key

        # ---------- E. 网关错误不算可达 ----------
        print("\n== 网关错误（502/503/504）必须判为不可用 ==")
        server.mode = "gateway"
        llm_probe.reset()
        result = llm_probe.probe()
        check(not result.ok, "代理返回 502 时判定为不可用（不是'链路可达'）")
        check("502" in result.message, "信息里点明具体的网关状态码")
        check("代理" in result.message, "信息里指出代理这一常见原因")

        # ---------- F. 环境代理不得接管模型服务流量 ----------
        # 模型服务是本机/内网依赖，被系统或环境代理接管时通常得到 502，
        # 表现成"模型服务暂时不可用"，排查方向完全错。
        print("\n== 环境代理变量不得接管模型服务流量 ==")
        import os  # noqa: PLC0415

        check(
            settings.llm_trust_env_proxy is False,
            "默认不使用系统/环境代理（RAG_LLM_TRUST_ENV_PROXY 默认 0）",
        )
        proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy")
        saved_proxy = {k: os.environ.get(k) for k in proxy_keys}
        blackhole = f"http://127.0.0.1:{closed_port()}"
        try:
            for k in proxy_keys:
                os.environ[k] = blackhole
            server.mode = "ok_openai"
            llm_probe.reset()
            result = llm_probe.probe()
            check(
                result.ok,
                "设了代理变量后仍直连模型服务（未被送到代理）"
                f"：{result.message[:50]}…",
            )

            settings.deepseek_base_url = f"http://127.0.0.1:{closed_port()}"
            llm_probe.reset()
            result = llm_probe.probe()
            check(
                "无法连接" in result.message,
                "端口无监听时给出连接错误，而不是经代理得到的网关错误"
                f"：{result.message[:50]}…",
            )
        finally:
            for k, v in saved_proxy.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        # ---------- G. TTL 缓存 ----------
        print("\n== 结果带 TTL 缓存（健康检查每 30 秒一次，不该每次都打模型服务）==")
        # 本段自带前置状态，不依赖前面段落残留的 base_url。
        server.mode = "ok_openai"
        settings.deepseek_base_url = server.base_url
        settings.llm_probe_ttl_s = 60.0
        llm_probe.reset()
        before = server.hits
        first = llm_probe.probe()
        second = llm_probe.probe()
        check(server.hits - before == 1, f"两次探测只真正请求一次（实际 {server.hits - before} 次）")
        check(not first.cached and second.cached, "第二次标记为 cached")
        forced = llm_probe.probe(force=True)
        check(server.hits - before == 2, "force=True 时忽略缓存重新探测")
        check(not forced.cached, "强制探测结果不标记为 cached")

        # ---------- H. 可关闭 ----------
        print("\n== 允许关闭探测（某些部署不希望就绪依赖模型服务）==")
        settings.llm_probe_enabled = False
        llm_probe.reset()
        disabled = llm_probe.probe()
        check(disabled.ok and "未启用" in disabled.message, "关闭后恒为就绪，并说明原因")

        settings.llm_probe_enabled = True
        settings.llm_probe_ttl_s = 0.0

        # ---------- I. HTTP 层：/api/ready 必须同时覆盖两条链路 ----------
        print("\n== /api/ready 必须同时反映嵌入与生成两条链路 ==")
        from app.embeddings import embedding_service  # noqa: PLC0415
        from app.main import app  # noqa: PLC0415

        client = TestClient(app)
        saved_state = (embedding_service.state, embedding_service.message)

        # F1 嵌入没就绪 → 503，且不去探测模型服务
        embedding_service.state = "idle"
        embedding_service.message = "加载中"
        server.mode = "ok_openai"
        llm_probe.reset()
        hits_before = server.hits
        r = client.get("/api/ready")
        check(r.status_code == 503, f"嵌入未就绪 → 503（实际 {r.status_code}）")
        check(r.json().get("reason") == "embed_not_ready", "原因标注为 embed_not_ready")
        check(server.hits == hits_before, "嵌入未就绪时不会去探测模型服务（省掉无意义的探测）")

        # F2 嵌入就绪 + 模型服务不可达 → 503，且原因为 llm_not_ready
        embedding_service.state = "ready"
        embedding_service.message = "就绪"
        settings.deepseek_base_url = f"http://127.0.0.1:{closed_port()}"
        llm_probe.reset()
        r = client.get("/api/ready")
        check(r.status_code == 503, f"嵌入就绪但模型服务不可达 → 503（实际 {r.status_code}）")
        body = r.json()
        check(body.get("reason") == "llm_not_ready", "原因标注为 llm_not_ready")
        check(body["checks"]["embed"]["ok"] is True, "checks.embed 显示嵌入是好的")
        check(body["checks"]["llm"]["ok"] is False, "checks.llm 显示生成不可用")
        check(body["model_ready"] is True, "model_ready 仍只表示嵌入（保持向后兼容）")

        # F3 两条链路都好 → 200
        settings.deepseek_base_url = server.base_url
        server.mode = "ok_openai"
        llm_probe.reset()
        r = client.get("/api/ready")
        check(r.status_code == 200, f"两条链路都好 → 200（实际 {r.status_code}）")
        check(r.json()["checks"]["llm"]["ok"] is True, "checks.llm 显示生成可用")

        # F4 存活探针不得触发探测（否则 Ollama 抖动会把容器判成不健康）
        settings.deepseek_base_url = f"http://127.0.0.1:{closed_port()}"
        llm_probe.reset()
        hits_before = server.hits
        r = client.get("/api/health")
        check(r.status_code == 200, "存活探针始终 200（不受生成模型影响）")
        check(r.json()["checks"]["llm"] is None, "没有缓存时不触发探测，checks.llm 为 null")
        check(server.hits == hits_before, "存活探针没有发起任何模型服务请求")

        # F5 存活探针会带上已有的缓存结果
        settings.deepseek_base_url = server.base_url
        llm_probe.reset()
        llm_probe.probe()
        r = client.get("/api/health")
        check(r.status_code == 200 and r.json()["checks"]["llm"]["ok"] is True,
              "存活探针附带上次探测的缓存结果")

        embedding_service.state, embedding_service.message = saved_state
    finally:
        server.stop()
        (
            settings.deepseek_base_url,
            settings.deepseek_model,
            settings.deepseek_api_key,
            settings.llm_probe_enabled,
            settings.llm_probe_ttl_s,
            settings.llm_probe_timeout_s,
        ) = saved
        llm_probe.reset()

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
