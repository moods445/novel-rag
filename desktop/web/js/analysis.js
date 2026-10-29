// 分析平面：问答（带引用）、任务进度、关系与时间线。
import { api, escapeHtml } from './api.js';
import { goToCitation } from './reader.js';

const els = {};
let state = { book: null, progressSeq: 1, spoilerGuard: true, askSeq: 0 };

export function init(refs) {
  Object.assign(els, refs);
  els.btnAsk?.addEventListener('click', ask);
  els.question?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') ask();
  });
  els.btnStart?.addEventListener('click', () => withTask('start'));
  els.btnPause?.addEventListener('click', () => withTask('pause'));
  els.spoilerGuard?.addEventListener('change', () => {
    state.spoilerGuard = els.spoilerGuard.checked;
    if (typeof els.onSpoilerChange === 'function') els.onSpoilerChange(state.spoilerGuard);
  });
}

export function setBook(book, progressSeq, spoilerGuard) {
  state.book = book;
  state.progressSeq = progressSeq || 1;
  state.spoilerGuard = Boolean(spoilerGuard);
  if (els.spoilerGuard) els.spoilerGuard.checked = state.spoilerGuard;
}

export function setProgress(seq) {
  state.progressSeq = seq;
}

export function setSpoilerGuard(value) {
  state.spoilerGuard = Boolean(value);
  if (els.spoilerGuard) els.spoilerGuard.checked = state.spoilerGuard;
}

export function spoilerGuard() {
  return state.spoilerGuard;
}

async function ask() {
  if (!state.book) return toast('先选一本书');
  const question = (els.question?.value || '').trim();
  if (!question) return;

  const token = ++state.askSeq;
  els.btnAsk.disabled = true;
  els.answer.innerHTML = '<span class="hint">检索并生成中…</span>';
  els.citations.innerHTML = '';

  try {
    const result = await api.post('/api/ask', {
      book_id: state.book.id,
      question,
      progress_seq: state.progressSeq,
      spoiler_guard: spoilerGuard(),
    });
    if (token !== state.askSeq) return; // 丢弃过期回答
    renderAnswer(result);
    renderCitations(result.citations || []);
    renderMeta(result);
  } catch (error) {
    els.answer.innerHTML = `<span class="hint">提问失败：${escapeHtml(error.message)}</span>`;
  } finally {
    els.btnAsk.disabled = false;
  }
}

function renderAnswer(result) {
  const text = escapeHtml(result.answer || '');
  const withCites = text.replace(/\[(\d+)\]/g, (_, n) => {
    return `<span class="cite" data-cite="${n}">[${n}]</span>`;
  });
  els.answer.innerHTML = withCites;
  els.answer.querySelectorAll('.cite').forEach((node) => {
    node.addEventListener('click', () => {
      const index = Number(node.dataset.cite) - 1;
      jumpTo((result.citations || [])[index]);
    });
  });
}

function renderCitations(citations) {
  els.citations.innerHTML = '';
  if (!citations.length) {
    els.citations.innerHTML = '<li class="empty">没有检索到证据</li>';
    return;
  }
  for (const citation of citations) {
    const li = document.createElement('li');
    const where = citation.chapter_no ? `第${citation.chapter_no}章` : '全书';
    const trust = citation.coverage === 'extracted' ? '' : ' · 未经校验';
    li.innerHTML =
      `<span class="tag">[${citation.n}]</span>${escapeHtml(where)} · ${escapeHtml(citation.label || '')}` +
      `${escapeHtml(trust)}<div class="excerpt">${escapeHtml(citation.excerpt || '')}</div>`;
    li.addEventListener('click', () => jumpTo(citation));
    els.citations.appendChild(li);
  }
}

async function jumpTo(citation) {
  if (!citation) return;
  const ok = await goToCitation(citation);
  if (!ok) toast('这条引用没有可定位的原文位置');
}

function renderMeta(result) {
  const meta = result.meta || {};
  const routes = meta.routes || {};
  const gates = meta.gates || {};
  const coverage = result.coverage || {};
  els.coverageHint.textContent =
    `召回 ${(routes.structured || 0) + (routes.lexical || 0) + (routes.semantic || 0)} 条` +
    `（结构化 ${routes.structured || 0} / 全文 ${routes.lexical || 0} / 语义 ${routes.semantic || 0}）` +
    ` · 已校验 ${gates.extracted || 0} / 未校验 ${gates.unparsed || 0}` +
    ` · 知识库覆盖 ${coverage.done || 0}/${coverage.total || 0}`;
}

async function withTask(action) {
  if (!state.book) return toast('先选一本书');
  try {
    await api.post(`/api/analysis/${state.book.id}/${action}`, {});
    refreshTask();
    setTimeout(refreshTask, 1200);
  } catch (error) {
    toast(`操作失败：${error.message}`);
  }
}

export async function refreshTask() {
  if (!state.book) return;
  try {
    const data = await api.get(`/api/books/${state.book.id}/coverage`);
    const { task, coverage } = data;
    const percent = coverage.total ? Math.round((coverage.done / coverage.total) * 100) : 0;
    els.taskBar.style.width = `${percent}%`;
    els.taskHint.textContent = `${task.state} · ${coverage.done}/${coverage.total}`;
    els.coverageHint.textContent = `知识库覆盖 ${coverage.done}/${coverage.total}`;
  } catch {
    /* 忽略 */
  }
}

export async function refreshGraph() {
  if (!state.book) return;
  try {
    const [graph, timeline] = await Promise.all([
      api.get(`/api/books/${state.book.id}/graph`),
      api.get(`/api/books/${state.book.id}/timeline`),
    ]);
    renderRelations(graph.relations || []);
    renderTimeline(timeline.timeline || []);
  } catch {
    /* 忽略 */
  }
}

function renderRelations(relations) {
  if (!relations.length) {
    els.relations.innerHTML = '<li class="empty">还没有抽取到关系</li>';
    return;
  }
  els.relations.innerHTML = relations
    .slice(0, 60)
    .map(
      (r) =>
        `<li>${escapeHtml(r.a_name || '?')} <span class="dim">${escapeHtml(r.rel_type || '')}</span> ` +
        `${escapeHtml(r.b_name || '?')} <span class="dim">seq ${r.valid_from_seq}</span></li>`
    )
    .join('');
}

function renderTimeline(items) {
  if (!items.length) {
    els.timeline.innerHTML = '<li class="empty">还没有抽取到事件</li>';
    return;
  }
  els.timeline.innerHTML = items
    .slice(0, 60)
    .map(
      (e) =>
        `<li><span class="dim">第${e.chapter_idx + 1}章</span> ${escapeHtml(e.summary || '')}</li>`
    )
    .join('');
}

let toastTimer = null;
function toast(message) {
  if (!els.coverageHint) return;
  const original = els.coverageHint.textContent;
  els.coverageHint.textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.coverageHint.textContent = original;
  }, 2600);
}
