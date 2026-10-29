// 日志页面：读取 backend.log / sidecar.log 的尾部，按级别着色，可在页面内自动刷新。
import { api, escapeHtml } from './api.js';

const els = {};
const state = { visible: false, timer: null, loading: false };

export function init(refs) {
  Object.assign(els, refs);
  els.refreshBtn?.addEventListener('click', () => refresh(true));
  els.source?.addEventListener('change', () => refresh(true));
  els.lines?.addEventListener('change', () => refresh(true));
  els.auto?.addEventListener('change', () => {
    if (state.visible) schedule();
  });
}

export function setVisible(visible) {
  state.visible = Boolean(visible);
  clearTimeout(state.timer);
  if (state.visible) {
    refresh(true);
    schedule();
  }
}

function schedule() {
  clearTimeout(state.timer);
  if (!state.visible || !els.auto?.checked) return;
  state.timer = setTimeout(async () => {
    if (!state.visible) return;
    await refresh(false);
    schedule();
  }, 2000);
}

async function refresh(force) {
  if (state.loading) return;
  state.loading = true;
  try {
    const source = els.source?.value || 'backend';
    const lines = els.lines?.value || '500';
    const data = await api.get(`/api/logs?source=${source}&lines=${lines}`);
    render(data);
    if (force) toggleAuto();
  } catch (error) {
    if (els.meta) els.meta.textContent = `读取失败：${error.message}`;
  } finally {
    state.loading = false;
  }
}

function toggleAuto() {
  // 手动刷新后按当前勾选状态重新排下一次
  schedule();
}

function render(data) {
  if (els.meta) {
    const size = formatBytes(data.size || 0);
    els.meta.textContent = data.exists
      ? `${data.path} · ${size}`
      : `${data.path} · 文件不存在`;
  }
  if (!els.view) return;
  const lines = data.lines || [];
  if (!lines.length) {
    els.view.innerHTML = '<span class="hint">暂无日志。</span>';
    return;
  }
  els.view.innerHTML = lines.map(renderLine).join('');
  els.view.scrollTop = els.view.scrollHeight;
}

function renderLine(line) {
  const match = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\w+)\s+\[(.*?)\]\s+([^:]+):\s*(.*)$/);
  if (!match) {
    return `<div class="log-line">${escapeHtml(line)}</div>`;
  }
  const [, time, level, thread, name, message] = match;
  const cls = level.toLowerCase();
  return (
    `<div class="log-line ${cls}">` +
    `<span class="log-time">${escapeHtml(time)}</span>` +
    `<span class="log-level">${escapeHtml(level)}</span>` +
    `<span class="log-name">${escapeHtml(name)}</span>` +
    `<span class="log-msg">${escapeHtml(message)}</span>` +
    `<span class="log-thread">${escapeHtml(thread)}</span>` +
    '</div>'
  );
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
