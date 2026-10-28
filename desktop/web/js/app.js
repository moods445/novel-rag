// 桌面壳前端入口：等待 sidecar 就绪 → 绑定四个平面。
import { api, base64FromArrayBuffer, escapeHtml, setBackend } from './api.js';
import * as analysis from './analysis.js';
import * as settings from './settings.js';

const $ = (id) => document.getElementById(id);

const els = {
  status: $('backend-status'),
  hint: $('backend-hint'),
  nav: $('nav'),
  bookList: $('book-list'),
  fileInput: $('file-input'),
  readerTitle: $('reader-title'),
  readerProgress: $('reader-progress'),
  readerEmpty: $('reader-empty'),
  viewer: $('viewer'),
  tocSelect: $('toc-select'),
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
  download: $('btn-embed-download'),
  llmProvider: $('set-llm-provider'),
  llmModel: $('set-llm-model'),
  llmConfigPath: $('llm-config-path'),
  effective: $('effective'),
  resplitPattern: $('resplit-pattern'),
  resplitHint: $('resplit-hint'),
  resplit: $('btn-resplit'),
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

async function backendInfo() {
  const invoke = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
  if (invoke) return invoke('backend_info');
  // 浏览器调试：http://localhost:5173/#port=8799&token=xxx
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (params.get('port')) {
    return { ready: true, port: Number(params.get('port')), token: params.get('token') || '', mode: 'browser', home: '' };
  }
  return { ready: false };
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
    if (info && info.ready && info.port) return info;
    // 子进程已经死了就别再等到超时，把原因直接甩出来
    if (info && info.error) throw new Error(info.error);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(
    `本地后端启动超时（120s）${state.logPath ? `，日志：${state.logPath}` : ''}`
  );
}

async function boot() {
  els.status.textContent = '正在启动本地后端…';
  try {
    const info = await waitForBackend();
    setBackend(info);
    els.status.textContent = `后端就绪 :${info.port}`;
    els.hint.textContent = info.mode === 'python-dev'
      ? `开发模式（源码）· 日志：${info.log}`
      : `日志：${info.log}`;
    els.hint.title = info.log;
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
  });
  settings.init({
    ner: els.ner,
    split: els.split,
    embed: els.embed,
    embedStatus: els.embedStatus,
    embedBar: els.embedBar,
    download: els.download,
    llmProvider: els.llmProvider,
    llmModel: els.llmModel,
    llmConfigPath: els.llmConfigPath,
    effective: els.effective,
    resplitPattern: els.resplitPattern,
    resplitHint: els.resplitHint,
    resplit: els.resplit,
    getBookId: () => (state.book ? state.book.id : null),
  });

  await Promise.all([loadBooks(), settings.load().catch(() => {})]);
  setInterval(() => {
    if (state.view === 'analysis') analysis.refreshTask();
    if (state.view === 'shelf') refreshShelf();
  }, 4000);
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

async function importFile(file) {
  els.status.textContent = `导入 ${file.name}…`;
  try {
    const buffer = await file.arrayBuffer();
    const result = await api.post('/api/books', {
      filename: file.name,
      content_base64: base64FromArrayBuffer(buffer),
      title: file.name.replace(/\.[^.]+$/, ''),
    });
    els.status.textContent = `已导入：${result.chapter_count} 章 / ${result.paragraph_count} 段`;
    await loadBooks();
    const book = state.books.find((b) => b.id === result.book.id);
    if (book) await selectBook(book);
  } catch (error) {
    els.status.classList.add('bad');
    els.status.textContent = `导入失败：${error.message}`;
  }
}

async function loadBooks() {
  const data = await api.get('/api/books');
  state.books = data.books || [];
  cards.clear();
  els.bookList.innerHTML = '';
  if (!state.books.length) {
    els.bookList.innerHTML = '<div class="empty">还没有书，点右上角「导入小说」。</div>';
    return;
  }
  for (const book of state.books) {
    els.bookList.appendChild(bookCard(book));
  }
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
    await openReader(book);
    fillToc();
    showView('read');
  } catch (error) {
    els.status.classList.add('bad');
    els.status.textContent = `打开失败：${error.message}`;
  }
}

async function openReader(book) {
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
  await state.reader.open(book, {
    onRelocate: (detail, chapter) => {
      if (chapter && chapter.seq_end) {
        state.progressSeq = chapter.seq_end;
        analysis.setProgress(chapter.seq_end);
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
