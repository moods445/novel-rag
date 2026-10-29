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
import mimetypes
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

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
    watching_embedding: bool = False
    # 向量重建任务：book_id -> {state, chapter_idx, chapter_no, indexed, ...}
    vector_jobs: dict[str, dict] = field(default_factory=dict)

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
        embedder, reason = _embedder_or_fallback(self.settings)
        self.embedder = embedder
        if reason:
            self._start_embedding_download()
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

    def _start_embedding_download(self) -> None:
        """模型缺失时后台补齐（幂等：正在下载/已安装都会直接返回）。"""
        if self.settings.embedding_backend not in ("auto", "bge"):
            return
        try:
            started = not embedding_models.ensure(
                self.settings.resolved_embed_dir,
                variant=self.settings.embedding_variant,
                endpoint=self.settings.embedding_endpoint or None,
                proxy=self.settings.network_proxy or None,
            )
        except Exception:  # noqa: BLE001 - 下载失败不影响启动
            return
        if started:
            self.watch_embedding_model()

    def watch_embedding_model(self) -> None:
        """首次启动会后台下载向量模型；下完自动切换到 BGE 并重建向量索引。

        不这么做的话，第一轮分析会用占位哈希向量建索引，之后即使模型下载完成，
        语义检索也一直是空的（model_id 不同，向量空间不通用）。
        """

        with self.lock:
            if self.watching_embedding:
                return
            self.watching_embedding = True

        def worker() -> None:
            logger = logging.getLogger("novel_rag.embedding")
            dest = self.settings.resolved_embed_dir
            try:
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
            finally:
                # 允许之后的 refresh（如用户重装/重连网络）再次启动监听
                self.watching_embedding = False

        threading.Thread(target=worker, name="embedding-watch", daemon=True).start()

    # -------------------------------------------------------- 向量化任务

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def effective_summary(self) -> dict:
        """当前真正生效的组件（设置页展示 & 排查问答降级用）。"""
        from ..llm.gateway import EchoProvider

        return {
            "ner_backend": self.settings.ner_backend,
            "split_backend": self.settings.split_backend,
            "embedding": type(self.embedder).__name__,
            "embedding_model_id": self.embedder.model_id,
            "llm": {
                "extract": self.gateway.extract.name,
                "query": self.gateway.query.name,
            },
            "llm_ready": {
                "extract": not isinstance(self.gateway.extract, EchoProvider),
                "query": not isinstance(self.gateway.query, EchoProvider),
            },
        }

    def vector_status(self, book_id: str) -> dict:
        payload = index_store.vector_status(self.db, book_id, self.embedder.model_id)
        with self.lock:
            job = dict(self.vector_jobs.get(book_id) or {})
        payload.update(
            {
                "book_id": book_id,
                "backend": type(self.embedder).__name__,
                "job": job or {"state": "idle"},
            }
        )
        return payload

    def start_vector_rebuild(self, book_id: str) -> dict:
        """后台重建整本书的向量（逐章推进，进度可在页面看到）。"""
        with self.lock:
            job = self.vector_jobs.get(book_id) or {}
            if job.get("state") in ("queued", "running"):
                raise InvalidInput("向量重建正在进行中")
            self.vector_jobs[book_id] = {
                "state": "queued",
                "chapter_idx": None,
                "chapter_no": None,
                "indexed": 0,
                "message": "",
                "started_at": self._now(),
                "updated_at": self._now(),
            }
            thread = threading.Thread(
                target=self._vector_rebuild_worker,
                args=(book_id,),
                name=f"vector-rebuild-{book_id}",
                daemon=True,
            )
            thread.start()
        logging.getLogger("novel_rag.vectors").info("rebuild queued: %s", book_id)
        return self.vector_status(book_id)

    def _update_vector_job(self, book_id: str, **changes: Any) -> None:
        with self.lock:
            job = dict(self.vector_jobs.get(book_id) or {})
            job.update(changes)
            job["updated_at"] = self._now()
            self.vector_jobs[book_id] = job

    def _vector_rebuild_worker(self, book_id: str) -> None:
        logger = logging.getLogger("novel_rag.vectors")
        embedder = self.embedder
        try:
            chapters = self.db.query(
                "SELECT idx, title FROM chapters WHERE book_id=? ORDER BY idx", (book_id,)
            )
            total = len(chapters)
            cleared = index_store.clear_vectors(self.db, book_id)
            self._update_vector_job(
                book_id,
                state="running",
                chapters_total=total,
                indexed=0,
                message="正在重建向量…",
            )
            logger.info(
                "rebuild start: book=%s chapters=%d cleared=%d model=%s",
                book_id,
                total,
                cleared,
                embedder.model_id,
            )
            indexed = 0
            for pos, chapter in enumerate(chapters):
                self._update_vector_job(
                    book_id,
                    chapter_idx=int(chapter["idx"]),
                    chapter_no=pos + 1,
                )
                count = index_store.index_vectors(
                    self.db,
                    book_id,
                    embedder,
                    chapter_idx=int(chapter["idx"]),
                    batch=self.settings.embedding_batch,
                )
                indexed += count
                self._update_vector_job(book_id, indexed=indexed)
                if (pos + 1) % 10 == 0 or pos + 1 == total:
                    logger.info(
                        "rebuild progress: book=%s %d/%d chapters, %d vectors",
                        book_id,
                        pos + 1,
                        total,
                        indexed,
                    )
            self._update_vector_job(
                book_id,
                state="done",
                chapter_idx=None,
                chapter_no=None,
                indexed=indexed,
                message=f"重建完成：{total} 章 / {indexed} 段",
            )
            logger.info("rebuild done: book=%s chapters=%d vectors=%d", book_id, total, indexed)
        except Exception as exc:  # noqa: BLE001
            logger.exception("rebuild failed: book=%s", book_id)
            self._update_vector_job(book_id, state="failed", message=f"重建失败：{exc}")

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
        logging.getLogger("novel_rag.api").info(
            "provider saved: %s (%s)", name, base_url
        )
        return self.providers_payload()

    def delete_provider(self, name: str) -> dict:
        current = opencode.parse_custom(self.settings.custom_providers)
        if name not in current:
            raise NotFound("provider not found")
        del current[name]
        logging.getLogger("novel_rag.api").info("provider deleted: %s", name)
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


def _embedder_or_fallback(settings: Settings) -> tuple[embedding_mod.EmbeddingProvider, str]:
    """模型或运行时缺失时降级为占位向量，保证应用能起来（随后后台自动补齐模型）。

    显式选 bge 但模型文件缺失时，直接启动失败会让界面都打不开（用户无处修复），
    因此这里统一降级并记录告警，下载完成后再由 watch_embedding_model 切回 BGE。
    """
    try:
        return embedding_mod.from_settings(settings), ""
    except (FileNotFoundError, ImportError) as exc:
        logging.getLogger("novel_rag").warning("embedding 降级为占位向量：%s", exc)
        return embedding_mod.HashEmbedding(), str(exc)


def build_app(settings: Settings | None = None, db_path: str | None = None) -> App:
    settings = settings or get_settings()
    settings.ensure_home()
    db = open_db(db_path or settings.db_path)
    store = SettingsStore(db)
    store.apply(settings)
    gw = gateway_mod.from_settings(settings)
    emb, reason = _embedder_or_fallback(settings)
    pipeline = Pipeline(db=db, gateway=gw, embedder=emb, settings=settings)
    qa = QaService(db=db, gateway=gw, embedder=emb, settings=settings)

    app = App(
        settings=settings,
        db=db,
        gateway=gw,
        embedder=emb,
        pipeline=pipeline,
        qa=qa,
        store=store,
    )
    # 决策：模型缺失时自动下载（后台线程，不阻塞启动）；下完再切 BGE 并重建索引
    if reason:
        app._start_embedding_download()
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

    def _send(
        self,
        status: int,
        payload: Any,
        *,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
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
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    # -------------------------------------------------------- 静态前端（Web 部署）

    def _serve_static(self, method: str, raw_path: str) -> None:
        """同源托管 ``desktop/web``。

        Web 部署里页面和 /api 同一个源，静态资源不校验 token（浏览器也带不上
        自定义头）；API 分支仍在 ``_dispatch`` 里做 Bearer 校验。
        """
        root = self.app.settings.web_root_path
        if root is None:
            self._send(404, {"error": "not found"})
            return
        if method not in ("GET", "HEAD"):
            self._send(405, {"error": "method not allowed"})
            return

        rel = unquote(raw_path).lstrip("/") or "index.html"
        try:
            target = (root / rel).resolve()
            target.relative_to(root)  # 目录穿越（../）直接 404
        except (ValueError, OSError):
            self._send(404, {"error": "not found"})
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send(404, {"error": "not found"})
            return

        content_type = _content_type(target)
        # index.html 必须走协商缓存，否则前端更新后用户一直看到旧壳
        cache = "no-cache" if target.name == "index.html" else "public, max-age=3600"
        self._send(
            200,
            target.read_bytes(),
            content_type=content_type,
            extra_headers={"Cache-Control": cache},
        )

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
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        # 非 /api 路径 = 静态前端（Docker Web 部署同源托管）
        if not parts or parts[0] != "api":
            self._serve_static(method, parsed.path)
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
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

        # ------------------------------------------------------ 日志
        if tail == ["logs"] and method == "GET":
            source = (_first(query, "source") or "backend").lower()
            if source not in ("backend", "sidecar"):
                raise InvalidInput("source 只能是 backend | sidecar")
            limit = _opt_int(_first(query, "lines")) or 200
            limit = min(max(limit, 1), 2000)
            path = (
                self.app.settings.log_file
                if source == "backend"
                else self.app.settings.resolved_log_dir / "sidecar.log"
            )
            lines, size = _tail_file(path, limit)
            return {
                "source": source,
                "path": str(path),
                "exists": path.exists(),
                "size": size,
                "lines": lines,
            }

        # ------------------------------------------------------ 设置与模型
        if tail == ["settings"]:
            if method == "GET":
                payload = self.app.store.describe(self.app.settings)
                payload["effective"] = self.app.effective_summary()
                return payload
            if method == "PUT":
                changes = self._read_json()
                self.app.store.update(changes)
                self.app.refresh()
                logging.getLogger("novel_rag.settings").info(
                    "settings updated: %s", ", ".join(sorted(changes))
                )
                payload = self.app.store.describe(self.app.settings)
                payload["effective"] = self.app.effective_summary()
                return payload
            if method == "DELETE":
                cleared = list(self.app.store.get_all())
                self.app.store.update({key: "" for key in cleared})
                logging.getLogger("novel_rag.settings").info(
                    "settings reset: %s", ", ".join(sorted(cleared))
                )
                payload = self.app.store.describe(self.app.settings)
                self.app.refresh()
                payload["effective"] = self.app.effective_summary()
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
            api_log = logging.getLogger("novel_rag.api")
            if method == "GET":
                return self.app.pipeline.status(book_id)
            if method == "POST":
                if action == "start":
                    state = self.app.pipeline.start(book_id)
                    body = self._read_json() if self.headers.get("Content-Length") else {}
                    api_log.info("analysis start: book=%s", book_id)
                    self.app.start_analysis(book_id, max_chapters=_opt_int(body.get("max_chapters")))
                    return state
                if action == "pause":
                    api_log.info("analysis pause: book=%s", book_id)
                    return self.app.pipeline.pause(book_id)
                if action == "resume":
                    api_log.info("analysis resume: book=%s", book_id)
                    state = self.app.pipeline.resume(book_id)
                    self.app.start_analysis(book_id)
                    return state
                if action == "retry":
                    body = self._read_json()
                    chapter_idx = _opt_int(body.get("chapter_idx"))
                    if chapter_idx is None:
                        raise InvalidInput("需要 chapter_idx（从 0 开始）")
                    api_log.info("analysis retry: book=%s chapter=%s", book_id, chapter_idx)
                    state = self.app.pipeline.retry_from(book_id, chapter_idx)
                    self.app.start_analysis(book_id, max_chapters=_opt_int(body.get("max_chapters")))
                    return state
            raise NotFound()

        if tail == ["ask"] and method == "POST":
            body = self._read_json()
            question = str(body.get("question") or "")
            logging.getLogger("novel_rag.api").info(
                "ask: book=%s chars=%d history=%d",
                body.get("book_id") or "",
                len(question),
                len(body.get("history") or []),
            )
            return self.app.qa.ask(
                str(body.get("book_id") or ""),
                question,
                progress_seq=_opt_int(body.get("progress_seq")),
                spoiler_guard=_opt_bool(body.get("spoiler_guard")),
                history=body.get("history"),
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
            logging.getLogger("novel_rag.api").info("resplit: book=%s", book_id)
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

        if rest == ["vectors"] and method == "GET":
            return self.app.vector_status(book_id)

        if rest == ["vectors", "rebuild"] and method == "POST":
            return self.app.start_vector_rebuild(book_id)

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
                current = db.query_one(
                    "SELECT * FROM reading_progress WHERE book_id=?", (book_id,)
                )
                default_guard = 1 if self.app.settings.spoiler_guard_default else 0
                seq = _opt_int(body.get("progress_seq"))
                if seq is None:
                    seq = int(current["progress_seq"]) if current else 0
                cfi = body.get("progress_cfi")
                if cfi is None:
                    cfi = str(current["progress_cfi"]) if current else ""
                guard_value = _opt_bool(body.get("spoiler_guard"))
                if guard_value is None:
                    guard = int(current["spoiler_guard"]) if current else default_guard
                else:
                    guard = 1 if guard_value else 0
                db.execute(
                    "INSERT INTO reading_progress(book_id, progress_seq, progress_cfi, spoiler_guard, updated_at) "
                    "VALUES(?,?,?,?,datetime('now')) ON CONFLICT(book_id) DO UPDATE SET "
                    "progress_seq=excluded.progress_seq, progress_cfi=excluded.progress_cfi, "
                    "spoiler_guard=excluded.spoiler_guard, updated_at=excluded.updated_at",
                    (book_id, seq, str(cfi or ""), guard),
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

        logging.getLogger("novel_rag.import").info(
            "import: book=%s title=%s chapters=%d paragraphs=%d reused=%s",
            result.book.id,
            result.book.title,
            result.chapter_count,
            result.paragraph_count,
            result.reused,
        )

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


# 静态前端用到的类型（slim 镜像里 mimetypes 的库不一定全，显式兜一层）
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
}


def _content_type(path: Path) -> str:
    known = _CONTENT_TYPES.get(path.suffix.lower())
    if known:
        return known
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _tail_file(path: Path, limit: int) -> tuple[list[str], int]:
    """读取日志尾部 ``limit`` 行。日志由 RotatingFileHandler 控制在 5MB 内。"""
    if not path.exists():
        return [], 0
    try:
        size = path.stat().st_size
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], 0
    return text.splitlines()[-limit:], size


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
