"""CLI 契约与日志落盘。

这里锁的是线上事故：桌面壳传 ``--home``，而 argparse 不认识这个参数，
sidecar 直接退出 → 前端只看到「本地后端启动超时」，且没有日志可查。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from novel_rag.config import Settings
from novel_rag.logging_setup import log_paths, reset_logging, setup_logging

BACKEND_DIR = Path(__file__).resolve().parents[1]


class CliStartupTest(unittest.TestCase):
    def test_home_flag_accepted_and_logs_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                **os.environ,
                "NOVEL_RAG_TOKEN": "test-token",
                "NOVEL_RAG_EMBED_BACKEND": "hash",
                "NOVEL_RAG_LLM_PROVIDER": "",
                "PYTHONUNBUFFERED": "1",
            }
            env.pop("NOVEL_RAG_LOG_DIR", None)
            proc = subprocess.Popen(
                [sys.executable, "-m", "novel_rag", "--home", tmp, "--port", "0"],
                cwd=str(BACKEND_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                first = proc.stdout.readline().strip()
                second = proc.stdout.readline().strip()
                self.assertTrue(
                    first.startswith("PORT:"),
                    msg=f"未拿到 PORT 行（sidecar 可能启动失败）: {first!r}",
                )
                self.assertTrue(second.startswith("LOG:"), msg=second)
                port = int(first.split(":", 1)[1])
                self.assertGreater(port, 0)

                log_file = Path(second.split(":", 1)[1])
                self.assertEqual(log_file, Path(tmp) / "logs" / "backend.log")
                self.assertTrue(log_file.exists(), "backend.log 未生成")

                # 数据库应落在 --home 指定的目录
                self.assertTrue((Path(tmp) / "novel-rag.db").exists())
            finally:
                proc.terminate()
                try:
                    proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()

            content = log_file.read_text(encoding="utf-8")
            self.assertIn("starting:", content)
            self.assertIn("listening on", content)

    def test_unknown_argument_exits_fast(self) -> None:
        """非法参数必须立刻失败（而不是静默 hang），便于壳报错。"""
        proc = subprocess.run(
            [sys.executable, "-m", "novel_rag", "--nope"],
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unrecognized arguments", proc.stderr)


class LoggingTest(unittest.TestCase):
    def tearDown(self) -> None:
        reset_logging()

    def test_writes_to_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(home=Path(tmp))
            log_file = setup_logging(settings, console=False)
            self.assertEqual(log_file, Path(tmp) / "logs" / "backend.log")

            import logging

            logging.getLogger("novel_rag.test").warning("hello-log")
            for handler in logging.getLogger().handlers:
                handler.flush()

            self.assertIn("hello-log", log_file.read_text(encoding="utf-8"))

    def test_log_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as logtmp:
            settings = Settings(home=Path(tmp), log_dir=logtmp)
            log_file = setup_logging(settings, console=False)
            self.assertEqual(log_file, Path(logtmp) / "backend.log")
            self.assertTrue(log_file.exists())

    def test_paths_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(home=Path(tmp))
            settings.ensure_home()
            paths = log_paths(settings)
            self.assertEqual(paths["dir"], str(Path(tmp) / "logs"))
            self.assertTrue(paths["backend"].endswith("backend.log"))
            self.assertTrue(paths["sidecar"].endswith("sidecar.log"))


if __name__ == "__main__":
    unittest.main()
