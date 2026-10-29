"""Web 部署：同源托管静态前端，同时 API 仍然要 token。

锁的是 Docker/Web 方案的两个边界：
1) 非 /api 路径走静态分支，不校验 token（浏览器带不上自定义头）；
2) /api/** 依旧校验 Bearer，静态分支不能成为绕过鉴权的口子。
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from novel_rag.api.server import build_app, serve
from novel_rag.config import Settings


class WebStaticTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.web = root / "web"
        (self.web / "js").mkdir(parents=True)
        (self.web / "index.html").write_text("<html><body>novel-rag</body></html>", encoding="utf-8")
        (self.web / "js" / "app.js").write_text("console.log('hi')", encoding="utf-8")
        (root / "secret.txt").write_text("should not be served", encoding="utf-8")

        self.settings = Settings(
            home=self.home,
            auth_token="web-token",
            embedding_backend="hash",
            web_root=str(self.web),
        )
        self.settings.ensure_home()
        self.app = build_app(settings=self.settings)
        self.httpd = serve(self.app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.db.close()
        self._tmp.cleanup()

    def _get(self, path: str, *, token: str | None = None) -> tuple[int, dict, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read()
        head = {k.lower(): v for k, v in res.getheaders()}
        conn.close()
        return res.status, head, body

    def test_index_is_served_without_token(self) -> None:
        status, head, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", head["content-type"])
        self.assertEqual(head["cache-control"], "no-cache")
        self.assertIn(b"novel-rag", body)

    def test_static_assets_and_content_types(self) -> None:
        status, head, body = self._get("/js/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", head["content-type"])
        self.assertEqual(head["cache-control"], "public, max-age=3600")
        self.assertIn(b"console.log", body)

    def test_missing_file_is_404(self) -> None:
        status, _head, _body = self._get("/nope.js")
        self.assertEqual(status, 404)

    def test_path_traversal_is_blocked(self) -> None:
        for path in ("/../secret.txt", "/..%2Fsecret.txt", "/js/../../secret.txt"):
            status, _head, body = self._get(path)
            self.assertEqual(status, 404, msg=f"{path} 泄露了静态根目录外的文件")
            self.assertNotIn(b"should not be served", body)

    def test_api_still_requires_token(self) -> None:
        status, _head, _body = self._get("/api/health")
        self.assertEqual(status, 401)
        status, _head, body = self._get("/api/health", token="web-token")
        self.assertEqual(status, 200)
        self.assertIn(b"ok", body)

    def test_log_tail_endpoint(self) -> None:
        """日志页依赖这个接口：只返回尾部 N 行，且必须带 token。"""
        log_file = self.settings.log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entries = [f"2026-10-29 20:00:0{i} INFO     [MainThread] novel_rag.test: line{i}" for i in range(3)]
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(entries) + "\n")

        status, _head, body = self._get("/api/logs?source=backend&lines=2", token="web-token")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["source"], "backend")
        self.assertEqual(payload["lines"][-2:], entries[-2:])

        status, _head, _body = self._get("/api/logs?source=bogus", token="web-token")
        self.assertEqual(status, 400)


class WebRootUnsetTest(unittest.TestCase):
    """没配 web_root 时行为不变：非 /api 路径一律 404。"""

    def test_no_static_when_web_root_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(home=Path(tmp), auth_token="t", embedding_backend="hash")
            settings.ensure_home()
            app = build_app(settings=settings)
            httpd = serve(app, "127.0.0.1", 0)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request("GET", "/")
                res = conn.getresponse()
                self.assertEqual(res.status, 404)
                res.read()
                conn.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                app.db.close()


if __name__ == "__main__":
    unittest.main()
