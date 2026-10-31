// 设置平面：模型选择 + 下载进度。所有改动 PUT 后后端热重建，无需重启。
import { api, escapeHtml } from './api.js';

const els = {};
let snapshot = null;
let poll = null;

export function init(refs) {
  Object.assign(els, refs);
  els.ner?.addEventListener('change', () => save({ ner_backend: els.ner.value }));
  els.split?.addEventListener('change', () => save({ split_backend: els.split.value }));
  els.embed?.addEventListener('change', () => save({ embedding_backend: els.embed.value }));
  els.llmProvider?.addEventListener('change', () => {
    const provider = (snapshot?.options?.llm?.providers || []).find((p) => p.name === els.llmProvider.value);
    // provider 没声明模型列表时用默认 model，否则 llm_model 会被存成空值
    const model = provider ? providerModel(provider) : '';
    save({ llm_provider: els.llmProvider.value, llm_model: model });
  });
  els.llmModel?.addEventListener('change', () =>
    save({ llm_model: (els.llmModel.value || '').trim() })
  );
  els.embedEndpoint?.addEventListener('change', () => save({ embedding_endpoint: els.embedEndpoint.value }));
  els.proxySave?.addEventListener('click', saveProxy);
  els.providerAdd?.addEventListener('click', addProvider);
  els.providerList?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-provider]');
    if (button) removeProvider(button.dataset.provider);
  });
  els.download?.addEventListener('click', startDownload);
  els.resplit?.addEventListener('click', resplitCurrentBook);
}

export async function load() {
  const data = await api.get('/api/settings');
  snapshot = data;
  fill(data);
  watchDownload(data);
}

function fill(data) {
  const { values = {}, options = {}, embedding = {} } = data;

  fillSelect(els.ner, options.ner_backend, values.ner_backend);
  fillSelect(els.split, options.split_backend, values.split_backend);
  fillSelect(els.embed, options.embedding_backend, values.embedding_backend);

  const providers = options.llm?.providers || [];
  // 必须带一个空的「未选择」项：否则下拉框会显示第一个 provider，
  // 看着像已经配好了，实际后端 llm_provider 还是空的（问答一直走原文摘录）。
  fillSelect(
    els.llmProvider,
    [
      { id: '', label: providers.length ? '（未选择，请选择 provider）' : '（无可用项）' },
      ...providers.map((p) => ({ id: p.name, label: `${p.label}（${p.base_url}）` })),
    ],
    values.llm_provider
  );
  const active = providers.find((p) => p.name === values.llm_provider);
  fillModelOptions(active?.models || [], values.llm_model || (active ? providerModel(active) : ''));

  fillSelect(els.embedEndpoint, options.embedding_endpoint, values.embedding_endpoint || '');

  if (els.proxy) els.proxy.value = values.network_proxy || '';
  renderProviders(options.llm?.providers || []);

  if (els.llmConfigPath) {
    const custom = (options.llm?.providers || []).filter((p) => p.custom).length;
    const parts = [
      custom
        ? `已添加 ${custom} 个 provider（保存在本机数据库）`
        : '还没有添加 provider：在下方「添加 provider」里填写名称 / Base URL / 模型',
    ];
    // 只有本地测试显式指定 NOVEL_RAG_OPENCODE_CONFIG 时后端才会返回这个字段
    if (options.llm?.config_path) parts.push(`本地测试配置：${options.llm.config_path}`);
    els.llmConfigPath.textContent = parts.join(' · ');
  }

  if (els.embedStatus) {
    els.embedStatus.textContent = embedding.installed
      ? `${embedding.model_id} · 已安装 ${embedding.size_mb} MB`
      : `未安装（缺 ${(embedding.missing || []).join(', ')}）`;
  }

  if (els.llmEffective) {
    const effective = data.effective || {};
    const llm = effective.llm || {};
    const ready = effective.llm_ready || {};
    if (ready.query) {
      els.llmEffective.textContent = `问答模型已生效：${llm.query || ''}`;
      els.llmEffective.style.color = '';
    } else {
      // 后端返回唯一原因（provider 找不到 / 缺少模型名 / 环境变量缺一半），
      // 不再只给一句笼统提示，避免「明明配了」却查不出问题
      const reason = effective.llm_detail?.reason || '';
      els.llmEffective.textContent = `问答模型未生效（当前走离线摘录）：${
        reason || '请选择 provider 与模型，或配置 NOVEL_RAG_QUERY_* 环境变量'
      }`;
      els.llmEffective.style.color = 'var(--danger)';
    }
  }

  if (els.effective) {
    els.effective.textContent = JSON.stringify(
      {
        ner_backend: values.ner_backend,
        split_backend: values.split_backend,
        embedding_backend: values.embedding_backend,
        llm_provider: values.llm_provider,
        llm_model: values.llm_model,
        jieba: data.ner_runtime?.jieba_available,
      },
      null,
      2
    );
  }
}

function fillModelOptions(models, value) {
  const list = document.getElementById('llm-model-options');
  if (list) {
    list.innerHTML = '';
    const suggestions = [...(models || [])];
    // provider 没声明模型列表时，也把当前生效的模型名作为建议
    if (value && !suggestions.includes(value)) suggestions.unshift(value);
    for (const model of suggestions) {
      const option = document.createElement('option');
      option.value = model;
      list.appendChild(option);
    }
  }
  // 用输入框而不是下拉框：provider 没有声明模型列表时也能直接填模型名
  if (els.llmModel) els.llmModel.value = value || '';
}

// provider 声明了模型就用第一个；没声明时退回本地测试配置里的默认 model，
// 否则 llm_model 会被存成空值，后端解析不出模型，问答静默降级成原文摘录。
function providerModel(provider) {
  if (!provider) return '';
  if (provider.models?.length) return provider.models[0];
  return provider.custom ? '' : (snapshot?.options?.llm?.default_model || '');
}

function fillSelect(select, items, value) {
  if (!select) return;
  select.innerHTML = '';
  for (const item of items || []) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = item.label || item.id;
    if (item.id === value) option.selected = true;
    select.appendChild(option);
  }
  if (!items || !items.length) {
    const option = document.createElement('option');
    option.textContent = '（无可用项）';
    select.appendChild(option);
  }
}

async function save(patch) {
  try {
    const data = await api.put('/api/settings', patch);
    snapshot = data;
    fill(data);
    if (data.effective) {
      els.effective.textContent = JSON.stringify(data.effective, null, 2);
    }
    return true;
  } catch (error) {
    alert(`保存失败：${error.message}`);
    await load();
    return false;
  }
}

async function saveProxy() {
  const proxy = (els.proxy?.value || '').trim();
  const ok = await save({ network_proxy: proxy });
  if (els.proxyHint) {
    els.proxyHint.textContent = ok ? (proxy ? '代理已保存' : '已切回直连') : '保存失败';
  }
}

function renderProviders(providers) {
  if (!els.providerList) return;
  const custom = providers.filter((p) => p.custom);
  els.providerList.innerHTML = '';
  if (!custom.length) {
    els.providerList.innerHTML = '<li><span class="dim">还没有手动添加的 provider</span></li>';
    return;
  }
  for (const provider of custom) {
    const item = document.createElement('li');
    const detail = `${provider.base_url}${provider.has_key ? ' · 已配置 Key' : ' · 无 Key'}`;
    item.innerHTML = `<span>${escapeHtml(provider.label || provider.name)}</span> ` +
      `<span class="dim">${escapeHtml(detail)}</span> `;
    const button = document.createElement('button');
    button.className = 'btn';
    button.textContent = '删除';
    button.dataset.provider = provider.name;
    item.appendChild(button);
    els.providerList.appendChild(item);
  }
}

async function addProvider() {
  const name = (els.providerName?.value || '').trim();
  const baseUrl = (els.providerBaseUrl?.value || '').trim();
  if (!name || !baseUrl) {
    els.providerHint.textContent = '名称和 Base URL 必填';
    return;
  }
  els.providerHint.textContent = '保存中…';
  try {
    const result = await api.post('/api/providers', {
      name,
      base_url: baseUrl,
      api_key: (els.providerApiKey?.value || '').trim(),
      models: (els.providerModels?.value || '').trim(),
    });
    for (const key of ['providerName', 'providerBaseUrl', 'providerApiKey', 'providerModels']) {
      if (els[key]) els[key].value = '';
    }
    els.providerHint.textContent = '已添加' + (result.values?.llm_provider ? '并已选中' : '，请在上方选择');
    await load();
    if (result.values?.llm_provider) els.effective.textContent = JSON.stringify(result.values, null, 2);
  } catch (error) {
    els.providerHint.textContent = `失败：${error.message}`;
  }
}

async function removeProvider(name) {
  if (!window.confirm(`删除 provider「${name}」？`)) return;
  try {
    await api.del(`/api/providers/${encodeURIComponent(name)}`);
    els.providerHint.textContent = `已删除 ${name}`;
    await load();
  } catch (error) {
    els.providerHint.textContent = `删除失败：${error.message}`;
  }
}

async function resplitCurrentBook() {
  const bookId = typeof els.getBookId === 'function' ? els.getBookId() : null;
  if (!bookId) {
    els.resplitHint.textContent = '先到书架选一本书';
    return;
  }
  const pattern = (els.resplitPattern?.value || '').trim();
  els.resplitHint.textContent = '重新分章中…';
  try {
    const result = await api.post(`/api/books/${bookId}/resplit`, {
      pattern: pattern || null,
      use_llm: true,
    });
    const parts = [`${result.chapter_count} 章 / ${result.paragraph_count} 段`];
    if (result.invalidated?.facts) parts.push(`作废事实 ${result.invalidated.facts}`);
    if (result.warnings?.length) parts.push(result.warnings[0]);
    els.resplitHint.textContent = parts.join(' · ');
  } catch (error) {
    els.resplitHint.textContent = `失败：${error.message}`;
  }
}

async function startDownload() {
  try {
    els.download.disabled = true;
    const result = await api.post('/api/models/embedding/download', {
      endpoint: els.embedEndpoint?.value || null,
    });
    renderDownload(result.status);
    watchDownload(result);
  } catch (error) {
    alert(`下载失败：${error.message}`);
  } finally {
    els.download.disabled = false;
  }
}

function watchDownload(payload) {
  clearInterval(poll);
  const status = payload?.embedding?.download || payload?.status;
  if (!status) return;
  renderDownload(status);
  if (status.state !== 'running') return;
  poll = setInterval(async () => {
    try {
      const next = await api.get('/api/models/embedding/download');
      renderDownload(next.status);
      if (next.status.state !== 'running') {
        clearInterval(poll);
        await load();
      }
    } catch {
      clearInterval(poll);
    }
  }, 1000);
}

function renderDownload(status) {
  if (!els.embedBar) return;
  const percent = Number(status?.percent || 0);
  els.embedBar.style.width = `${percent}%`;
  if (els.embedStatus) {
    const state = status?.state || 'idle';
    const label = {
      idle: '',
      running: `下载中 ${percent}%（${status.current || ''}）`,
      done: '下载完成',
      failed: `下载失败：${status.error || ''}`,
    }[state];
    if (label) els.embedStatus.textContent = label;
  }
  if (status?.state === 'failed' && status.error) {
    els.embedStatus.innerHTML = `<span style="color:#f87171">${escapeHtml(status.error)}</span>`;
  }
}
