"""设置层：opencode provider 解析、设置存取与热重建、分章后端可配。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from novel_rag.api.server import build_app
from novel_rag.config import Settings
from novel_rag.errors import InvalidInput
from novel_rag.ingest import importer
from novel_rag.llm import opencode
from novel_rag.llm.gateway import OpenAICompatProvider, StubProvider, from_settings
from novel_rag.settings_store import SettingsStore

from .fixtures import SAMPLE_TEXT

OPENCODE_JSONC = """
{
  // opencode 允许行注释与尾逗号
  "provider": {
    "magpie": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Magpie",
      "options": {
        "baseURL": "http://127.0.0.1:3425/v1",
        "apiKey": "sk-test-key",
      },
      "models": {
        "qnaigc/qiniu/conatus-free": {"name": "conatus-free"},
        "agnes/agnes-3.0-flash": {"name": "agnes"},
      },
    },
  },
  "model": "qnaigc/qiniu/conatus-free",
}
"""


class OpenCodeConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "opencode.json"
        self.path.write_text(OPENCODE_JSONC, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_parses_jsonc_with_comments_and_trailing_commas(self) -> None:
        config = opencode.load_config(self.path)
        self.assertIn("provider", config)
        self.assertEqual(config["model"], "qnaigc/qiniu/conatus-free")

    def test_provider_spec(self) -> None:
        specs = opencode.providers(self.path)
        self.assertIn("magpie", specs)
        spec = specs["magpie"]
        self.assertEqual(spec.base_url, "http://127.0.0.1:3425/v1")
        self.assertEqual(spec.api_key, "sk-test-key")
        self.assertIn("qnaigc/qiniu/conatus-free", spec.models)

    def test_resolve_uses_requested_model(self) -> None:
        spec, model = opencode.resolve("magpie", None, path=self.path)
        assert spec is not None
        self.assertEqual(spec.name, "magpie")
        self.assertEqual(model, "qnaigc/qiniu/conatus-free")

        spec2, model2 = opencode.resolve("magpie", "agnes/agnes-3.0-flash", path=self.path)
        assert spec2 is not None
        self.assertEqual(model2, "agnes/agnes-3.0-flash")

    def test_missing_provider_falls_back_to_first(self) -> None:
        spec, _model = opencode.resolve("nope", None, path=self.path)
        assert spec is not None
        self.assertEqual(spec.name, "magpie")

    def test_describe_has_no_api_key(self) -> None:
        described = opencode.describe(self.path)
        payload = json.dumps(described, ensure_ascii=False)
        self.assertNotIn("sk-test-key", payload)
        self.assertTrue(described["providers"][0]["has_key"])


class GatewayResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "opencode.json"
        self.path.write_text(OPENCODE_JSONC, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_llm_provider_builds_openai_compat(self) -> None:
        settings = Settings(
            home=Path(self._tmp.name),
            llm_provider="magpie",
            llm_model="qnaigc/qiniu/conatus-free",
            opencode_config=str(self.path),
        )
        gateway = from_settings(settings)
        self.assertIsInstance(gateway.extract, OpenAICompatProvider)
        self.assertIsInstance(gateway.query, OpenAICompatProvider)
        self.assertEqual(gateway.extract.name, "magpie:qnaigc/qiniu/conatus-free")
        self.assertEqual(gateway.extract.base_url, "http://127.0.0.1:3425/v1")

    def test_explicit_env_wins_over_opencode(self) -> None:
        settings = Settings(
            home=Path(self._tmp.name),
            llm_provider="magpie",
            opencode_config=str(self.path),
            extract_base_url="http://other/v1",
            extract_model="m1",
        )
        gateway = from_settings(settings)
        self.assertEqual(gateway.extract.base_url, "http://other/v1")

    def test_no_config_uses_echo(self) -> None:
        settings = Settings(home=Path(self._tmp.name), opencode_config="/nonexistent.json")
        gateway = from_settings(settings)
        self.assertEqual(gateway.extract.name, "echo-extract")


class SettingsStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(home=Path(self._tmp.name), embedding_backend="hash")
        self.app = build_app(settings=self.settings, db_path=str(Path(self._tmp.name) / "t.db"))
        self.store: SettingsStore = self.app.store

    def tearDown(self) -> None:
        self.app.db.close()
        self._tmp.cleanup()

    def test_update_and_apply(self) -> None:
        self.store.update({"ner_backend": "regex", "split_backend": "rule"})
        self.assertEqual(self.store.get_all()["ner_backend"], "regex")
        applied = self.store.apply(self.settings)
        self.assertEqual(applied.ner_backend, "regex")
        self.assertEqual(applied.split_backend, "rule")

    def test_unknown_key_rejected(self) -> None:
        with self.assertRaises(InvalidInput):
            self.store.update({"hack": "1"})

    def test_invalid_enum_rejected(self) -> None:
        with self.assertRaises(InvalidInput):
            self.store.update({"ner_backend": "gpt"})

    def test_empty_value_clears_override(self) -> None:
        self.store.update({"ner_backend": "regex"})
        self.store.update({"ner_backend": ""})
        self.assertNotIn("ner_backend", self.store.get_all())

    def test_refresh_rebuilds_components(self) -> None:
        result = self.app.refresh()
        self.assertIn("embedding", result)
        self.assertEqual(result["ner_backend"], self.settings.ner_backend)

    def test_describe_exposes_options(self) -> None:
        described = self.store.describe(self.settings)
        self.assertIn("options", described)
        self.assertIn("ner_backend", described["options"])
        self.assertIn("split_backend", described["options"])
        self.assertIn("embedding", described)
        ids = {item["id"] for item in described["options"]["ner_backend"]}
        self.assertEqual(ids, {"auto", "jieba", "regex"})


class SplitBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _lines(self) -> list[int]:
        return [
            idx + 1
            for idx, line in enumerate(SAMPLE_TEXT.split("\n"))
            if line.startswith("第") and "章" in line
        ]

    def test_auto_keeps_rule_result(self) -> None:
        settings = Settings(home=Path(self._tmp.name), split_backend="auto", embedding_backend="hash")
        app = build_app(settings=settings, db_path=str(Path(self._tmp.name) / "a.db"))
        app.gateway.extract = StubProvider("{}")  # 不该被调用
        result = importer.import_text(
            app.db, text=SAMPLE_TEXT, title="x", settings=settings, llm=app.gateway
        )
        self.assertEqual(result.split_rule, "cn-chapter")
        app.db.close()

    def test_llm_backend_forces_llm_even_when_rule_matches(self) -> None:
        settings = Settings(home=Path(self._tmp.name), split_backend="llm", embedding_backend="hash")
        app = build_app(settings=settings, db_path=str(Path(self._tmp.name) / "b.db"))
        lines = self._lines()
        self.assertEqual(len(lines), 3)
        app.gateway.extract = StubProvider(json.dumps({"title_lines": lines}))
        result = importer.import_text(
            app.db, text=SAMPLE_TEXT, title="x", settings=settings, llm=app.gateway
        )
        self.assertEqual(result.split_rule, "llm")
        self.assertEqual(result.chapter_count, 3)
        app.db.close()

    def test_rule_backend_never_calls_llm(self) -> None:
        settings = Settings(home=Path(self._tmp.name), split_backend="rule", embedding_backend="hash")
        app = build_app(settings=settings, db_path=str(Path(self._tmp.name) / "c.db"))
        provider = StubProvider(json.dumps({"title_lines": [1]}))
        app.gateway.extract = provider
        result = importer.import_text(
            app.db, text="一二三四五。" * 2000, title="x", settings=settings, llm=app.gateway
        )
        self.assertEqual(result.split_rule, "fallback")
        app.db.close()


if __name__ == "__main__":
    unittest.main()
