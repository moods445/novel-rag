// 关系图页面：独立管理抽取任务的开始 / 暂停 / 继续 / 重建，并用 relation-graph
// 把当前关系画成「云图」风格的关系网络（节点自由拖拽、画布可平移缩放）。
//
// 关系只能由「章节事实抽取」派生：开始 = 从断点继续抽取，重建 = 清空本书
// 的抽取产物后从第一章重跑。离线哨兵模式不会生成关系，因此未配置抽取模型
// 时开始/继续/重建都会被禁用。
import { api, escapeHtml } from './api.js';

const els = {};
const state = {
  books: [],
  bookId: null,
  visible: false,
  timer: null,
  busy: false,
  running: false,
  modelReady: false,
  taskState: 'absent',
  dataKey: '',
  detailHint: '',
  // 云图设置：人物上限 / 根人物 / 是否显示孤立人物
  maxNodes: 30,
  showIsolated: false,
  roots: [],
  rootNodes: [], // 当前书可选的全部人物（按连接度降序），供搜索列表使用
  rootQuery: '', // 根人物搜索框内容
  rootSig: '',
  // 下探：用户点开「＋」的人物 id，其直接关系人会被补进画布（超出人物上限）
  drilled: new Set(),
  posCache: new Map(), // 上一版节点坐标：下探只把新节点铺到外圈，已有节点不跳动
  posSig: '', // 布局签名（书 / 数据量 / 上限 / 根人物 / 孤立开关），变了才清坐标缓存
  payload: null,
};

// 默认最多 30 个人物（页面可改）：先画根人物，再按「离根人物多远」由近到远补充，
// 与根人物不连通的人物算作孤立，只有打开开关才出现。
const DEFAULT_MAX_NODES = 30;
const MIN_MAX_NODES = 3;
const MAX_MAX_NODES = 200;
const DEFAULT_ROOT_COUNT = 3;
const MAX_EDGES = 400;
const ROOT_LIST_LIMIT = 80; // 候选列表一次最多显示多少个名字，其余靠搜索
const MAX_INPUT_DELAY = 350; // 上限输入框的防抖（毫秒）
const DRILL_HARD_CAP = 200; // 下探补进来的节点也有硬上限，避免一戳就铺满整张图

const STORE_MAX_NODES = 'novel-rag.graph.maxNodes';
const STORE_ISOLATED = 'novel-rag.graph.showIsolated';
const STORE_ROOTS = 'novel-rag.graph.roots'; // 后接书 id：根人物按书单独记住

const KIND_LABELS = {
  person: '人物',
  place: '地点',
  org: '势力/组织',
  item: '物品',
  other: '其他',
};

// relation-graph 的 UMD 构建 + 它的三个外部全局（由 desktop/scripts/prepare_vendor.py 拉取）。
// 顺序不能乱：relation-graph 加载时就要读到 window.Vue / window.screenfull / window.html2canvas。
const VENDOR_DIR = './vendor/relation-graph/';
const VENDOR_SCRIPTS = [
  'vue.global.prod.js',
  'screenfull.js',
  'html2canvas.min.js',
  'relation-graph.umd.js',
];

const CLOUD_OPTIONS = {
  backgroundColor: 'transparent',
  disableDragNode: false, // 节点可以自由拖拽
  disableDragCanvas: false, // 空白处拖动平移画布
  disableZoom: false, // 滚轮缩放
  allowShowMiniToolBar: false,
  allowShowRefreshButton: false,
  allowShowDownloadButton: false,
  allowShowFullscreenMenu: false,
  defaultNodeShape: 0,
  defaultLineShape: 1,
  defaultLineColor: '#8b98ad',
  defaultLineWidth: 1,
  defaultShowLineLabel: false,
  defaultJunctionPoint: 'border',
  // 缩小时也渲染节点内容：云图的气泡本身就是内容，被「按缩放隐藏」就只剩一片空白
  hideNodeContentByZoom: false,
  moveToCenterWhenRefresh: true,
  zoomToFitWhenRefresh: true,
  useAnimationWhenRefresh: false,
  layout: { layoutName: 'fixed' },
};

let vendorPromise = null;
let cloud = null; // { app, graph }：Vue 应用与 relation-graph 组件实例

export function init(refs) {
  Object.assign(els, refs);
  // 记下「点击节点前的提示文案」，空图时还原，避免残留上一次点的节点
  state.detailHint = els.detail ? els.detail.textContent.trim() : '';
  state.maxNodes = clampMax(readNumber(STORE_MAX_NODES, DEFAULT_MAX_NODES));
  state.showIsolated = readFlag(STORE_ISOLATED);
  if (els.maxInput) els.maxInput.value = String(state.maxNodes);
  if (els.isolatedToggle) els.isolatedToggle.checked = state.showIsolated;

  els.bookSelect?.addEventListener('change', () => {
    state.bookId = els.bookSelect.value || null;
    state.dataKey = '';
    state.roots = loadRoots(state.bookId);
    // 根人物按书记住，搜索关键字属于上一本书，换书时清掉
    state.rootQuery = '';
    if (els.rootSearch) els.rootSearch.value = '';
    resetDrill();
    clearTimeout(state.timer);
    if (state.visible && state.bookId) {
      refresh();
      schedule();
    } else {
      refresh();
    }
  });
  // 上限改成边输入边生效：type=number 的 change 要等失焦，看起来就像「设置不起效果」
  let maxTimer = null;
  els.maxInput?.addEventListener('input', () => {
    clearTimeout(maxTimer);
    maxTimer = setTimeout(() => applyMax(false), MAX_INPUT_DELAY);
  });
  els.maxInput?.addEventListener('change', () => {
    clearTimeout(maxTimer);
    applyMax(true);
  });
  els.isolatedToggle?.addEventListener('change', () => {
    state.showIsolated = els.isolatedToggle.checked;
    write(STORE_ISOLATED, state.showIsolated ? '1' : '');
    resetDrill();
    rerender();
  });
  els.rootSearch?.addEventListener('input', filterRoots);
  els.rootSearch?.addEventListener('search', filterRoots);
  els.rootList?.addEventListener('click', (event) => {
    const row = event.target.closest('[data-root-id]');
    if (row) toggleRoot(row.dataset.rootId);
  });
  els.rootChips?.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-root-id]');
    if (chip) toggleRoot(chip.dataset.rootId);
  });
  // 气泡里的「＋」下探按钮：捕获阶段截住事件，避免同时触发节点点击 / 拖拽
  for (const type of ['pointerdown', 'mousedown', 'click']) {
    els.canvas?.addEventListener(
      type,
      (event) => {
        const button = event.target.closest?.('.cloud-drill');
        if (!button) return;
        event.stopPropagation();
        if (type === 'click') {
          event.preventDefault();
          toggleDrill(button.dataset.drillId);
        }
      },
      true
    );
  }
  els.drillReset?.addEventListener('click', () => {
    resetDrill();
    rerender();
  });
  els.refreshBtn?.addEventListener('click', () => refresh());
  els.startBtn?.addEventListener('click', start);
  els.pauseBtn?.addEventListener('click', pause);
  els.resumeBtn?.addEventListener('click', resume);
  els.rebuildBtn?.addEventListener('click', rebuild);
}

/** 设置变了就用最近一次的数据重画：不重新请求，等下次轮询再对齐。 */
function rerender() {
  if (state.payload) renderGraph(state.payload);
}

/**
 * 应用「最多人物」输入。commit=true 时（失焦 / 回车）做完整校验并回填输入框；
 * 输入过程中只在合法范围内即时生效，避免把「1」「20」这种中间态强行夹成 3。
 */
function applyMax(commit) {
  if (!els.maxInput) return;
  const raw = els.maxInput.value.trim();
  const value = Number(raw);
  if (!raw || !Number.isFinite(value)) {
    if (commit) els.maxInput.value = String(state.maxNodes);
    return;
  }
  if (!commit && (value < MIN_MAX_NODES || value > MAX_MAX_NODES)) return;
  const next = clampMax(value);
  if (commit) els.maxInput.value = String(next);
  if (next === state.maxNodes) return;
  state.maxNodes = next;
  write(STORE_MAX_NODES, String(next));
  resetDrill();
  rerender();
}

function filterRoots() {
  state.rootQuery = els.rootSearch ? els.rootSearch.value : '';
  paintRootPicker(state.activeRoots || [], state.pickedRoots || []);
}

/** 点一下设为根人物，再点一下取消；未手动选过时由 selectCloud 回落到「连接最多的人」。 */
function toggleRoot(id) {
  if (!id) return;
  const roots = state.roots.includes(id)
    ? state.roots.filter((value) => value !== id)
    : [...state.roots, id];
  state.roots = roots;
  saveRoots(state.bookId, roots);
  resetDrill();
  rerender();
}

/** 每个气泡上的「＋」：展开这个节点的一跳关系人；再点一次收起。 */
function toggleDrill(id) {
  if (!id) return;
  if (state.drilled.has(id)) state.drilled.delete(id);
  else state.drilled.add(id);
  rerender();
}

function resetDrill() {
  if (!state.drilled.size) return;
  state.drilled.clear();
  if (els.drillReset) els.drillReset.hidden = true;
}

function clampMax(value) {
  const number = Math.round(Number(value));
  if (!Number.isFinite(number)) return DEFAULT_MAX_NODES;
  return Math.min(MAX_MAX_NODES, Math.max(MIN_MAX_NODES, number));
}

function readNumber(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    const number = Number(raw);
    return raw !== null && Number.isFinite(number) ? number : fallback;
  } catch {
    return fallback;
  }
}

function readFlag(key) {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

function write(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* 存不了就算了，只影响下次打开时的默认值 */
  }
}

function loadRoots(bookId) {
  if (!bookId) return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(`${STORE_ROOTS}:${bookId}`) || '[]');
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

function saveRoots(bookId, roots) {
  if (!bookId) return;
  write(`${STORE_ROOTS}:${bookId}`, JSON.stringify(roots));
}

export function setBooks(books) {
  state.books = books || [];
  // 必须先手动选书：不自动落到第一本，避免在错误的书上开始抽取。
  if (!state.books.some((b) => b.id === state.bookId)) state.bookId = null;
  fillBooks();
}

export function setVisible(visible) {
  state.visible = Boolean(visible);
  clearTimeout(state.timer);
  if (state.visible) {
    refresh();
    if (state.bookId) schedule();
  }
}

function schedule() {
  clearTimeout(state.timer);
  if (!state.bookId) return;
  const delay = state.running ? 1500 : 5000;
  state.timer = setTimeout(async () => {
    if (!state.visible || !state.bookId) return;
    await refresh();
    schedule();
  }, delay);
}

function fillBooks() {
  if (!els.bookSelect) return;
  els.bookSelect.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = state.books.length ? '请选择书籍…' : '（还没有书）';
  els.bookSelect.appendChild(placeholder);
  for (const book of state.books) {
    const option = document.createElement('option');
    option.value = book.id;
    option.textContent = `${book.title}（${book.chapter_count} 章）`;
    if (book.id === state.bookId) option.selected = true;
    els.bookSelect.appendChild(option);
  }
  if (!state.bookId) els.bookSelect.value = '';
}

async function refresh() {
  if (!state.bookId) {
    state.modelReady = false;
    state.taskState = 'absent';
    state.payload = null;
    renderEmpty('请先在上方选择一本书，再开始抽取。');
    renderTask({ coverage: {}, task: { state: 'absent' } });
    return;
  }
  try {
    const [coverage, graph] = await Promise.all([
      api.get(`/api/books/${state.bookId}/coverage`),
      api.get(`/api/books/${state.bookId}/graph`),
    ]);
    state.modelReady = Boolean(graph.model_ready);
    renderTask(coverage);
    renderGraph(graph);
  } catch (error) {
    if (els.hint) els.hint.textContent = `读取失败：${error.message}`;
  }
}

function renderTask(data) {
  const task = data.task || {};
  const coverage = data.coverage || {};
  const total = coverage.total || 0;
  const done = coverage.done || 0;
  const percent = total ? Math.round((done / total) * 100) : 0;
  const running = ['queued', 'running'].includes(task.state);
  state.running = running;
  state.taskState = task.state || 'absent';

  if (els.bar) els.bar.style.width = `${percent}%`;
  if (els.state) {
    const labels = {
      absent: '尚未开始',
      queued: '排队中',
      running: '抽取中',
      paused: '已暂停（可点「继续」）',
      failed: '最近一次失败',
      done: '最近一次完成',
    };
    const label = labels[task.state] || '待命';
    els.state.textContent = total ? `${label} · ${done}/${total}（${percent}%）` : label;
  }

  if (els.stats) {
    els.stats.innerHTML = [
      stat('章节覆盖', `${done} / ${total}`, `${percent}%`),
      stat('进度游标', `${task.cursor || 0} / ${task.total || total}`),
      stat('抽取模型', state.modelReady ? '已配置' : '离线（无关系）'),
    ].join('');
  }

  if (els.startBtn) els.startBtn.disabled = !state.bookId || state.busy || !state.modelReady || running;
  if (els.pauseBtn) els.pauseBtn.disabled = !state.bookId || state.busy || !running;
  if (els.resumeBtn) {
    els.resumeBtn.disabled =
      !state.bookId || state.busy || !state.modelReady || !['paused', 'failed'].includes(task.state);
  }
  if (els.rebuildBtn) els.rebuildBtn.disabled = !state.bookId || state.busy || !state.modelReady || running;

  if (els.hint) {
    let text;
    if (!state.bookId) {
      text = '请先在上方选择一本书，再开始抽取。';
    } else if (!state.modelReady) {
      text = '未配置抽取模型：离线模式不会生成关系。请先到「设置」页配置抽取模型，再回来开始或重建。';
    } else if (running) {
      text = `正在抽取：已完成 ${done}/${total} 章（${percent}%）；重复点击开始不会重复启动任务，可以随时暂停。`;
    } else if (task.state === 'done' && !done) {
      text = '抽取已结束但没有关系；若刚刚配置/更换了模型，点「重建」让整本书用新模型重跑。';
    } else {
      text = '「开始」从断点继续；「重建」会清空本书的抽取产物并从第一章重跑（原文与向量不受影响）。';
    }
    els.hint.textContent = `${text}${task.message ? ` · ${task.message}` : ''}`;
  }
}

async function start() {
  if (!state.bookId || state.busy) return;
  await action('start', 'analysis');
}

async function resume() {
  if (!state.bookId || state.busy) return;
  await action('resume', 'analysis');
}

async function pause() {
  if (!state.bookId) return;
  await action('pause', 'analysis');
}

async function action(name, route) {
  state.busy = true;
  if (els.hint) els.hint.textContent = '任务提交中…';
  try {
    await api.post(`/api/${route}/${state.bookId}/${name}`, {});
  } catch (error) {
    if (els.hint) els.hint.textContent = `操作失败：${error.message}`;
  } finally {
    state.busy = false;
    await refresh();
    if (state.visible) schedule();
  }
}

async function rebuild() {
  if (!state.bookId || state.busy) return;
  const book = state.books.find((b) => b.id === state.bookId);
  const ok = window.confirm(
    `重建《${book?.title || state.bookId}》的关系图？\n\n` +
      '会清空该书的章节事实、实体与关系，并用当前抽取模型从第一章重跑；\n' +
      '重建期间关系图为空，原文阅读与向量检索不受影响。'
  );
  if (!ok) return;
  state.busy = true;
  if (els.hint) els.hint.textContent = '重建任务提交中…';
  try {
    await api.post(`/api/books/${state.bookId}/graph/rebuild`, {});
    state.dataKey = '';
  } catch (error) {
    if (els.hint) els.hint.textContent = `重建失败：${error.message}`;
  } finally {
    state.busy = false;
    await refresh();
  }
}

function stat(label, value, extra = '') {
  return (
    `<div class="stat"><span class="stat-label">${escapeHtml(label)}</span>` +
    `<span class="stat-value">${escapeHtml(value)}</span>` +
    (extra ? `<span class="stat-extra">${escapeHtml(extra)}</span>` : '') +
    '</div>'
  );
}

function renderEmpty(message) {
  destroyCloud();
  if (els.canvas) els.canvas.innerHTML = `<span class="hint">${escapeHtml(message)}</span>`;
  if (els.legend) els.legend.textContent = '';
  if (els.detail) els.detail.textContent = state.detailHint;
  // 清掉上一本书的候选根人物，免得空图时列表里还挂着不相干的名字
  state.rootNodes = [];
  state.rootQuery = '';
  state.rootSig = '';
  state.activeRoots = [];
  state.pickedRoots = [];
  if (els.rootSearch) els.rootSearch.value = '';
  if (els.rootChips) els.rootChips.innerHTML = '';
  if (els.rootList) els.rootList.innerHTML = '';
  // 图没了，下探与坐标缓存也失效
  resetDrill();
  state.posSig = '';
  state.posCache = new Map();
}

async function renderGraph(data) {
  state.payload = data || null;
  const relations = (data && data.relations) || [];
  const entities = (data && data.entities) || [];
  if (!relations.length) {
    state.dataKey = '';
    renderEmpty('还没有抽取到关系');
    return;
  }

  const raw = analyzeGraph(relations, entities);
  if (!raw.nodes.length) {
    state.dataKey = '';
    renderEmpty('还没有抽取到关系');
    return;
  }

  // 根人物：优先用用户选的；没选过就默认取度数最高的几个（列表里标为「自动」）。
  prepareRootNodes(raw.nodes);
  const known = new Set(raw.nodes.map((node) => node.id));
  const picked = state.roots.filter((id) => known.has(id));
  const roots = picked.length ? picked : defaultRoots(raw.nodes);
  state.activeRoots = roots;
  state.pickedRoots = picked;
  paintRootPicker(roots, picked);

  const maxSeq = relations.reduce((max, r) => Math.max(max, Number(r.valid_from_seq) || 0), 0);
  // 下探沿用上一版的坐标：只有「书 / 数据量 / 上限 / 根人物 / 孤立开关」变了才重排整张图
  const posSig = [
    state.bookId,
    relations.length,
    entities.length,
    maxSeq,
    state.maxNodes,
    state.showIsolated ? 1 : 0,
    roots.join(','),
  ].join('|');
  if (posSig !== state.posSig) {
    state.posSig = posSig;
    state.posCache = new Map();
  } else {
    // 下探前先记下当前坐标（含用户拖过的位置），新节点只补在外圈
    snapshotPositions();
  }

  const built = selectCloud(raw.nodes, raw.edges, {
    max: state.maxNodes,
    roots,
    showIsolated: state.showIsolated,
    drilled: state.drilled,
    positions: state.posCache,
  });

  // 数据或设置没变就不重画：刷新是轮询的，重画会把用户拖好的节点位置弹回去。
  const key = [
    state.bookId,
    relations.length,
    entities.length,
    maxSeq,
    state.maxNodes,
    state.showIsolated ? 1 : 0,
    roots.join(','),
    [...state.drilled].sort().join(','),
  ].join('|');
  if (key === state.dataKey && cloud) {
    renderCloudMeta(built, raw.nodes.length);
    return;
  }

  const ready = await loadVendor();
  if (!ready) {
    state.dataKey = '';
    renderEmpty(
      '关系图组件未就绪：先运行 python3 desktop/scripts/prepare_vendor.py 拉取 vendor/relation-graph'
    );
    return;
  }
  if (!state.visible) return; // 加载脚本期间用户可能已经切走
  state.dataKey = key;
  const graph = ensureCloud().graph;
  graph.setJsonData({ nodes: built.nodes, lines: built.lines }, (instance) => {
    renderCloudMeta(built, raw.nodes.length);
    if (els.detail) els.detail.textContent = state.detailHint;
    // 节点位置是算好的，布局一结束就取景；力导向残留的外扩问题不会再有
    fitCloud(instance);
  });
}

/** 图例与「隐藏了多少」的提示：设置调小上限时能看出被藏了多少人。 */
function renderCloudMeta(built, total) {
  if (els.drillReset) els.drillReset.hidden = state.drilled.size === 0;
  if (!els.legend) return;
  const parts = [`${built.nodes.length} 个节点`, `${built.lines.length} 条关系`];
  if (built.isolated) parts.push(`孤立 ${built.isolated}`);
  if (built.drilled) parts.push(`下探 ${built.drilled}`);
  if (total > built.nodes.length) parts.push(`共 ${total} 人`);
  els.legend.textContent = parts.join(' · ');
}

function defaultRoots(nodes) {
  // 优先取有关系的；整本书都没关系时退化成任意几个人，避免根人物为空
  const connected = nodes.filter((node) => node.degree > 0);
  const pool = connected.length ? connected : nodes;
  return [...pool]
    .sort((a, b) => b.degree - a.degree || a.name.localeCompare(b.name, 'zh-CN'))
    .slice(0, DEFAULT_ROOT_COUNT)
    .map((node) => node.id);
}

/**
 * 候选人物按连接度降序排好放进 state.rootNodes。
 * 不收「只保留 degree>0」：没有关系的实体也要能选，否则无关系的书列表就是空的。
 * 人物集合或度数没变就不重建（轮询刷新频繁，避免搜索框/滚动位置被重置）。
 */
function prepareRootNodes(nodes) {
  const sig = nodes.map((node) => `${node.id}:${node.degree}`).join('|');
  if (sig === state.rootSig) return;
  state.rootSig = sig;
  state.rootNodes = [...nodes].sort(
    (a, b) => b.degree - a.degree || a.name.localeCompare(b.name, 'zh-CN')
  );
}

/**
 * 根人物选择器：已选胶囊（点一下取消）+ 搜索框 + 可点击的人物列表。
 * roots 是当前真正生效的根人物，picked 是用户显式选中的那些；
 * picked 为空时 roots 由默认规则生成，列表里用虚线框标注，点一下即可固定下来。
 */
function paintRootPicker(roots, picked) {
  paintRootChips(picked);
  if (!els.rootList) return;
  if (!state.rootNodes.length) {
    els.rootList.innerHTML = '<div class="root-empty">还没有可选的人物</div>';
    return;
  }
  const active = new Set(roots);
  const explicit = new Set(picked);
  const query = state.rootQuery.trim().toLowerCase();
  const matched = query
    ? state.rootNodes.filter((node) => node.name.toLowerCase().includes(query))
    : state.rootNodes;
  const shown = matched.slice(0, ROOT_LIST_LIMIT);
  const rows = shown.map((node) => {
    const isRoot = active.has(node.id);
    const auto = isRoot && !explicit.has(node.id);
    return (
      `<button type="button" class="root-row${isRoot ? ' is-root' : ''}${auto ? ' is-auto' : ''}"` +
      ` data-root-id="${escapeHtml(node.id)}" role="option" aria-selected="${isRoot}">` +
      '<span class="root-check"></span>' +
      `<span class="root-name">${escapeHtml(node.name)}</span>` +
      `<span class="root-degree">${node.degree}</span>` +
      '</button>'
    );
  });
  if (query && !matched.length) rows.push('<div class="root-empty">没有匹配的人物</div>');
  if (matched.length > shown.length) {
    rows.push(`<div class="root-more">还有 ${matched.length - shown.length} 人，输入关键字查找</div>`);
  }
  els.rootList.innerHTML = rows.join('');
}

function paintRootChips(picked) {
  if (!els.rootChips) return;
  if (!picked.length) {
    els.rootChips.innerHTML =
      `<span class="root-empty">未选择，自动取连接最多的 ${DEFAULT_ROOT_COUNT} 人</span>`;
    return;
  }
  const byId = new Map(state.rootNodes.map((node) => [node.id, node]));
  els.rootChips.innerHTML = picked
    .map((id) => {
      const name = byId.get(id)?.name || id;
      return (
        `<button type="button" class="root-chip" data-root-id="${escapeHtml(id)}" title="点击取消">` +
        `${escapeHtml(name)}<span class="root-chip-x">×</span></button>`
      );
    })
    .join('');
}

/**
 * zoomToFit / setZoom / setCanvasOffset 不在组件的公开 API 上，要通过 getInstance() 取内部实例。
 * 这里不用组件自带的 zoomToFit：它按「当前可见节点」的外框算，而组件会把视口外的节点裁掉，
 * 反复取景会越取越小。改为直接用布局后的节点坐标算整张图尺寸，再等比缩放到画布留白内。
 */
const FIT_PADDING = 36; // 屏幕像素留白，给气泡的辉光留位置

function fitCloud(api) {
  if (!api) return;
  try {
    const instance = typeof api.getInstance === 'function' ? api.getInstance() : api;
    const nodes = instance?.graphData?.nodes;
    const view = instance?.options?.viewSize;
    if (!nodes?.length || !view?.width || !view?.height) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const node of nodes) {
      // node.width 可能是数字，也可能是 "88px" 这样的字符串
      const width = Number.parseFloat(node.width) || 62;
      const height = Number.parseFloat(node.height) || width;
      minX = Math.min(minX, node.x);
      minY = Math.min(minY, node.y);
      maxX = Math.max(maxX, node.x + width);
      maxY = Math.max(maxY, node.y + height);
    }
    if (!Number.isFinite(minX) || !Number.isFinite(maxX)) return;
    const scale = Math.min(
      1.3, // 人物少的时候允许放大一点，别让几个气泡缩在画布中央
      (view.width - FIT_PADDING * 2) / Math.max(maxX - minX, 1),
      (view.height - FIT_PADDING * 2) / Math.max(maxY - minY, 1)
    );
    if (!(scale > 0)) return;
    if (typeof instance.setZoom === 'function') instance.setZoom(scale * 100);
    // 缩放要下一帧才反映到节点外框上，等渲染完再按实测外框居中
    requestAnimationFrame(() => requestAnimationFrame(() => centerCloud(instance)));
  } catch {
    /* 取景失败不影响图本身，用户还可以自己缩放 */
  }
}

function centerCloud(instance) {
  if (!els.canvas || typeof instance.setCanvasOffset !== 'function') return;
  const nodes = els.canvas.querySelectorAll('.rel-node-peel[data-id]');
  if (!nodes.length) return;
  const frame = els.canvas.getBoundingClientRect();
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const rect = node.getBoundingClientRect();
    minX = Math.min(minX, rect.x);
    minY = Math.min(minY, rect.y);
    maxX = Math.max(maxX, rect.right);
    maxY = Math.max(maxY, rect.bottom);
  }
  const offset = instance.options?.canvasOffset || { x: 0, y: 0 };
  // canvasOffset 是画布坐标→屏幕坐标的平移量（单位是屏幕像素），补上差值就把内容对中
  instance.setCanvasOffset(
    offset.x + (frame.x + frame.width / 2 - (minX + maxX) / 2),
    offset.y + (frame.y + frame.height / 2 - (minY + maxY) / 2)
  );
}

// ---------------------------------------------------------------- relation-graph 接入

function vendorReady() {
  return Boolean(window.Vue && window.RelationGraph && window.RelationGraph.RelationGraph);
}

/** 按顺序用普通 script 加载 UMD（前端没有打包器，也就不用 import 裸模块名）。 */
function loadVendor() {
  if (vendorReady()) return Promise.resolve(true);
  if (vendorPromise) return vendorPromise;
  vendorPromise = VENDOR_SCRIPTS.reduce(
    (chain, file) => chain.then(() => appendScript(`${VENDOR_DIR}${file}`)),
    Promise.resolve()
  )
    .then(() => vendorReady())
    .catch(() => false);
  // 失败（比如 vendor 还没拉）时清掉缓存，下次刷新还会再试一次。
  vendorPromise.then((ok) => {
    if (!ok) vendorPromise = null;
  });
  return vendorPromise;
}

function appendScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`加载失败：${src}`));
    document.head.appendChild(script);
  });
}

/** 挂载一次 Vue 应用，之后所有刷新都走 setJsonData，避免反复销毁重建。 */
function ensureCloud() {
  if (cloud) return cloud;
  const { createApp, h } = window.Vue;
  const RelationGraph = window.RelationGraph.RelationGraph;
  let graph = null;
  const app = createApp({
    render: () =>
      h(RelationGraph, {
        ref: (instance) => {
          if (instance) graph = instance;
        },
        options: CLOUD_OPTIONS,
        onNodeClick: (node) => showNodeDetail(node),
        onLineClick: (line) => showLineDetail(line),
      }),
  });
  app.mount(els.canvas);
  cloud = { app, graph };
  return cloud;
}

function destroyCloud() {
  if (!cloud) return;
  cloud.app.unmount();
  cloud = null;
  if (els.canvas) els.canvas.innerHTML = '';
}

function showNodeDetail(node) {
  const data = (node && node.data) || {};
  const relations = data.relations || [];
  const head = `${data.name || node?.text || '?'} · ${KIND_LABELS[data.kind] || '其他'}` +
    ` · ${data.degree || 0} 条关系`;
  if (!els.detail) return;
  els.detail.textContent = relations.length
    ? `${head}：${relations.slice(0, 8).join('；')}${relations.length > 8 ? ' …' : ''}`
    : head;
}

function showLineDetail(line) {
  const data = (line && line.data) || {};
  if (els.detail) els.detail.textContent = `${data.a || '?'} ${data.label || '关联'} ${data.b || '?'}`;
}


/** 把后端的 entities/relations 转成「人物 + 关系」，同时算出每个人的连接度。 */
function analyzeGraph(relations, entities) {
  const byId = new Map();
  const byName = new Map();
  for (const entity of entities) {
    byId.set(entity.id, {
      id: entity.id,
      name: entity.canonical_name || '?',
      kind: entity.kind || 'other',
      degree: 0,
      relations: [],
      root: false,
    });
    byName.set(entity.canonical_name, byId.get(entity.id));
  }
  const ensure = (id, name) => {
    if (id && byId.has(id)) return byId.get(id);
    if (name && byName.has(name)) return byName.get(name);
    const node = {
      id: id || name || `unknown-${byId.size}`,
      name: name || '?',
      kind: 'other',
      degree: 0,
      relations: [],
      root: false,
    };
    byId.set(node.id, node);
    if (name) byName.set(name, node);
    return node;
  };

  const edges = [];
  for (const rel of relations) {
    const a = ensure(rel.a_entity, rel.a_name);
    const b = ensure(rel.b_entity, rel.b_name);
    if (!a || !b || a === b) continue;
    const label = rel.rel_type || '';
    edges.push({ a, b, label });
    a.degree += 1;
    b.degree += 1;
    if (label) {
      a.relations.push(`→ ${b.name}（${label}）`);
      b.relations.push(`← ${a.name}（${label}）`);
    }
  }

  return { nodes: [...byId.values()], edges };
}

/**
 * 按「根人物 → 一跳 → 两跳 …」的顺序挑人：
 * 先把根人物全部放进来，再一层层往外补，直到达到人物上限。
 * 与根人物完全不连通的人算作孤立，只有打开开关才会被追加（同样受上限约束）。
 */
function selectCloud(nodes, edges, config) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const neighbours = new Map();
  for (const edge of edges) {
    if (!neighbours.has(edge.a.id)) neighbours.set(edge.a.id, []);
    if (!neighbours.has(edge.b.id)) neighbours.set(edge.b.id, []);
    neighbours.get(edge.a.id).push(edge.b.id);
    neighbours.get(edge.b.id).push(edge.a.id);
  }

  // 用户手选的根人物一律保留（哪怕它当前没有任何关系），没选过才回落到默认
  let roots = (config.roots || []).map((id) => byId.get(id)).filter(Boolean);
  if (!roots.length) roots = defaultRoots(nodes).map((id) => byId.get(id)).filter(Boolean);

  const limit = Math.max(config.max, roots.length);
  const order = []; // 广度优先顺序：根人物 → 一跳邻居 → 两跳邻居 …
  const seen = new Set();
  const queue = [];
  for (const root of roots) {
    if (seen.has(root.id)) continue;
    seen.add(root.id);
    queue.push(root.id);
  }
  for (let head = 0; head < queue.length; head += 1) {
    const id = queue[head];
    order.push(id);
    for (const next of neighbours.get(id) || []) {
      if (seen.has(next)) continue;
      seen.add(next);
      queue.push(next);
    }
  }

  const chosen = new Set(order.slice(0, limit));
  const isolatedIds = new Set();
  if (config.showIsolated) {
    // 已下探补进来的人不再算孤立
    const rest = nodes
      .filter((node) => !seen.has(node.id) && !chosen.has(node.id))
      .sort((a, b) => b.degree - a.degree);
    for (const node of rest) {
      if (isolatedIds.size >= limit) break;
      chosen.add(node.id);
      isolatedIds.add(node.id);
    }
  }

  // 下探：把用户点开的人物的直接关系人补进来（不受人物上限限制，但有硬上限）
  const drilledRoots = config.drilled instanceof Set ? config.drilled : new Set();
  const drilledIds = new Set();
  if (drilledRoots.size) {
    const candidates = new Set();
    for (const id of drilledRoots) {
      if (!byId.has(id)) continue;
      for (const next of neighbours.get(id) || []) {
        if (!chosen.has(next)) candidates.add(next);
      }
    }
    const room = Math.max(0, DRILL_HARD_CAP - chosen.size);
    const extra = [...candidates]
      .map((id) => byId.get(id))
      .filter(Boolean)
      .sort((a, b) => b.degree - a.degree)
      .slice(0, room);
    for (const node of extra) {
      chosen.add(node.id);
      drilledIds.add(node.id);
    }
  }

  const rootIds = new Set(roots.map((node) => node.id));
  const shown = nodes.filter((node) => chosen.has(node.id));
  for (const node of shown) {
    node.root = rootIds.has(node.id);
    node.isolated = isolatedIds.has(node.id);
    node.drillAdded = drilledIds.has(node.id);
    node.drillOpen = drilledRoots.has(node.id);
    // 还有几个直接关系人没画出来：决定气泡上要不要显示「＋」
    node.drillCount = (neighbours.get(node.id) || []).filter((id) => !chosen.has(id)).length;
  }
  placeCloud(shown, config.positions);
  if (config.positions) {
    for (const node of shown) config.positions.set(node.id, { x: node.x, y: node.y });
  }

  const kept = edges.filter((edge) => chosen.has(edge.a.id) && chosen.has(edge.b.id)).slice(0, MAX_EDGES);

  return {
    isolated: isolatedIds.size,
    drilled: drilledIds.size,
    nodes: shown.map((node) => ({
      id: node.id,
      text: node.name,
      innerHTML: bubbleHtml(node),
      width: bubbleSize(node.degree),
      height: bubbleSize(node.degree),
      x: node.x,
      y: node.y,
      fixed: true, // 位置由 placeCloud 算好，不让组件再用随机力导向跑一遍
      color: 'transparent',
      borderWidth: 0,
      styleClass:
        'cloud-node' +
        (node.isolated ? ' cloud-isolated' : '') +
        (node.drillAdded ? ' cloud-drilled' : ''),
      data: { name: node.name, kind: node.kind, degree: node.degree, relations: node.relations },
    })),
    lines: kept.map((edge) => ({
      from: edge.a.id,
      to: edge.b.id,
      text: edge.label,
      styleClass: 'cloud-line',
      opacity: 0.55,
      lineWidth: 1.5,
      showStartArrow: false,
      showEndArrow: false,
      data: { label: edge.label, a: edge.a.name, b: edge.b.name },
    })),
  };
}

/** 气泡直径：度数越大越大，62–104px。 */
function bubbleSize(degree) {
  return Math.round(62 + Math.min(42, Math.sqrt(Math.max(degree, 1)) * 7));
}

/**
 * 云图坐标：按度数从高到低螺旋铺开（向日葵式），高连接度的人物落在中心，
 * 边缘的角色依次向外，整张图聚成一个圆盘。坐标写死后由 fitCloud 统一缩放到画布。
 *
 * 半径按「已占用面积」累加而不是按下标均分：中心的气泡本来就大，
 * 用均分半径会把它们挤成一坨；按面积算则大节点自动多占位置。
 */
const CLOUD_GOLDEN = Math.PI * (3 - Math.sqrt(5)); // 黄金角，避免节点排成一条条直线
const CLOUD_PACK = 0.45; // 目标填充率：气泡之间留出约半个直径的空隙，避免中心大节点叠在一起

function placeCloud(nodes, cache) {
  // 根人物排在最前（落在圆心附近），其次是连通人物（按度数），孤立人物统一放外圈
  const sorted = [...nodes].sort((a, b) => {
    if (a.root !== b.root) return a.root ? -1 : 1;
    if (a.isolated !== b.isolated) return a.isolated ? 1 : -1;
    return b.degree - a.degree;
  });
  let area = 0; // 已占用的圆面积之和
  const occupied = []; // 已摆好的圆心与半径，给下探补进来的新节点避让
  sorted.forEach((node, index) => {
    const size = bubbleSize(node.degree);
    const radius = size / 2;
    const cached = cache?.get(node.id);
    if (cached) {
      // 已经在图上的节点保持原位，下探时整张图不会跳
      node.x = cached.x;
      node.y = cached.y;
    } else {
      // 从「按面积算出的半径」起步；被挡住就沿黄金角往外挪，直到不重叠
      let x = 0;
      let y = 0;
      for (let attempt = 0; attempt < 24; attempt += 1) {
        const spread = Math.sqrt(area / (Math.PI * CLOUD_PACK)) + attempt * (size * 0.55);
        const angle = (index + attempt) * CLOUD_GOLDEN;
        x = Math.round(spread * Math.cos(angle) - radius);
        y = Math.round(spread * Math.sin(angle) - radius);
        if (!overlaps(occupied, x + radius, y + radius, radius)) break;
      }
      node.x = x;
      node.y = y;
    }
    occupied.push({ x: node.x + radius, y: node.y + radius, r: radius });
    area += Math.PI * radius * radius;
  });
}

function overlaps(occupied, cx, cy, r) {
  return occupied.some((item) => Math.hypot(item.x - cx, item.y - cy) < item.r + r + 2);
}

/** 把组件实例里当前节点坐标（拖动后会变）抄回缓存，供下探时保持原位。 */
function snapshotPositions() {
  const instance = typeof cloud?.graph?.getInstance === 'function'
    ? cloud.graph.getInstance()
    : cloud?.graph;
  for (const node of instance?.graphData?.nodes || []) {
    if (typeof node.x === 'number' && typeof node.y === 'number') {
      state.posCache.set(node.id, { x: node.x, y: node.y });
    }
  }
}

/** 节点内容用 HTML 画：渐变气泡 + 名字 + 度数角标 + 下探按钮（颜色由 styles.css 按类型给）。 */
function bubbleHtml(node) {
  const size = bubbleSize(node.degree);
  const chars = Math.max(1, [...node.name].length);
  // 名字最多占 2 个字宽（更长的自动换行）；字号不随字数缩得太小，整图缩放后还要能看清
  const font = Math.max(12, Math.min(26, Math.floor((size * 1.05) / Math.min(chars, 2))));
  const badge = node.degree > 1 ? `<em class="cloud-degree">${node.degree}</em>` : '';
  let drill = '';
  if (node.drillOpen || node.drillCount > 0) {
    const title = node.drillOpen
      ? '收起这一层'
      : `下探：展开 ${node.drillCount} 个直接关系人`;
    drill =
      `<button type="button" class="cloud-drill${node.drillOpen ? ' is-open' : ''}"` +
      ` data-drill-id="${escapeHtml(node.id)}" title="${escapeHtml(title)}">` +
      `${node.drillOpen ? '−' : '＋'}</button>`;
  }
  return (
    `<span class="cloud-bubble kind-${kindClass(node.kind)}" style="font-size:${font}px">` +
    `<span class="cloud-name">${escapeHtml(node.name)}</span>${badge}${drill}</span>`
  );
}


function kindClass(kind) {
  return ['person', 'place', 'org', 'item'].includes(kind) ? kind : 'other';
}
