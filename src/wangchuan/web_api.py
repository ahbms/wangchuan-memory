#!/usr/bin/env python3
"""
忘川 Web API 服务

定位：
- preview / local-only surface
- 默认只绑定 loopback，不承诺公网服务语义
- 如果 operator 显式设置 WANGCHUAN_HOST（例如 0.0.0.0），属于自担边界，不等于当前 contract 已提供公网安全能力

用法:
    python -m wangchuan.web_api
    # 默认访问 http://127.0.0.1:8765
"""

import json
import os
import sys
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

from wangchuan.paths import workspace_root

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

workspace = workspace_root()
if str(workspace) not in sys.path:
    sys.path.insert(0, str(workspace))

from wangchuan.memory_api import Memory


class WangchuanHandler(BaseHTTPRequestHandler):
    memory = None

    def do_OPTIONS(self):
        self.send_response(204)
        self._add_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Wangchuan-Token")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if self.memory is None:
            self.memory = Memory()

        try:
            if path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(self._get_html().encode("utf-8"))

            elif path == "/api/status":
                self._json_response(self.memory.status())

            elif path == "/api/recall":
                q = query.get("q", [""])[0]
                limit = int(query.get("limit", [10])[0])
                results = self.memory.recall(q, limit=limit)
                self._json_response({"results": results, "count": len(results)})

            elif path == "/api/recent":
                limit = int(query.get("limit", [10])[0])
                results = self.memory.recent(limit=limit)
                self._json_response({"results": results, "count": len(results)})

            elif path == "/api/tags":
                results = self.memory.list_all_tags()
                self._json_response({"tags": results, "count": len(results)})

            elif path == "/api/find_by_tag":
                tag = query.get("tag", [""])[0]
                if tag:
                    tag = unquote(tag)
                    results = self.memory.find_by_tag(tag)
                    self._json_response({"results": results, "count": len(results), "tag": tag})
                else:
                    self._json_response({"error": "tag required", "results": []}, status=400)

            elif path == "/favicon.ico":
                self.send_response(200)
                self.send_header("Content-Type", "image/x-icon")
                self.end_headers()
                self.wfile.write(b"")

            elif path == "/api/nodes":
                results = self.memory.list_nodes()
                self._json_response({"nodes": results, "count": len(results)})

            else:
                self._json_response({"error": "Not Found"}, status=404)

        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if self.memory is None:
            self.memory = Memory()

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body) if body else {}
        except Exception as e:
            print(f"[WangchuanHandler] JSON decode failed on {path}: {e}")
            data = {}

        try:
            if path == "/api/remember":
                if not self._check_write_token():
                    return
                content = data.get("content", "")
                importance = float(data.get("importance", 0.5))
                result = self.memory.remember(content, importance=importance)
                self._json_response(result)

            elif path == "/api/tag":
                if not self._check_write_token():
                    return
                memory_id = int(data.get("memory_id"))
                tag = data.get("tag", "")
                result = self.memory.add_tag(memory_id, tag)
                self._json_response(result)

            elif path == "/api/node":
                if not self._check_write_token():
                    return
                node_url = data.get("node_url", "")
                node_name = data.get("node_name")
                result = self.memory.register_node(node_url, node_name)
                self._json_response(result)

            else:
                self._json_response({"error": "Not Found"}, status=404)

        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _check_write_token(self):
        expected_token = os.environ.get("WANGCHUAN_WRITE_TOKEN")
        if not expected_token:
            self._json_response(
                {"error": "write API disabled: WANGCHUAN_WRITE_TOKEN not configured"},
                status=403,
            )
            return False

        auth_header = self.headers.get("Authorization", "")
        provided_token = ""
        if auth_header.startswith("Bearer "):
            provided_token = auth_header[len("Bearer "):].strip()
        if not provided_token:
            provided_token = self.headers.get("X-Wangchuan-Token", "").strip()

        if provided_token != expected_token:
            self._json_response({"error": "invalid write token"}, status=403)
            return False

        return True

    def _add_cors_headers(self):
        cors_origin = os.environ.get("WANGCHUAN_CORS_ORIGIN")
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _get_html(self):
        html_path = Path(__file__).with_name("web_ui.html")
        if html_path.exists():
            return html_path.read_text(encoding="utf-8")
        return "<html><body><h1>WangChuan UI missing</h1><p>Expected web_ui.html next to web_api.py</p></body></html>"


def _resolve_server_host() -> str:
    return os.environ.get("WANGCHUAN_HOST", DEFAULT_HOST)


def _resolve_server_port() -> int:
    return int(os.environ.get("WANGCHUAN_PORT", DEFAULT_PORT))


def run_server(port=None):
    if port is None:
        port = _resolve_server_port()
    host = _resolve_server_host()
    WangchuanHandler.memory = Memory()
    server = HTTPServer((host, port), WangchuanHandler)
    print(f"🚀 忘川 Web API 启动: http://{host}:{port}")
    print("📖 API 端点:")
    print("   GET  /              - Web 界面")
    print("   GET  /api/status   - 系统状态")
    print("   GET  /api/recall?q=xxx - 召回记忆")
    print("   GET  /api/recent   - 最近记忆")
    print("   POST /api/remember - 写入记忆（需要 token）")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
