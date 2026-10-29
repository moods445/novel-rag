// 向量化页面：按章节显示 embedding 进度，支持整本重建。
//
// 进度来源两层：
//   - 数据库统计（当前 model_id 的真实覆盖，刷新/重启都不丢）
//   - 后台任务状态（正在处理第几章，仅在重建期间有意义）
import { api, escapeHtml } from './api.js';

const els = {};
const state = {
  books: [],
  bookId: null,
  visible: false,
  timer: null,
  busy: false,
  running: false,
};

export function init(refs) {
  Object.assign(els, refs);
  els.bookSelect?.addEventListener('change', () => {
    state.bookId = els.bookSelect.value || null;
    refresh();
  });
  els.refreshBtn?.addEventListener('click', () => refresh());
  els.rebuildBtn?.addEventListener('click', rebuild);
}

export function setBooks(books, currentId) {
  state.books = books || [];
  if (currentId && state.books.some((b) => b.id === currentId)) state.bookId = currentId;
  if (!state.books.some((b) => b.id === state.bookId)) {
    state.bookId = state.books[0]?.id || null;
  }
  fillBooks();
}

export function setVisible(visible) {
  state.visible = Boolean(visible);
  clearTimeout(state.timer);
  if (state.visible) {
    refresh();
    schedule();
  }
}

function schedule() {
  clearTimeout(state.timer);
  // 重建期间 1.5s 一跳，闲时 5s，避免一直全量扫库。
  const delay = state.running ? 1500 : 5000;
  state.timer = setTimeout(async () => {
    if (!state.visible) return;
    await refresh();
    schedule();
  }, delay);
}

function fillBooks() {
  if (!els.bookSelect) return;
  els.bookSelect.innerHTML = '';
  if (!state.books.length) {
    const option = document.createElement('option');
    option.textContent = '（还没有书）';
    els.bookSelect.appendChild(option);
    return;
  }
  for (const book of state.books) {
    const option = document.createElement('option');
    option.value = book.id;
    option.textContent = `${book.title}（${book.chapter_count} 章）`;
    if (book.id === state.bookId) option.selected = true;
    els.bookSelect.appendChild(option);
  }
}

async function refresh() {
  if (!state.bookId) {
    if (els.stats) els.stats.innerHTML = '<span class="hint">先到书架导入一本书。</span>';
    return;
  }
  try {
    const data = await api.get(`/api/books/${state.bookId}/vectors`);
    render(data);
  } catch (error) {
    if (els.hint) els.hint.textContent = `读取失败：${error.message}`;
  }
}

async function rebuild() {
  if (!state.bookId || state.busy) return;
  const book = state.books.find((b) => b.id === state.bookId);
  const ok = window.confirm(
    `重建《${book?.title || state.bookId}》的全部向量？\n重建期间该书的语义检索会暂时为空。`
  );
  if (!ok) return;
  state.busy = true;
  if (els.rebuildBtn) els.rebuildBtn.disabled = true;
  try {
    await api.post(`/api/books/${state.bookId}/vectors/rebuild`, {});
    await refresh();
  } catch (error) {
    if (els.hint) els.hint.textContent = `重建失败：${error.message}`;
  } finally {
    state.busy = false;
    if (els.rebuildBtn) els.rebuildBtn.disabled = false;
  }
}

function render(data) {
  const job = data.job || {};
  const running = job.state === 'queued' || job.state === 'running';
  state.running = running;
  const total = data.paragraphs || 0;
  const indexed = running && typeof job.indexed === 'number' ? job.indexed : data.indexed || 0;
  const percent = total ? Math.min(100, Math.round((indexed / total) * 100)) : 0;

  if (els.bar) els.bar.style.width = `${percent}%`;
  if (els.state) {
    const labels = {
      idle: '待命',
      queued: '排队中',
      running: '重建中',
      done: '最近一次重建完成',
      failed: '最近一次重建失败',
    };
    els.state.textContent = labels[job.state] || '待命';
  }

  const current = job.chapter_no
    ? `第 ${job.chapter_no} 章`
    : data.next_chapter_no
      ? `下一章：第 ${data.next_chapter_no} 章`
      : '全部章节已完成';

  if (els.stats) {
    els.stats.innerHTML = [
      stat('已向量化段落', `${indexed} / ${total}`, `${percent}%`),
      stat('完成章节', `${data.chapters_done || 0} / ${data.chapters_total || 0}`),
      stat('当前进度', current),
      stat('向量模型', escapeHtml(data.model_id || ''), escapeHtml(data.backend || '')),
    ].join('');
  }

  if (els.hint) {
    const parts = [];
    if (job.message) parts.push(job.message);
    if (job.state === 'failed') parts.push('详见「日志」页');
    if (!parts.length) {
      parts.push(
        '向量检索按当前模型按章节增量建立；导入后后台分析会自动补齐，也可以在这里整本重建。'
      );
    }
    els.hint.textContent = parts.join(' · ');
  }

  if (els.chapterHint) {
    els.chapterHint.textContent = `当前模型：${data.model_id || ''} · 共 ${data.chapters_total || 0} 章`;
  }

  renderChapters(data.chapters || [], job);
}

function stat(label, value, extra = '') {
  return (
    `<div class="stat"><span class="stat-label">${escapeHtml(label)}</span>` +
    `<span class="stat-value">${value}</span>` +
    (extra ? `<span class="stat-extra">${extra}</span>` : '') +
    '</div>'
  );
}

function renderChapters(chapters, job) {
  if (!els.chapters) return;
  if (!chapters.length) {
    els.chapters.innerHTML = '<span class="hint">这本书还没有章节。</span>';
    return;
  }
  const activeIdx = job.state === 'running' ? job.chapter_idx : null;
  els.chapters.innerHTML = chapters
    .map((chapter) => {
      const status = chapter.done ? 'done' : chapter.indexed > 0 ? 'partial' : 'pending';
      const active = chapter.chapter_idx === activeIdx ? ' active' : '';
      return (
        `<div class="vch-row ${status}${active}" data-idx="${chapter.chapter_idx}">` +
        `<span class="vch-no">第${chapter.chapter_no}章</span>` +
        `<span class="vch-title" title="${escapeHtml(chapter.title || '')}">${escapeHtml(
          chapter.title || '未命名'
        )}</span>` +
        `<span class="vch-count">${chapter.indexed}/${chapter.total}</span>` +
        `<span class="vch-bar"><i style="width:${chapter.percent}%"></i></span>` +
        '</div>'
      );
    })
    .join('');

  if (activeIdx !== null) {
    const row = els.chapters.querySelector(`[data-idx="${activeIdx}"]`);
    if (row) row.scrollIntoView({ block: 'nearest' });
  }
}
