"""SQLite 连接与迁移。

只依赖标准库 sqlite3；FTS5 使用 trigram tokenizer（中文需要子串匹配，
unicode61 会把整句当成一个 token）。

**线程模型**：后端是 ThreadingHTTPServer，请求并发。sqlite3.Connection
不能跨线程安全共享（会让不同线程的语句互相踩，实测表现为 SELECT 返回
错误结果）。因此每个线程持有自己的连接（WAL 下多读单写、busy_timeout 兜底）。
"""

from __future__ import annotations

import sqlite3
import threading
from importlib import resources
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = "3"

# v1 -> v2/v3：CREATE TABLE IF NOT EXISTS 不会补列，旧库单独 ALTER。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("books", "raw_text", "TEXT NOT NULL DEFAULT ''"),
)


class Database:
    """一个 Database 对应一个数据库文件；连接按线程懒创建。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._memory = str(path) == ":memory:"
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._lock = threading.RLock()
        if not self._memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        _ = self.conn  # 主线程先建一个，保证迁移有落点

    # ------------------------------------------------------------- 连接

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conns.append(conn)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._memory:
            # 内存库不能跨连接共享，退化为单连接（测试与临时脚本用）
            with self._lock:
                return self._conns[0] if self._conns else self._connect()
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            for conn in self._conns:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()

    # ------------------------------------------------------------- 迁移

    def migrate(self) -> None:
        sql = _schema_sql()
        self.conn.executescript(sql)
        for table, column, decl in _ADDED_COLUMNS:
            self._add_column_if_missing(table, column, decl)
        self.conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SCHEMA_VERSION,),
        )

    def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(r["name"] == column for r in rows):
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ------------------------------------------------------------- 便捷查询

    def tx(self):
        """显式事务上下文（isolation_level=None 表示手动管理）。"""
        return _Tx(self.conn)

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()


def _schema_sql() -> str:
    """读取 schema.sql。

    三种运行形态都要能读到：源码、wheel/editable、PyInstaller onefile。
    打包时漏带数据文件会在建库阶段直接 FileNotFoundError，
    所以这里优先走包资源，再退回文件路径。
    """
    try:
        return resources.files("novel_rag.db").joinpath("schema.sql").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        if SCHEMA_PATH.exists():
            return SCHEMA_PATH.read_text(encoding="utf-8")
        raise FileNotFoundError(
            f"schema.sql 缺失（{SCHEMA_PATH}）。打包时需 --collect-data novel_rag"
        ) from exc


class _Tx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self._conn.execute("BEGIN IMMEDIATE")
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")


def open_db(path: str | Path) -> Database:
    db = Database(path)
    db.migrate()
    return db
