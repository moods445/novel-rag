// 阅读页右侧对话：多轮上下文 + 引用跳转。
//
// 历史对话只在客户端保存：每次提问把最近若干条消息作为 `history`
// 交给后端，后端只用于理解追问中的指代，事实仍来自检索资料。
import { api, escapeHtml } from './api.js';
import { goToCitation } from './reader.js';

const HISTORY_LIMIT = 8;

const els = {};
const state = {
  book: null,
  messages: [],
  progressSeq: 1,
  spoilerGuard: true,
  pending: false,
  askSeq: 0,
};
let emptyHtml = '';

export function init(refs) {
  Object.assign(els, refs);
  emptyHtml = els.log ? els.log.innerHTML : '';
  els.send?.addEventListener('click', send);
  els.question?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') send();
  });
  els.spoilerGuard?.addEventListener('change', () => {
    state.spoilerGuard = els.spoilerGuard.checked;
    if (typeof els.onSpoilerChange === 'function') els.onSpoilerChange(state.spoilerGuard);
  });
}

export function setBook(book) {
  state.book = book;
  state.messages = [];
  state.askSeq += 1;
  state.pending = false;
  if (els.send) els.send.disabled = false;
  if (els.question) els.question.value = '';
  if (els.log) els.log.innerHTML = emptyHtml;
}

export function setProgress(seq) {
  state.progressSeq = seq;
}

export function setSpoilerGuard(value) {
  state.spoilerGuard = Boolean(value);
  if (els.spoilerGuard) els.spoilerGuard.checked = state.spoilerGuard;
}

function history() {
  return state.messages.slice(-HISTORY_LIMIT).map((m) => ({ ...m }));
}

async function send() {
  if (!state.book) return;
  const question = (els.question?.value || '').trim();
  if (!question || state.pending) return;

  state.pending = true;
  if (els.send) els.send.disabled = true;
  if (els.question) els.question.value = '';

  const historyPayload = history();
  appendMessage('user', question);
  const pending = appendMessage('assistant', '检索并生成中…');
  const token = ++state.askSeq;

  try {
    const result = await api.post('/api/ask', {
      book_id: state.book.id,
      question,
      progress_seq: state.progressSeq,
      spoiler_guard: state.spoilerGuard,
      history: historyPayload,
    });
    if (token !== state.askSeq) return; // 切书/重开后丢弃过期回答
    state.messages.push({ role: 'user', content: question });
    state.messages.push({ role: 'assistant', content: result.answer || '' });
    renderAnswer(pending, result);
  } catch (error) {
    pending.querySelector('.chat-bubble').textContent = `提问失败：${error.message}`;
  } finally {
    if (token === state.askSeq) {
      state.pending = false;
      if (els.send) els.send.disabled = false;
    }
  }
}

function appendMessage(role, text) {
  removeEmpty();
  const wrap = document.createElement('div');
  wrap.className = `chat-msg ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  bubble.textContent = text;
  wrap.appendChild(bubble);
  els.log.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function renderAnswer(wrap, result) {
  const bubble = wrap.querySelector('.chat-bubble');
  const citations = result.citations || [];
  bubble.innerHTML = escapeHtml(result.answer || '').replace(
    /\[(\d+)\]/g,
    (_, n) => `<span class="cite" data-cite="${n}">[${n}]</span>`
  );
  bubble.querySelectorAll('.cite').forEach((node) => {
    node.addEventListener('click', () => {
      const citation = citations[Number(node.dataset.cite) - 1];
      jumpTo(citation);
    });
  });

  if (citations.length) {
    const row = document.createElement('div');
    row.className = 'chat-cites';
    for (const citation of citations) {
      const button = document.createElement('button');
      button.className = 'chat-cite';
      const where = citation.chapter_no ? `第${citation.chapter_no}章` : '全书';
      button.textContent = `[${citation.n}] ${where}`;
      button.addEventListener('click', () => jumpTo(citation));
      row.appendChild(button);
    }
    wrap.appendChild(row);
  }

  const coverage = result.coverage || {};
  const meta = document.createElement('div');
  meta.className = 'chat-meta';
  const parts = [`引用 ${citations.length} 条`];
  if (coverage.total) parts.push(`知识库 ${coverage.done || 0}/${coverage.total}`);
  if (result.degraded) parts.push(result.degraded === 'extractive' ? '离线摘录' : result.degraded);
  meta.textContent = parts.join(' · ');
  wrap.appendChild(meta);
  scrollToBottom();
}

async function jumpTo(citation) {
  if (!citation) return;
  const ok = await goToCitation(citation);
  if (!ok) {
    const meta = document.createElement('div');
    meta.className = 'chat-meta';
    meta.textContent = '这条引用没有可定位的原文位置';
    els.log.appendChild(meta);
    scrollToBottom();
  }
}

function removeEmpty() {
  const empty = els.log?.querySelector('.chat-empty');
  if (empty) empty.remove();
}

function scrollToBottom() {
  if (els.log) els.log.scrollTop = els.log.scrollHeight;
}
