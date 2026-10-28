-- AI 小说阅读器 · 存储 schema（版本 1）
-- 决策依据见 docs/architecture.md 第 24 条决策与第四节契约。
-- 关键约束：
--   chapters/paragraphs 是原文真相，段落必须是章节正文的精确切片。
--   paragraph.cfi 是渲染坐标系，paragraph.seq 是业务坐标系（双轴）。
--   relations 表 append-only + version：valid_to_seq 延迟确定，禁止 load-modify-save。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');

-- ---------------------------------------------------------------- 原文

CREATE TABLE IF NOT EXISTS books (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  author        TEXT NOT NULL DEFAULT '',
  source_name   TEXT NOT NULL DEFAULT '',
  source_sha256 TEXT NOT NULL,
  chapter_fp    TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  meta          TEXT NOT NULL DEFAULT '{}',
  -- 原文全文：重新分章（人工校正）时唯一可信的输入源
  raw_text      TEXT NOT NULL DEFAULT '',
  UNIQUE (source_sha256, chapter_fp)
);

CREATE TABLE IF NOT EXISTS chapters (
  book_id      TEXT NOT NULL,
  idx          INTEGER NOT NULL,
  title        TEXT NOT NULL,
  fp           TEXT NOT NULL,
  spine_index  INTEGER NOT NULL DEFAULT 0,
  nav_href     TEXT NOT NULL DEFAULT '',
  char_start   INTEGER NOT NULL,
  char_end     INTEGER NOT NULL,
  body         TEXT NOT NULL,
  split_source TEXT NOT NULL DEFAULT 'rule',
  confidence   REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (book_id, idx)
);

CREATE TABLE IF NOT EXISTS paragraphs (
  id          INTEGER PRIMARY KEY,
  book_id     TEXT NOT NULL,
  chapter_idx INTEGER NOT NULL,
  seq         INTEGER NOT NULL,
  para_idx    INTEGER NOT NULL,
  char_off    INTEGER NOT NULL,
  cfi         TEXT NOT NULL,
  text        TEXT NOT NULL,
  UNIQUE (book_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_para_chapter ON paragraphs(book_id, chapter_idx, para_idx);

-- ---------------------------------------------------------------- 索引

CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts USING fts5(
  text,
  tokenize = "trigram"
);

CREATE TABLE IF NOT EXISTS vectors (
  book_id  TEXT NOT NULL,
  seq      INTEGER NOT NULL,
  model_id TEXT NOT NULL,
  dim      INTEGER NOT NULL,
  vec      BLOB NOT NULL,
  PRIMARY KEY (book_id, seq, model_id)
);

-- ---------------------------------------------------------------- 抽取产物

CREATE TABLE IF NOT EXISTS chapter_coverage (
  book_id     TEXT NOT NULL,
  chapter_idx INTEGER NOT NULL,
  state       TEXT NOT NULL,        -- pending | running | done | failed
  errors      INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (book_id, chapter_idx)
);

CREATE TABLE IF NOT EXISTS chapter_facts (
  book_id     TEXT NOT NULL,
  chapter_idx INTEGER NOT NULL,
  chapter_fp  TEXT NOT NULL,
  payload     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (book_id, chapter_idx, chapter_fp)
);

CREATE TABLE IF NOT EXISTS fact_rejects (
  id          INTEGER PRIMARY KEY,
  book_id     TEXT NOT NULL,
  chapter_idx INTEGER NOT NULL,
  kind        TEXT NOT NULL,
  reason      TEXT NOT NULL,
  payload     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS entities (
  id             TEXT PRIMARY KEY,
  book_id        TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'person',
  first_seq      INTEGER NOT NULL DEFAULT 0,
  meta           TEXT NOT NULL DEFAULT '{}',
  UNIQUE (book_id, canonical_name)
);

CREATE TABLE IF NOT EXISTS entity_aliases (
  id           INTEGER PRIMARY KEY,
  book_id      TEXT NOT NULL,
  entity_id    TEXT NOT NULL,
  alias        TEXT NOT NULL,
  evidence_seq INTEGER,
  evidence_cfi TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL DEFAULT 'fact',
  UNIQUE (book_id, alias)
);

CREATE TABLE IF NOT EXISTS relations (
  id             INTEGER PRIMARY KEY,
  book_id        TEXT NOT NULL,
  a_entity       TEXT NOT NULL,
  b_entity       TEXT NOT NULL,
  rel_type       TEXT NOT NULL,
  valid_from_seq INTEGER NOT NULL,
  valid_to_seq   INTEGER,
  version        INTEGER NOT NULL DEFAULT 1,
  evidence_seq   INTEGER,
  evidence_cfi   TEXT NOT NULL DEFAULT '',
  meta           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_rel_pair ON relations(book_id, a_entity, b_entity, version);

CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY,
  book_id      TEXT NOT NULL,
  chapter_idx  INTEGER NOT NULL,
  seq          INTEGER NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'plot',
  summary      TEXT NOT NULL,
  actors       TEXT NOT NULL DEFAULT '[]',
  evidence_cfi TEXT NOT NULL DEFAULT '',
  UNIQUE (book_id, chapter_idx, summary)
);

CREATE TABLE IF NOT EXISTS chapter_summaries (
  book_id     TEXT NOT NULL,
  chapter_idx INTEGER NOT NULL,
  summary     TEXT NOT NULL,
  PRIMARY KEY (book_id, chapter_idx)
);

-- ---------------------------------------------------------------- 任务与阅读态

CREATE TABLE IF NOT EXISTS analysis_tasks (
  book_id    TEXT PRIMARY KEY,
  state      TEXT NOT NULL,          -- queued | running | paused | failed | done
  cursor     INTEGER NOT NULL DEFAULT 0,
  total      INTEGER NOT NULL DEFAULT 0,
  message    TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reading_progress (
  book_id      TEXT PRIMARY KEY,
  progress_seq INTEGER NOT NULL DEFAULT 0,
  progress_cfi TEXT NOT NULL DEFAULT '',
  spoiler_guard INTEGER NOT NULL DEFAULT 1,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  id          TEXT PRIMARY KEY,
  book_id     TEXT NOT NULL,
  kind        TEXT NOT NULL,         -- bookmark | highlight | note
  cfi         TEXT NOT NULL,
  seq         INTEGER,
  chapter_idx INTEGER,
  text        TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notes_book ON notes(book_id, seq);

-- 实体归一冲突审计（LLM 裁决的每一次决策都要可回溯）
CREATE TABLE IF NOT EXISTS resolution_audit (
  id          INTEGER PRIMARY KEY,
  book_id     TEXT NOT NULL,
  chapter_idx INTEGER NOT NULL,
  alias       TEXT NOT NULL,
  candidates  TEXT NOT NULL DEFAULT '[]',
  decision    TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL DEFAULT 'rule',   -- rule | llm | skipped
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_book ON resolution_audit(book_id, chapter_idx);

-- ---------------------------------------------------------------- 界面可改设置
CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ---------------------------------------------------------------- 预留扩展槽
-- 百科 / 势力 / 世界地图 / 二审（决策 #7：只留槽，不实现）
CREATE TABLE IF NOT EXISTS extensions (
  book_id TEXT NOT NULL,
  key     TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (book_id, key)
);
