"""本地后端 HTTP 接口（stdlib 实现，零依赖可运行）。

契约：所有请求带 ``Authorization: Bearer <token>``（token 由壳在 spawn sidecar
时通过环境变量注入，见架构决策 #1）。

路由分组：
    /api/books/**        原文与阅读态
    /api/analysis/**     后台抽取任务
    /api/ask             问答（唯一消费入口）
    /api/books/{id}/graph|timeline|entity   读模型
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import embedding_models
from ..config import Settings, get_settings
from ..db.conn import Database, open_db
from ..errors import InvalidInput, NotFound
from ..extract.pipeline import Pipeline
from ..ingest import importer
from ..ingest.resplit import resplit_book, update_chapter_title
from ..ingest.epub_builder import build_epub
from ..llm import embedding as embedding_mod
from ..llm import gateway as gateway_mod
from ..llm import opencode
from ..qa.service import QaService
from ..settings_store import SettingsStore
from ..retrieve import structured
from ..store import facts as fact_store
from ..store import index as index_store


@dataclass
class App:
    settings: Settings
    db: Database
    gateway: gateway_mod.LlmGateway
    embedder: embedding_mod.EmbeddingProvider
    pipeline: Pipeline
    qa: QaService
    store: SettingsStore
    threads: dict[str, threading.Thread] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # -------------------------------------------------------- 后台任务

    def start_analysis(self, book_id: str, *, max_chapters: int | None = None) -> None:
        with self.lock:
            running = self.threads.get(book_id)
            if running is not None and running.is_alive():
                return
            thread = threading.Thread(
                target=self._analysis_worker,
                args=(book_id, max_chapters),
                name=f"analysis-{book_id}",
                daemon=True,
            )
            self.threads[book_id] = thread
            thread.start()

    def _analysis_worker(self, book_id: str, max_chapters: int | None = None) -> None:
        logger = logging.getLogger("novel_rag.analysis")
        try:
            report = self.pipeline.run(book_id, max_chapters=max_chapters)
            logger.info("analysis %s: %s", book_id, report.to_dict())
        except Exception:  # noqa: BLE001
            logger.exception("analysis %s crashed", book_id)
            traceback.print_exc()

    def refresh(self) -> dict:
        """设置变更后重建组件：网关 / embedding / 流水线 / 问答。不重启进程。"""
        from ..extract import ner as ner_mod

        self.store.apply(self.settings)
        ner_mod.reset_default()
        self.gateway = gateway_mod.from_settings(self.settings)
        self.embedder = embedding_mod.from_settings(self.settings)
        self.pipeline = Pipeline(
            db=self.db, gateway=self.gateway, embedder=self.embedder, settings=self.settings
        )
        self.qa = QaService(
            db=self.db, gateway=self.gateway, embedder=self.embedder, settings=self.settings
        )
        return {
            "ner_backend": self.settings.ner_backend,
            "split_backend": self.settings.split_backend,
            "embedding": type(self.embedder).__name__,
            "embedding_model_id": self.embedder.model_id,
            "llm": {
                "extract": self.gateway.extract.name,
                "query": self.gateway.query.name,
            },
        }

    def watch_embedding_model(self) -> None:
        """首次启动会后台下载向量模型；下完自动切换到 BGE 并重建向量索引。

        不这么做的话，第一轮分析会用占位哈希向量建索引，之后即使模型下载完成，
        语义检索也一直是空的（model_id 不同，向量空间不通用）。
        """

        def worker() -> None:
            logger = logging.getLogger("novel_rag.embedding")
            dest = self.settings.resolved_embed_dir
            for _ in range(900):  # 最多等 30 分钟
                status = embedding_models.model_status(dest)
                if status["installed"]:
                    break
                time.sleep(2)
            else:
                logger.warning("embedding model 仍未就绪: %s", dest)
                return

            if not isinstance(self.embedder, embedding_mod.HashEmbedding):
                return
            try:
                self.refresh()
                logger.info("embedding 已切换到 %s，开始重建向量索引", self.embedder.model_id)
                for row in self.db.query("SELECT id, title FROM books"):
                    count = index_store.index_vectors(
                        self.db, row["id"], self.embedder, batch=self.settings.embedding_batch
                    )
                    logger.info("reindex %s (%s): %d 段", row["title"], row["id"], count)
            except Exception:  # noqa: BLE001
                logger.exception("embedding 切换/重建索引失败")

        threading.Thread(target=worker, name="embedding-watch", daemon=True).start()

    # -------------------------------------------------------- provider 管理

    def providers_payload(self) -> dict:
        """opencode 配置与界面添加的 provider 合并后的清单（不含 api_key）。"""
        payload = opencode.describe(self.settings.opencode_config, self.settings.custom_providers)
        payload["values"] = {
            "llm_provider": self.settings.llm_provider,
            "llm_model": self.settings.llm_model,
        }
        return payload

    def save_provider(self, body: dict) -> dict:
        """新增或更新一个界面自定义 provider。"""
        name = str(body.get("name") or "").strip()
        base_url = str(body.get("base_url") or body.get("baseURL") or "").strip().rstrip("/")
        if not name:
            raise InvalidInput("provider 名称不能为空")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
            raise InvalidInput("provider 名称只能包含字母、数字、点、下划线和短横线（≤64 字）")
        if not base_url.startswith(("http://", "https://")):
            raise InvalidInput("base_url 需以 http:// 或 https:// 开头")

        models = body.get("models")
        if isinstance(models, str):
            models = [m.strip() for m in re.split(r"[,\n]", models)]
        elif isinstance(models, list):
            models = [str(m).strip() for m in models]
        else:
            models = []
        models = [m for m in models if m]

        current = opencode.parse_custom(self.settings.custom_providers)
        existing = current.get(name)
        raw_key = body.get("api_key")
        if raw_key is None and existing is not None:
            api_key = existing.api_key  # 未传 key 时保留原值
        else:
            api_key = str(raw_key or "").strip()

        current[name] = opencode.ProviderSpec(
            name=name,
            base_url=base_url,
            api_key=api_key,
            models=models,
            label=str(body.get("label") or name).strip() or name,
            custom=True,
        )
        self._store_custom(current)
        return self.providers_payload()

    def delete_provider(self, name: str) -> dict:
        current = opencode.parse_custom(self.settings.custom_providers)
        if name not in current:
            raise NotFound("provider not found")
        del current[name]
        changes: dict[str, str] = {
            "custom_providers": json.dumps(
                [spec.to_storage() for spec in current.values()], ensure_ascii=False
            )
            if current
            else ""
        }
        if self.settings.llm_provider == name:
            changes["llm_provider"] = ""
            changes["llm_model"] = ""
        self.store.update(changes)
        self.refresh()
        return self.providers_payload()

    def _store_custom(self, specs) -> None:
        text = json.dumps(
            [spec.to_storage() for spec in specs.values()], ensure_ascii=False
        )
        self.store.update({"custom_providers": text})
        self.refresh()

    def recover_tasks(self) -> list[str]:
        """进程重启：把残留的 running 状态改成 paused，交给用户或下次启动续跑。"""
        rows = self.db.query("SELECT book_id FROM analysis_tasks WHERE state='running'")
        ids = [r["book_id"] for r in rows]
        for book_id in ids:
            self.db.execute(
                "UPDATE analysis_tasks SET state='paused' WHERE book_id=?", (book_id,)
            )
        return ids


def build_app(settings: Settings | None = None, db_path: str | None = None) -> App:
    settings = settings or get_settings()
    settings.ensure_home()
    db = open_db(db_path or settings.db_path)
    store = SettingsStore(db)
    store.apply(settings)
    gw = gateway_mod.from_settings(settings)
    emb = embedding_mod.from_settings(settings)
    pipeline = Pipeline(db=db, gateway=gw, embedder=emb, settings=settings)
    qa = QaService(db=db, gateway=gw, embedder=emb, settings=settings)

    # 决策：模型缺失时自动下载（后台线程，不阻塞启动）；下完再切 BGE 并重建索引
    download_started = False
    if settings.embedding_backend in ("auto", "bge") and isinstance(emb, embedding_mod.HashEmbedding):
        try:
            download_started = not embedding_models.ensure(
                settings.resolved_embed_dir,
                variant=settings.embedding_variant,
                endpoint=settings.embedding_endpoint or None,
                proxy=settings.network_proxy or None,
            )
        except Exception:  # noqa: BLE001 - 下载失败不影响启动
            download_started = False

    app = App(
        settings=settings,
        db=db,
        gateway=gw,
        embedder=emb,
        pipeline=pipeline,
        qa=qa,
        store=store,
    )
    if download_started:
        app.watch_embedding_model()
    return app


# ---------------------------------------------------------------- HTTP 层


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "novel-rag/0.1.0"

    # -------------------------------------------------------- 基础

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if not self.app.settings.home.joinpath("quiet").exists():
            super().log_message(fmt, *args)

    def _authorized(self) -> bool:
        token = self.app.settings.auth_token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {token}"

    def _send(self, status: int, payload: Any, *, content_type: str = "application/json") -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = str(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidInput("请求体不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise InvalidInput("请求体必须是 JSON 对象")
        return data

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", content_type="text/plain")

    # -------------------------------------------------------- 分发

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        query = parse_qs(parsed.query)
        try:
            result = self._route(method, parts, query)
        except NotFound as exc:
            self._send(404, {"error": str(exc) or "not found"})
            return
        except InvalidInput as exc:
            self._send(400, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("novel_rag.api").exception(
                "%s %s failed", method, self.path
            )
            traceback.print_exc()
            self._send(500, {"error": str(exc)})
            return

        if result is None:
            self._send(404, {"error": "not found"})
        elif isinstance(result, tuple) and result and result[0] == "__epub__":

            _, book_id, payload = result
            self.send_response(200)
            self.send_header("Content-Type", "application/epub+zip")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Disposition", f'inline; filename="{book_id}.epub"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        else:
            self._send(200, result)

    def _route(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        db = self.app.db
        if not parts or parts[0] != "api":
            raise NotFound()
        tail = parts[1:]

        if tail == ["health"]:
            return {"status": "ok", "version": self.server_version}

        # ------------------------------------------------------ 设置与模型
        if tail == ["settings"]:
            if method == "GET":
                return self.app.store.describe(self.app.settings)
            if method == "PUT":
                self.app.store.update(self._read_json())
                effective = self.app.refresh()
                payload = self.app.store.describe(self.app.settings)
                payload["effective"] = effective
                return payload
            if method == "DELETE":
                self.app.store.update({key: "" for key in self.app.store.get_all()})
                payload = self.app.store.describe(self.app.settings)
                payload["effective"] = self.app.refresh()
                return payload

        if tail == ["models"]:
            return self.app.store.describe(self.app.settings)["options"]

        # provider：opencode 配置 + 界面添加（可增删）
        if tail == ["providers"]:
            if method == "GET":
                return self.app.providers_payload()
            if method == "POST":
                return self.app.save_provider(self._read_json())
        if len(tail) == 2 and tail[0] == "providers" and method == "DELETE":
            return self.app.delete_provider(tail[1])

        if len(tail) >= 3 and tail[:2] == ["models", "embedding"] and tail[2] == "download":
            dest = self.app.settings.resolved_embed_dir
            if method == "GET":
                return {
                    "status": embedding_models.download_state(dest),
                    "model": embedding_models.model_status(dest),
                }
            if method == "POST":
                body = self._read_json()
                state = embedding_models.download_async(
                    dest,
                    variant=str(body.get("variant") or self.app.settings.embedding_variant),
                    endpoint=body.get("endpoint") or self.app.settings.embedding_endpoint or None,
                    proxy=self.app.settings.network_proxy or None,
                )
                return {
                    "status": state,
                    "model": embedding_models.model_status(dest),
                }

        if tail == ["books"]:
            if method == "GET":
                return {"books": importer.list_books(db)}
            if method == "POST":
                return self._create_book(self._read_json())

        if len(tail) >= 2 and tail[0] == "books":
            book_id = tail[1]
            book = importer.get_book(db, book_id)
            if book is None:
                raise NotFound("book not found")
            rest = tail[2:]
            return self._book_route(method, book_id, rest, query)

        if len(tail) >= 2 and tail[0] == "analysis":
            book_id = tail[1]
            action = tail[2] if len(tail) > 2 else ""
            if method == "GET":
                return self.app.pipeline.status(book_id)
            if method == "POST":
                if action == "start":
                    state = self.app.pipeline.start(book_id)
                    body = self._read_json() if self.headers.get("Content-Length") else {}
                    self.app.start_analysis(book_id, max_chapters=_opt_int(body.get("max_chapters")))
                    return state
                if action == "pause":
                    return self.app.pipeline.pause(book_id)
                if action == "resume":
                    state = self.app.pipeline.resume(book_id)
                    self.app.start_analysis(book_id)
                    return state
                if action == "retry":
                    body = self._read_json()
                    chapter_idx = _opt_int(body.get("chapter_idx"))
                    if chapter_idx is None:
                        raise InvalidInput("需要 chapter_idx（从 0 开始）")
                    state = self.app.pipeline.retry_from(book_id, chapter_idx)
                    self.app.start_analysis(book_id, max_chapters=_opt_int(body.get("max_chapters")))
                    return state
            raise NotFound()

        if tail == ["ask"] and method == "POST":
            body = self._read_json()
            return self.app.qa.ask(
                str(body.get("book_id") or ""),
                str(body.get("question") or ""),
                progress_seq=_opt_int(body.get("progress_seq")),
                spoiler_guard=_opt_bool(body.get("spoiler_guard")),
            )

        raise NotFound()

    # -------------------------------------------------------- 书籍子路由

    def _book_route(self, method: str, book_id: str, rest: list[str], query) -> Any:
        db = self.app.db
        book = importer.get_book(db, book_id)
        assert book is not None

        if rest == ["chapters"]:
            rows = db.query(
                "SELECT c.idx, c.title, c.fp, c.spine_index, c.nav_href, c.split_source, c.confidence, "
                "c.char_start, c.char_end, MIN(p.seq) AS seq_start, MAX(p.seq) AS seq_end "
                "FROM chapters c LEFT JOIN paragraphs p "
                "  ON p.book_id=c.book_id AND p.chapter_idx=c.idx "
                "WHERE c.book_id=? GROUP BY c.idx ORDER BY c.idx",
                (book_id,),
            )
            return {"book_id": book_id, "chapters": [dict(r) for r in rows]}

        if rest == ["resplit"] and method == "POST":
            body = self._read_json()
            result = resplit_book(
                db,
                book_id,
                pattern=(body.get("pattern") or None),
                min_confidence=_opt_float(body.get("min_confidence")),
                settings=self.app.settings,
                llm=self.app.gateway if body.get("use_llm") else None,
            )
            if body.get("auto_analyze", True):
                self.app.pipeline.start(book_id)
                self.app.start_analysis(book_id)
            return result.to_dict()

        if rest == ["audit"]:
            return {"audit": fact_store.audit_of(db, book_id), "rejects": fact_store.rejects_of(db, book_id)}

        if len(rest) == 2 and rest[0] == "chapters" and method == "PUT":
            body = self._read_json()
            return update_chapter_title(db, book_id, int(rest[1]), str(body.get("title") or ""))

        if len(rest) == 2 and rest[0] == "chapters":
            idx = int(rest[1])
            row = db.query_one("SELECT * FROM chapters WHERE book_id=? AND idx=?", (book_id, idx))
            if row is None:
                raise NotFound("chapter not found")
            facts = fact_store.get_chapter_fact(db, book_id, idx)
            paragraphs = db.query(
                "SELECT seq, para_idx, char_off, cfi, text FROM paragraphs WHERE book_id=? AND chapter_idx=? "
                "ORDER BY para_idx",
                (book_id, idx),
            )
            return {
                "chapter": dict(row),
                "facts": facts,
                "paragraphs": [dict(r) for r in paragraphs],
            }

        if rest == ["epub"]:
            rows = db.query(
                "SELECT idx, title, body, char_start, char_end, fp, spine_index, nav_href "
                "FROM chapters WHERE book_id=? ORDER BY idx",
                (book_id,),
            )
            from ..domain.models import Chapter

            chapters = [
                Chapter(
                    idx=r["idx"],
                    title=r["title"],
                    body=r["body"],
                    char_start=r["char_start"],
                    char_end=r["char_end"],
                    fp=r["fp"],
                    spine_index=r["spine_index"],
                    nav_href=r["nav_href"],
                )
                for r in rows
            ]
            payload = build_epub(book.title, book.author, chapters)
            return ("__epub__", book_id, payload)

        if rest == ["coverage"]:
            return {
                "coverage": fact_store.coverage_summary(db, book_id),
                "vectors": index_store.vector_coverage(
                    db, book_id, self.app.embedder.model_id
                ),
                "task": self.app.pipeline.status(book_id),
            }

        if rest == ["progress"]:
            if method == "PUT":
                body = self._read_json()
                seq = int(body.get("progress_seq") or 0)
                guard = 1 if bool(body.get("spoiler_guard", True)) else 0
                db.execute(
                    "INSERT INTO reading_progress(book_id, progress_seq, progress_cfi, spoiler_guard, updated_at) "
                    "VALUES(?,?,?,?,datetime('now')) ON CONFLICT(book_id) DO UPDATE SET "
                    "progress_seq=excluded.progress_seq, progress_cfi=excluded.progress_cfi, "
                    "spoiler_guard=excluded.spoiler_guard, updated_at=excluded.updated_at",
                    (book_id, seq, str(body.get("progress_cfi") or ""), guard),
                )
            row = db.query_one("SELECT * FROM reading_progress WHERE book_id=?", (book_id,))
            return dict(row) if row else {"progress_seq": 0, "spoiler_guard": 1}

        if rest == ["graph"]:
            as_of = _opt_int(_first(query, "as_of_seq"))
            return {
                "as_of_seq": as_of,
                "relations": fact_store.relation_pairs_at(db, book_id, as_of),
                "entities": [
                    dict(r)
                    for r in db.query(
                        "SELECT id, canonical_name, kind, first_seq FROM entities WHERE book_id=?",
                        (book_id,),
                    )
                ],
            }

        if rest == ["timeline"]:
            up_to = _opt_int(_first(query, "up_to_seq"))
            return {"timeline": fact_store.timeline(db, book_id, up_to)}

        if rest == ["entity"]:
            name = _first(query, "name") or ""
            profile = structured.entity_profile(
                db, book_id, name, as_of_seq=_opt_int(_first(query, "as_of_seq"))
            )
            if profile is None:
                raise NotFound("entity not found")
            return profile

        if rest == ["notes"]:
            if method == "POST":
                body = self._read_json()
                note_id = str(body.get("id") or "")
                if not note_id:
                    raise InvalidInput("缺少 id")
                db.execute(
                    "INSERT OR REPLACE INTO notes(id, book_id, kind, cfi, seq, chapter_idx, text, note, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                    (
                        note_id,
                        book_id,
                        str(body.get("kind") or "note"),
                        str(body.get("cfi") or ""),
                        _opt_int(body.get("seq")),
                        _opt_int(body.get("chapter_idx")),
                        str(body.get("text") or ""),
                        str(body.get("note") or ""),
                    ),
                )
                return {"ok": True, "id": note_id}
            rows = db.query("SELECT * FROM notes WHERE book_id=? ORDER BY created_at", (book_id,))
            return {"notes": [dict(r) for r in rows]}

        raise NotFound()

    def _create_book(self, body: dict) -> dict:
        settings = self.app.settings
        title = str(body.get("title") or "未命名")
        author = str(body.get("author") or "")
        source = str(body.get("source_name") or "")
        if body.get("path"):
            path = Path(str(body["path"])).expanduser()
            if not path.exists():
                raise NotFound(f"文件不存在: {path}")
            result = importer.import_bytes(
                self.app.db,
                path.read_bytes(),
                title=body.get("title") or path.stem,
                author=author,
                source_name=path.name,
                settings=settings,
                llm=self.app.gateway,
            )
        elif isinstance(body.get("content_base64"), str):
            import base64

            try:
                raw = base64.b64decode(str(body["content_base64"]), validate=True)
            except Exception as exc:  # noqa: BLE001
                raise InvalidInput(f"content_base64 解码失败: {exc}") from exc
            if not raw:
                raise InvalidInput("文件为空")
            result = importer.import_bytes(
                self.app.db,
                raw,
                title=body.get("title") or Path(str(body.get("filename") or "未命名")).stem,
                author=author,
                source_name=str(body.get("filename") or ""),
                settings=settings,
                llm=self.app.gateway,
            )
        elif isinstance(body.get("text"), str):
            result = importer.import_text(
                self.app.db,
                text=str(body["text"]),
                title=title,
                author=author,
                source_name=source,
                settings=settings,
                llm=self.app.gateway,
            )
        else:
            raise InvalidInput("需要 text 或 path")

        # 决策 #6：导入即后台全量预抽取，且立即解锁（不阻塞接口返回）。
        # analyze=false 用于「先导入、稍后手动控制分析量」的场景（如 LLM 成本试跑）。
        if body.get("analyze", True):
            if not result.reused:
                self.app.pipeline.start(result.book.id)
            self.app.start_analysis(result.book.id)

        return {
            "book": {
                "id": result.book.id,
                "title": result.book.title,
                "author": result.book.author,
                "chapter_fp": result.book.chapter_fp,
            },
            "chapter_count": result.chapter_count,
            "paragraph_count": result.paragraph_count,
            "reused": result.reused,
            "split_rule": result.split_rule,
            "split_confidence": result.confidence,
            "needs_llm_split": result.needs_llm,
        }


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _opt_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def serve(app: App, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd
