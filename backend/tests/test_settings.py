"""设置层：opencode provider 解析、设置存取与热重建、分章后端可配。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from novel_rag.api.server import build_app
from novel_rag.config import Settings
from novel_rag.errors import InvalidInput, NotFound
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


# opencode v2 的 schema：providers（复数）+ settings，而不是 provider + options
OPENCODE_V2 = """
{
  "providers": {
    "magpie": {
      "package": "@opencode/ai/providers/openai-compatible",
      "name": "Magpie",
      "settings": {
        "baseURL": "http://127.0.0.1:3425/v1",
        "apiKey": "sk-v2-key",
      },
      "models": {},
    },
  },
  "model": "qnaigc/qiniu/conatus-free",
}
"""


class OpenCodeV2ConfigTest(unittest.TestCase):
    """新版 opencode 配置（providers/settings）必须同样能解析出 provider。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "opencode.json"
        self.path.write_text(OPENCODE_V2, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_providers_plural_schema(self) -> None:
        specs = opencode.providers(self.path)
        self.assertIn("magpie", specs)
        spec = specs["magpie"]
        self.assertEqual(spec.base_url, "http://127.0.0.1:3425/v1")
        self.assertEqual(spec.api_key, "sk-v2-key")
        self.assertEqual(spec.label, "Magpie")

    def test_resolve_falls_back_to_default_model(self) -> None:
        spec, model = opencode.resolve("magpie", None, path=self.path)
        assert spec is not None
        self.assertEqual(model, "qnaigc/qiniu/conatus-free")

    def test_describe_hides_api_key(self) -> None:
        described = opencode.describe(self.path)
        self.assertNotIn("sk-v2-key", json.dumps(described, ensure_ascii=False))
        self.assertTrue(described["providers"][0]["has_key"])

    def test_gateway_uses_v2_provider(self) -> None:
        settings = Settings(
            home=Path(self._tmp.name),
            llm_provider="magpie",
            opencode_config=str(self.path),
        )
        gateway = from_settings(settings)
        self.assertIsInstance(gateway.query, OpenAICompatProvider)
        self.assertEqual(gateway.query.name, "magpie:qnaigc/qiniu/conatus-free")


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

    def test_missing_provider_is_not_silently_replaced(self) -> None:
        """选中的 provider 不存在时必须报错，不能悄悄换成别的（否则原因与事实不符）。"""
        spec, model = opencode.resolve("nope", None, path=self.path)
        self.assertIsNone(spec)
        self.assertEqual(model, "")

    def test_no_default_path_discovery(self) -> None:
        """应用默认不读本机 opencode 配置：不给路径就是空，避免「偷偷生效」。"""
        self.assertEqual(opencode.providers(None), {})
        self.assertEqual(opencode.describe()["providers"], [])
        self.assertIsNone(opencode.describe()["config_path"])

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


class CustomProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.custom = json.dumps(
            [
                {
                    "name": "local-vllm",
                    "label": "本地 vLLM",
                    "base_url": "http://127.0.0.1:8000/v1",
                    "api_key": "sk-local",
                    "models": ["qwen2.5-7b", "qwen2.5-14b"],
                }
            ],
            ensure_ascii=False,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_parse_custom_tolerates_bad_input(self) -> None:
        self.assertEqual(opencode.parse_custom("not-json"), {})
        self.assertEqual(opencode.parse_custom("[]"), {})
        specs = opencode.parse_custom(self.custom)
        self.assertEqual(specs["local-vllm"].base_url, "http://127.0.0.1:8000/v1")
        self.assertTrue(specs["local-vllm"].custom)

    def test_gateway_uses_custom_provider(self) -> None:
        settings = Settings(
            home=Path(self._tmp.name),
            llm_provider="local-vllm",
            llm_model="qwen2.5-7b",
            custom_providers=self.custom,
            opencode_config="/nonexistent.json",
            network_proxy="http://127.0.0.1:7890",
        )
        gateway = from_settings(settings)
        self.assertIsInstance(gateway.extract, OpenAICompatProvider)
        self.assertEqual(gateway.extract.name, "local-vllm:qwen2.5-7b")
        self.assertEqual(gateway.extract.proxy, "http://127.0.0.1:7890")

    def test_describe_merges_without_leaking_keys(self) -> None:
        described = opencode.describe("/nonexistent.json", self.custom)
        payload = json.dumps(described, ensure_ascii=False)
        self.assertNotIn("sk-local", payload)
        self.assertEqual(
            [p["name"] for p in described["providers"] if p["custom"]], ["local-vllm"]
        )

    def test_custom_overrides_opencode_provider(self) -> None:
        merged = opencode.merged("/nonexistent.json", self.custom)
        self.assertEqual(merged["local-vllm"].models[0], "qwen2.5-7b")

    def test_custom_provider_without_models_does_not_borrow_default(self) -> None:
        raw = json.dumps([{"name": "naked", "base_url": "http://127.0.0.1:9000/v1"}])
        settings = Settings(
            home=Path(self._tmp.name),
            llm_provider="naked",
            custom_providers=raw,
            opencode_config="/nonexistent.json",
        )
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
        self.assertIn("embedding_endpoint", described["options"])
        self.assertIn("network_proxy", described["values"])

    def test_network_settings_are_stored(self) -> None:
        self.store.update(
            {"network_proxy": "http://127.0.0.1:7890", "embedding_endpoint": "https://hf-mirror.com"}
        )
        applied = self.store.apply(self.settings)
        self.assertEqual(applied.network_proxy, "http://127.0.0.1:7890")
        self.assertEqual(applied.embedding_endpoint, "https://hf-mirror.com")

    def test_provider_add_and_delete(self) -> None:
        saved = self.app.save_provider(
            {
                "name": "my-api",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-xxx",
                "models": "m1, m2",
            }
        )
        names = [p["name"] for p in saved["providers"]]
        self.assertIn("my-api", names)
        custom = [p for p in saved["providers"] if p["custom"]]
        self.assertEqual(custom[0]["models"], ["m1", "m2"])
        self.assertNotIn("sk-xxx", json.dumps(saved, ensure_ascii=False))

        described = self.store.describe(self.settings)
        self.assertNotIn("custom_providers", described["overrides"])
        self.assertNotIn("sk-xxx", json.dumps(described, ensure_ascii=False))

        removed = self.app.delete_provider("my-api")
        self.assertNotIn("my-api", [p["name"] for p in removed["providers"]])
        with self.assertRaises(NotFound):
            self.app.delete_provider("my-api")

    def test_provider_rejects_bad_base_url(self) -> None:
        with self.assertRaises(InvalidInput):
            self.app.save_provider({"name": "bad", "base_url": "ftp://x"})


class EmbeddingEndpointTest(unittest.TestCase):
    def test_default_uses_official_then_mirror(self) -> None:
        from novel_rag import embedding_models

        self.assertEqual(
            embedding_models._endpoints(None), list(embedding_models.DEFAULT_ENDPOINTS)
        )
        self.assertEqual(
            embedding_models._endpoints("https://hf-mirror.com/"), ["https://hf-mirror.com"]
        )

    def test_endpoint_failure_falls_back_to_mirror(self) -> None:
        from unittest import mock

        from novel_rag import embedding_models

        seen: list[str] = []

        def fake_download(dest, *, variant, endpoint, opener) -> None:
            seen.append(endpoint)
            if endpoint == "https://huggingface.co":
                raise RuntimeError("tokenizer.json: timed out")
            for name in ("model.onnx", "tokenizer.json"):
                (dest / name).write_bytes(b"x")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(embedding_models, "_download_from", side_effect=fake_download):
                state = embedding_models.download(tmp)
        self.assertEqual(seen, ["https://huggingface.co", "https://hf-mirror.com"])
        self.assertEqual(state["state"], "done")

    def test_missing_required_file_is_not_reported_done(self) -> None:
        from unittest import mock

        from novel_rag import embedding_models

        def fake_download(dest, *, variant, endpoint, opener) -> None:
            (dest / "model.onnx").write_bytes(b"x")  # 少了 tokenizer.json

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(embedding_models, "_download_from", side_effect=fake_download):
                with self.assertRaises(RuntimeError):
                    embedding_models.download(tmp)
            self.assertNotEqual(embedding_models.download_state(tmp)["state"], "done")

    def test_requests_carry_non_python_user_agent(self) -> None:
        from novel_rag import embedding_models
        from novel_rag.net import USER_AGENT

        class Recorder:
            def open(self, request, timeout=120):
                self.headers = request.headers
                raise RuntimeError("stop")

        recorder = Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                embedding_models._fetch(
                    "https://example.com/x", Path(tmp) / "x.bin", Path(tmp), opener=recorder
                )
        self.assertEqual(recorder.headers.get("User-agent"), USER_AGENT)


class EmbeddingFallbackTest(unittest.TestCase):
    """模型缺失不应该让进程起不来（打包后用户没有终端可救）。"""

    def test_explicit_bge_without_model_degrades(self) -> None:
        from novel_rag.api.server import _embedder_or_fallback
        from novel_rag.llm import embedding as emb

        settings = Settings(embedding_dir="/nonexistent/path", embedding_backend="bge")
        provider, reason = _embedder_or_fallback(settings)
        self.assertIsInstance(provider, emb.HashEmbedding)
        self.assertIn("未找到模型", reason)

    def test_build_app_survives_missing_bge_model(self) -> None:
        from unittest import mock

        from novel_rag import embedding_models
        from novel_rag.llm import embedding as emb

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                home=Path(tmp), embedding_backend="bge", embedding_dir="/nonexistent/path"
            )
            with mock.patch.object(embedding_models, "ensure", return_value=True):
                app = build_app(settings=settings, db_path=str(Path(tmp) / "x.db"))
            self.assertIsInstance(app.embedder, emb.HashEmbedding)
            app.db.close()

    def test_refresh_with_missing_bge_model_keeps_running(self) -> None:
        from unittest import mock

        from novel_rag import embedding_models
        from novel_rag.llm import embedding as emb

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(home=Path(tmp), embedding_backend="hash")
            app = build_app(settings=settings, db_path=str(Path(tmp) / "y.db"))
            app.store.update(
                {"embedding_backend": "bge", "embedding_dir": "/nonexistent/path"}
            )
            with mock.patch.object(embedding_models, "ensure", return_value=True):
                info = app.refresh()
            self.assertIsInstance(app.embedder, emb.HashEmbedding)
            self.assertEqual(info["embedding"], "HashEmbedding")
            app.db.close()


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


class LlmDiagnosticsTest(unittest.TestCase):
    """「已配置模型却提示未生效」必须能给出唯一原因。"""

    def _app(self, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        options = dict(
            home=Path(tmp.name),
            embedding_backend="hash",
            opencode_config="/nonexistent.json",
        )
        options.update(kwargs)
        settings = Settings(**options)
        app = build_app(settings=settings, db_path=str(Path(tmp.name) / "d.db"))
        self.addCleanup(app.db.close)
        return app, settings

    def test_provider_without_models_reports_missing_model(self) -> None:
        raw = json.dumps([{"name": "naked", "base_url": "http://127.0.0.1:9000/v1"}])
        app, _ = self._app(custom_providers=raw, llm_provider="naked")
        summary = app.effective_summary()
        self.assertFalse(summary["llm_ready"]["query"])
        self.assertIn("没有可用模型", summary["llm_detail"]["reason"])

    def test_configured_provider_has_empty_reason(self) -> None:
        raw = json.dumps(
            [
                {
                    "name": "local",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "models": ["qwen2.5-7b"],
                }
            ]
        )
        app, _ = self._app(
            custom_providers=raw, llm_provider="local", llm_model="qwen2.5-7b"
        )
        summary = app.effective_summary()
        self.assertTrue(summary["llm_ready"]["query"])
        self.assertEqual(summary["llm_detail"]["reason"], "")
        self.assertEqual(summary["llm_detail"]["resolved_query"], "local:qwen2.5-7b")

    def test_half_configured_env_is_explained(self) -> None:
        app, _ = self._app(query_base_url="http://127.0.0.1:9000/v1")
        summary = app.effective_summary()
        self.assertFalse(summary["llm_ready"]["query"])
        self.assertIn("缺少 NOVEL_RAG_QUERY_MODEL", summary["llm_detail"]["reason"])

    def test_unknown_provider_is_explained(self) -> None:
        app, _ = self._app(llm_provider="ghost")
        summary = app.effective_summary()
        self.assertFalse(summary["llm_ready"]["query"])
        self.assertIn("找不到", summary["llm_detail"]["reason"])

    def test_v2_config_provider_works_without_manual_selection_input(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "opencode.json"
        path.write_text(OPENCODE_V2, encoding="utf-8")
        app, _ = self._app(opencode_config=str(path), llm_provider="magpie")
        summary = app.effective_summary()
        self.assertTrue(summary["llm_ready"]["query"])
        self.assertEqual(
            summary["llm_detail"]["resolved_query"], "magpie:qnaigc/qiniu/conatus-free"
        )

    def test_provider_present_but_unselected_is_explained(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "opencode.json"
        path.write_text(OPENCODE_V2, encoding="utf-8")
        app, _ = self._app(opencode_config=str(path))
        summary = app.effective_summary()
        self.assertFalse(summary["llm_ready"]["query"])
        self.assertIn("还没选中", summary["llm_detail"]["reason"])

    def test_ui_added_provider_is_auto_selected(self) -> None:
        """界面加了 provider 却没选中时，问答仍然是离线摘录——必须替用户选上。"""
        app, settings = self._app()
        app.save_provider(
            {
                "name": "my-api",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-xxx",
                "models": "m1, m2",
            }
        )
        self.assertEqual(settings.llm_provider, "my-api")
        self.assertEqual(settings.llm_model, "m1")
        self.assertTrue(app.effective_summary()["llm_ready"]["query"])


if __name__ == "__main__":
    unittest.main()
