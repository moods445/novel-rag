// 桌面壳前端入口：等待 sidecar 就绪 → 绑定各平面。
import { api, base64FromArrayBuffer, escapeHtml, setBackend } from './api.js';
import * as analysis from './analysis.js';
import * as chat from './chat.js';
import * as logs from './logs.js';
import * as settings from './settings.js';
import * as vectors from './vectors.js';

const $ = (id) => document.getElementById(id);

const els = {
  status: $('backend-status'),
  hint: $('backend-hint'),
  nav: $('nav'),
  themeToggle: $('theme-toggle'),
  bookList: $('book-list'),
  fileInput: $('file-input'),
  importPanel: $('import-panel'),
  importStage: $('import-stage'),
  importPercent: $('import-percent'),
  importBar: $('import-bar'),
  importDetail: $('import-detail'),
  readerTitle: $('reader-title'),
  readerProgress: $('reader-progress'),
  readerEmpty: $('reader-empty'),
  viewer: $('viewer'),
  tocSelect: $('toc-select'),
  readBody: document.querySelector('#view-read .read-body'),
  chatToggle: $('btn-chat-toggle'),
  chatClose: $('btn-chat-close'),
  chatLog: $('chat-log'),
  chatQuestion: $('chat-question'),
  chatSend: $('chat-send'),
  chatSpoilerGuard: $('chat-spoiler-guard'),
  question: $('question'),
  btnAsk: $('btn-ask'),
  answer: $('answer'),
  citations: $('citations'),
  coverageHint: $('coverage-hint'),
  taskHint: $('task-hint'),
  taskBar: $('task-bar'),
  btnStart: $('btn-start'),
  btnPause: $('btn-pause'),
  relations: $('relations'),
  timeline: $('timeline'),
  spoilerGuard: $('spoiler-guard'),
  // settings
  ner: $('set-ner'),
  split: $('set-split'),
  embed: $('set-embed'),
  embedStatus: $('embed-status'),
  embedBar: $('embed-bar'),
  embedEndpoint: $('set-embed-endpoint'),
  download: $('btn-embed-download'),
  llmProvider: $('set-llm-provider'),
  llmModel: $('set-llm-model'),
  llmEffective: $('llm-effective'),
  llmConfigPath: $('llm-config-path'),
  proxy: $('set-proxy'),
  proxyHint: $('proxy-hint'),
  proxySave: $('btn-proxy-save'),
  providerName: $('provider-name'),
  providerBaseUrl: $('provider-base-url'),
  providerApiKey: $('provider-api-key'),
  providerModels: $('provider-models'),
  providerHint: $('provider-hint'),
  providerAdd: $('btn-provider-add'),
  providerList: $('provider-list'),
  effective: $('effective'),
  resplitPattern: $('resplit-pattern'),
  resplitHint: $('resplit-hint'),
  resplit: $('btn-resplit'),
  // vectors
  vectorBook: $('vector-book'),
  vectorRefresh: $('btn-vector-refresh'),
  vectorContinue: $('btn-vector-continue'),
  vectorPause: $('btn-vector-pause'),
  vectorRebuild: $('btn-vector-rebuild'),
  vectorState: $('vector-state'),
  vectorBar: $('vector-bar'),
  vectorStats: $('vector-stats'),
  vectorHint: $('vector-hint'),
  vectorChapterHint: $('vector-chapter-hint'),
  vectorChapters: $('vector-chapters'),
  // logs
  logSource: $('log-source'),
  logLines: $('log-lines'),
  logAuto: $('log-auto'),
  logRefresh: $('btn-log-refresh'),
  logMeta: $('log-meta'),
  logView: $('log-view'),
};

const cards = new Map();

const state = {
  logPath: '',
  books: [],
  book: null,
  chapters: [],
  progressSeq: 1,
  reader: null,
  view: 'shelf',
};

// ---------------------------------------------------------------- 启动

const THEME_KEY = 'novelrag-theme';

function currentTheme() {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* 隐私模式下不可写，忽略 */
  }
  if (els.themeToggle) {
    els.themeToggle.textContent = theme === 'light' ? '切换到暗色主题' : '切换到亮色主题';
  }
  if (state.reader && state.reader.applyTheme) state.reader.applyTheme(theme);
}

function bindTheme() {
  applyTheme(currentTheme());
  els.themeToggle?.addEventListener('click', () => {
    applyTheme(currentTheme() === 'light' ? 'dark' : 'light');
  });
}

// ---------------------------------------------------------------- 阅读页对话面板

const CHAT_PANEL_KEY = 'novelrag-read-chat';

function chatPanelHidden() {
  try {
    return localStorage.getItem(CHAT_PANEL_KEY) === 'hidden';
  } catch {
    return false;
  }
}

function applyChatPanel(hidden, persist = true) {
  els.readBody?.classList.toggle('chat-hidden', hidden);
  if (els.chatToggle) {
    els.chatToggle.textContent = hidden ? '显示对话' : '隐藏对话';
    els.chatToggle.title = hidden ? '展开右侧对话面板' : '收起右侧对话面板';
  }
  if (!persist) return;
  try {
    localStorage.setItem(CHAT_PANEL_KEY, hidden ? 'hidden' : 'shown');
  } catch {
    /* 隐私模式下不可写，忽略 */
  }
}

function bindChatPanel() {
  applyChatPanel(chatPanelHidden(), false);
  const toggle = () => applyChatPanel(!els.readBody?.classList.contains('chat-hidden'));
  els.chatToggle?.addEventListener('click', toggle);
  els.chatClose?.addEventListener('click', () => applyChatPanel(true));
}

// ---------------------------------------------------------------- 阅读键盘

function isTypingTarget(target) {
  if (!target || typeof target.tagName !== 'string') return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

/**
 * 键盘绑两层：
 *  - 主文档 capture 阶段（比 foliate / 输入法更早拿到事件，避免被别人 preventDefault 抢走）；
 *  - 正文 iframe 的 document（foliate 每个章节一个 iframe，见 reader.js 的 bindKeysToDoc）。
 * 阅读页没有书的时候也给个提示，免得以为"刷新后键盘坏了"。
 */
function bindReaderKeys() {
  window.addEventListener(
    'keydown',
    (event) => {
      if (state.view !== 'read') return;
      if (isTypingTarget(event.target)) return;
      if (!state.book || !state.reader) {
        if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
          els.status.textContent = '先点书架里的一本书，再按 ← → / ↑ ↓ 翻屏与滚动';
        }
        return;
      }
      state.reader.handleKeyEvent(event);
    },
    true
  );
}

function handleSpoilerChange(value) {
  analysis.setSpoilerGuard(value);
  chat.setSpoilerGuard(value);
  persistSpoilerGuard(value);
}

async function persistSpoilerGuard(value) {
  if (!state.book) return;
  try {
    await api.put(`/api/books/${state.book.id}/progress`, { spoiler_guard: value });
  } catch {
    /* 进度丢失不致命 */
  }
}

const TOKEN_KEY = 'novelrag-token';

/** 浏览器/Web 模式：URL 里的 token 记住一次，之后不用每次带。 */
function readToken(params) {
  const fromHash = params.get('token');
  if (fromHash) {
    try {
      localStorage.setItem(TOKEN_KEY, fromHash);
    } catch {
      /* 隐私模式忽略 */
    }
    return fromHash;
  }
  try {
    return localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

async function backendInfo() {
  const invoke = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
  if (invoke) return invoke('backend_info');
  // 浏览器调试：http://127.0.0.1:8088/#port=8801&token=dev
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  const token = readToken(params);
  if (params.get('port')) {
    return { ready: true, port: Number(params.get('port')), token, mode: 'browser', home: '' };
  }
  // Web 部署（Docker）：页面由后端同源托管，/api 就在当前站点，没有 PORT 行可读
  return { ready: true, port: null, token, mode: 'web', home: '', log: '' };
}

/** 同源 Web 模式只能靠一次 /api/health 判断后端是否可用。 */
async function probeWebBackend(info) {
  const headers = info.token ? { Authorization: `Bearer ${info.token}` } : {};
  try {
    const res = await fetch('/api/health', { headers, cache: 'no-store' });
    if (res.status === 401) return 'auth';
    return res.ok ? 'ok' : 'down';
  } catch {
    return 'down';
  }
}

async function waitForBackend() {
  // 120s：PyInstaller onefile 首次解压 + onnxruntime 导入在 Intel Mac 上要十几秒
  for (let i = 0; i < 600; i += 1) {
    let info = null;
    try {
      info = await backendInfo();
    } catch {
      /* 壳还没建好，继续等 */
    }
    if (info && info.log) state.logPath = info.log;
    if (info && info.mode === 'web') {
      const probe = await probeWebBackend(info);
      if (probe === 'ok') return info;
      // token 不对就别死等 120s，直接把怎么办告诉用户
      if (probe === 'auth') {
        throw new Error(
          '后端要求 token：请用 /#token=xxx 打开本页，或让服务端设置 NOVEL_RAG_DISABLE_AUTH=1'
        );
      }
    } else if (info && info.ready && info.port) {
      return info;
    }
    // 子进程已经死了就别再等到超时，把原因直接甩出来
    if (info && info.error) throw new Error(info.error);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(
    `本地后端启动超时（120s）${state.logPath ? `，日志：${state.logPath}` : ''}`
  );
}

/** 浏览器/Web 模式下 hash 就是配置（port/token）：改了要重新走一遍启动流程。 */
function bindHashReload() {
  if (window.__TAURI__) return;
  window.addEventListener('hashchange', () => location.reload());
}

async function boot() {
  bindTheme();
  bindHashReload();
  els.status.textContent = '正在启动本地后端…';
  try {
    const info = await waitForBackend();
    setBackend(info);
    if (info.mode === 'web') {
      els.status.textContent = '后端就绪（同源 Web）';
      els.hint.textContent = info.token
        ? 'Web 部署 · 已用 URL 里的 token 鉴权'
        : 'Web 部署 · 服务端未启用 token';
      els.hint.title = '';
    } else {
      els.status.textContent = `后端就绪 :${info.port}`;
      els.hint.textContent = info.mode === 'python-dev'
        ? `开发模式（源码）· 日志：${info.log}`
        : `日志：${info.log}`;
      els.hint.title = info.log;
    }
  } catch (error) {
    els.status.classList.add('bad');
    els.status.textContent = `后端启动失败：${error.message}`;
    if (state.logPath) {
      els.hint.textContent = `日志：${state.logPath}`;
      els.hint.title = state.logPath;
    }
    return;
  }

  bindNav();
  bindShelf();
  bindChatPanel();
  bindReaderKeys();
  bindReaderToolbar();
  analysis.init({
    question: els.question,
    btnAsk: els.btnAsk,
    answer: els.answer,
    citations: els.citations,
    coverageHint: els.coverageHint,
    taskHint: els.taskHint,
    taskBar: els.taskBar,
    btnStart: els.btnStart,
    btnPause: els.btnPause,
    relations: els.relations,
    timeline: els.timeline,
    spoilerGuard: els.spoilerGuard,
    onSpoilerChange: handleSpoilerChange,
  });
  chat.init({
    log: els.chatLog,
    question: els.chatQuestion,
    send: els.chatSend,
    spoilerGuard: els.chatSpoilerGuard,
    onSpoilerChange: handleSpoilerChange,
  });
  settings.init({
    ner: els.ner,
    split: els.split,
    embed: els.embed,
    embedStatus: els.embedStatus,
    embedBar: els.embedBar,
    embedEndpoint: els.embedEndpoint,
    download: els.download,
    llmProvider: els.llmProvider,
    llmModel: els.llmModel,
    llmEffective: els.llmEffective,
    llmConfigPath: els.llmConfigPath,
    proxy: els.proxy,
    proxyHint: els.proxyHint,
    proxySave: els.proxySave,
    providerName: els.providerName,
    providerBaseUrl: els.providerBaseUrl,
    providerApiKey: els.providerApiKey,
    providerModels: els.providerModels,
    providerHint: els.providerHint,
    providerAdd: els.providerAdd,
    providerList: els.providerList,
    effective: els.effective,
    resplitPattern: els.resplitPattern,
    resplitHint: els.resplitHint,
    resplit: els.resplit,
    getBookId: () => (state.book ? state.book.id : null),
  });
  vectors.init({
    bookSelect: els.vectorBook,
    refreshBtn: els.vectorRefresh,
    continueBtn: els.vectorContinue,
    pauseBtn: els.vectorPause,
    rebuildBtn: els.vectorRebuild,
    state: els.vectorState,
    bar: els.vectorBar,
    stats: els.vectorStats,
    hint: els.vectorHint,
    chapterHint: els.vectorChapterHint,
    chapters: els.vectorChapters,
  });
  logs.init({
    refreshBtn: els.logRefresh,
    source: els.logSource,
    lines: els.logLines,
    auto: els.logAuto,
    meta: els.logMeta,
    view: els.logView,
  });

  await Promise.all([loadBooks(), settings.load().catch(() => {})]);
  await restoreLastBook();
  setInterval(() => {
    if (state.view === 'analysis') analysis.refreshTask();
    if (state.view === 'shelf') refreshShelf();
  }, 4000);
}

const LAST_BOOK_KEY = 'novelrag-last-book';

function rememberBook(id) {
  try {
    if (id) localStorage.setItem(LAST_BOOK_KEY, id);
    else localStorage.removeItem(LAST_BOOK_KEY);
  } catch {
    /* 隐私模式下不可写，忽略 */
  }
}

function lastBookId() {
  try {
    return localStorage.getItem(LAST_BOOK_KEY) || '';
  } catch {
    return '';
  }
}

/**
 * 刷新页面后自动回到上次读的那本书（位置由服务端 progress_cfi 恢复）。
 * 书被删了/解析失败就静默退回书架，不能因此把启动流程卡住。
 */
async function restoreLastBook() {
  const id = lastBookId();
  if (!id) return;
  const book = state.books.find((b) => b.id === id);
  if (!book) {
    rememberBook('');
    return;
  }
  const ok = await selectBook(book);
  if (!ok) {
    rememberBook('');
    return;
  }
  if (!els.status.classList.contains('bad')) {
    els.status.textContent = `已恢复上次阅读：${book.title}（随时可刷新页面）`;
  }
}

// ---------------------------------------------------------------- 导航

function bindNav() {
  els.nav.addEventListener('click', (event) => {
    const button = event.target.closest('.nav-item');
    if (!button) return;
    showView(button.dataset.view);
  });
}

function showView(name) {
  state.view = name;
  document.querySelectorAll('.nav-item').forEach((node) => {
    node.classList.toggle('active', node.dataset.view === name);
  });
  document.querySelectorAll('.view').forEach((node) => {
    node.classList.toggle('active', node.id === `view-${name}`);
  });
  if (name === 'analysis') {
    analysis.refreshTask();
    analysis.refreshGraph();
  }
  vectors.setVisible(name === 'vectors');
  logs.setVisible(name === 'logs');
}

// ---------------------------------------------------------------- 书架

function bindShelf() {
  els.fileInput.addEventListener('change', async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    els.fileInput.value = '';
    await importFile(file);
  });
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

/**
 * 导入进度条。四段：读文件 → 编码 → 上传（真实百分比）→ 服务端分章（不确定态）。
 * 后端是一次性同步解析，第二段拿不到真实百分比，所以只做"还在动"的不确定态。
 */
function setImport({ stage, percent = 0, detail = '', bad = false, indeterminate = false }) {
  const panel = els.importPanel;
  if (!panel) return;
  panel.hidden = false;
  panel.classList.toggle('bad', Boolean(bad));
  if (els.importStage) els.importStage.textContent = stage;
  if (els.importPercent) {
    els.importPercent.textContent = indeterminate ? '处理中' : `${Math.round(percent)}%`;
  }
  if (els.importBar) {
    els.importBar.classList.toggle('indeterminate', Boolean(indeterminate));
    if (!indeterminate) els.importBar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  }
  if (els.importDetail) els.importDetail.textContent = detail;
}

async function importFile(file) {
  els.status.classList.remove('bad');
  els.status.textContent = `导入 ${file.name}…`;
  const size = formatBytes(file.size);
  try {
    setImport({ stage: `读取 ${file.name}（${size}）…`, percent: 4, detail: '文件只在本机读，编码后一次性上传给本地后端。' });
    const buffer = await file.arrayBuffer();

    setImport({ stage: '编码中…', percent: 12, detail: `${size} → base64，大文件需要几秒。` });
    // 让浏览器先把进度条画出来，再跑同步的 base64 编码
    await new Promise((resolve) => setTimeout(resolve, 0));
    const content_base64 = base64FromArrayBuffer(buffer);

    setImport({ stage: '上传中…', percent: 20, detail: 'POST /api/books' });
    const result = await api.postWithProgress(
      '/api/books',
      {
        filename: file.name,
        content_base64,
        title: file.name.replace(/\.[^.]+$/, ''),
      },
      (ratio) => {
        // 上传占 20%~85%，剩下的是服务端分章解析
        setImport({
          stage: '上传中…',
          percent: 20 + ratio * 65,
          detail: `已传 ${formatBytes(file.size * ratio)} / ${size}`,
        });
      }
    );

    setImport({
      stage: '服务端分章中…',
      percent: 90,
      indeterminate: true,
      detail: '上传已完成，正在识别章节；这一步耗时取决于文件大小。',
    });

    els.status.textContent = `已导入：${result.chapter_count} 章 / ${result.paragraph_count} 段`;
    await loadBooks();
    const book = state.books.find((b) => b.id === result.book.id);
    if (book) await selectBook(book);
    setImport({
      stage: '导入完成',
      percent: 100,
      detail: `${result.chapter_count} 章 / ${result.paragraph_count} 段，已开始后台建知识库。可以刷新页面，阅读位置已存服务端。`,
    });
    setTimeout(() => {
      if (els.importPanel) els.importPanel.hidden = true;
    }, 6000);
  } catch (error) {
    els.status.classList.add('bad');
    els.status.textContent = `导入失败：${error.message}`;
    setImport({ stage: '导入失败', percent: 100, bad: true, detail: error.message });
  }
}

async function loadBooks() {
  const data = await api.get('/api/books');
  state.books = data.books || [];
  cards.clear();
  els.bookList.innerHTML = '';
  if (!state.books.length) {
    els.bookList.innerHTML = '<div class="empty">还没有书，点右上角「导入小说」。</div>';
    vectors.setBooks(state.books, state.book?.id);
    return;
  }
  for (const book of state.books) {
    els.bookList.appendChild(bookCard(book));
  }
  vectors.setBooks(state.books, state.book?.id);
  await refreshShelf();
}

function bookCard(book) {
  const card = document.createElement('div');
  card.className = 'book-card';
  card.innerHTML = `
    <h3>${escapeHtml(book.title)}</h3>
    <div class="meta">${escapeHtml(book.author || '未署名')} · ${book.chapter_count} 章 · ${book.max_seq} 段</div>
    <div class="mini-bar"><i style="width:0%"></i></div>
    <div class="meta" data-role="coverage">分析中…</div>`;
  card.addEventListener('click', () => selectBook(book));
  cards.set(book.id, card);
  applyCoverage(card, {});
  return card;
}

function applyCoverage(card, coverage) {
  const total = coverage.total || 0;
  const done = coverage.done || 0;
  const percent = total ? Math.round((done / total) * 100) : 0;
  card.querySelector('.mini-bar > i').style.width = `${percent}%`;
  card.querySelector('[data-role="coverage"]').textContent = total
    ? `知识库 ${done}/${total} 章（${percent}%）`
    : '分析中…';
}

/** 书架卡片需要轮询：知识库是后台异步建的，静态渲染会一直显示 0/N。 */
async function refreshShelf() {
  await Promise.all(
    state.books.map(async (book) => {
      const card = cards.get(book.id);
      if (!card) return;
      try {
        const data = await api.get(`/api/books/${book.id}/coverage`);
        applyCoverage(card, data.coverage || {});
      } catch {
        /* 忽略单本失败 */
      }
    })
  );
}

async function selectBook(book) {
  try {
    state.book = book;
    const [chapters, progress] = await Promise.all([
      api.get(`/api/books/${book.id}/chapters`),
      api.get(`/api/books/${book.id}/progress`),
    ]);
    state.chapters = chapters.chapters || [];
    state.progressSeq = progress.progress_seq || 1;
    els.readerTitle.textContent = book.title;
    els.readerEmpty.style.display = 'none';
    els.readerProgress.textContent = `进度 seq ${state.progressSeq} · ${state.chapters.length} 章`;

    analysis.setBook(book, state.progressSeq, progress.spoiler_guard !== 0);
    chat.setBook(book);
    chat.setProgress(state.progressSeq);
    chat.setSpoilerGuard(progress.spoiler_guard !== 0);
    vectors.setBooks(state.books, book.id);
    await openReader(book, progress);
    fillToc();
    showView('read');
    rememberBook(book.id);
    return true;
  } catch (error) {
    els.status.classList.add('bad');
    els.status.textContent = `打开失败：${error.message}`;
    return false;
  }
}

async function openReader(book, progress) {
  try {
    if (!state.reader) {
      state.reader = await import('./reader.js');
      await state.reader.ensureView(els.viewer);
    }
  } catch (error) {
    els.readerEmpty.style.display = 'flex';
    els.readerEmpty.innerHTML = `阅读引擎未就绪（foliate-js 未拉取）：<br/>${escapeHtml(error.message)}<br/><br/>先跑 <code>python3 desktop/scripts/prepare_vendor.py</code>`;
    throw error;
  }
  state.reader.setBook(book.id, state.chapters);
  // 续读：服务端存的是 CFI（渲染坐标），换设备/刷新都能落回原处
  await state.reader.open(book, {
    lastCfi: (progress && progress.progress_cfi) || '',
    onRelocate: (detail, chapter) => {
      if (chapter && chapter.seq_end) {
        state.progressSeq = chapter.seq_end;
        analysis.setProgress(chapter.seq_end);
        chat.setProgress(chapter.seq_end);
        els.readerProgress.textContent = `第${chapter.idx + 1}章 · seq ${chapter.seq_end}`;
        if (els.tocSelect) els.tocSelect.value = chapter.nav_href || '';
      }
    },
  });
}

function fillToc() {
  if (!els.tocSelect) return;
  els.tocSelect.innerHTML = '';
  const items = state.reader ? state.reader.toc() : [];
  const source = items.length
    ? items
    : state.chapters.map((c) => ({ label: c.title, href: c.nav_href, depth: 0 }));
  for (const item of source) {
    const option = document.createElement('option');
    option.value = item.href;
    option.textContent = `${'　'.repeat(Math.min(item.depth || 0, 2))}${item.label}`;
    els.tocSelect.appendChild(option);
  }
}

function bindReaderToolbar() {
  els.tocSelect.addEventListener('change', () => {
    if (state.reader) state.reader.goTo(els.tocSelect.value);
  });
  $('btn-prev').addEventListener('click', () => state.reader && state.reader.goToChapter(-1));
  $('btn-next').addEventListener('click', () => state.reader && state.reader.goToChapter(1));
  $('btn-highlight').addEventListener('click', saveHighlight);
}

async function saveHighlight() {
  if (!state.reader || !state.book) return;
  const cfi = state.reader.currentCfi();
  const text = state.reader.selectionText() || '';
  if (!cfi) return;
  try {
    await api.post(`/api/books/${state.book.id}/notes`, {
      id: `n-${Date.now()}`,
      kind: text ? 'highlight' : 'bookmark',
      cfi,
      seq: state.progressSeq,
      text: text.slice(0, 500),
    });
    els.status.textContent = text ? '已保存划线' : '已添加书签';
  } catch (error) {
    els.status.textContent = `保存失败：${error.message}`;
  }
}

boot();
