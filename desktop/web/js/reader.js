// 阅读平面：foliate-js 渲染 + 位置回传。
//
// 位置双轴（架构决策 #8）：foliate 给的是 CFI（渲染坐标），
// 后端要的是 seq（业务坐标）。这里用 CFI 的 spine 步长反推章节，
// 再由章节的 seq_end 作为阅读进度上界，避免逐段换算。
import { api } from './api.js';

let view = null;
let chapters = [];
let bookId = null;
let lastSeq = 0;
// 最近一次上报的 CFI：滚动到哪就记到哪，刷新后能落回原位置而不是章节开头
let lastCfi = '';
let pending = null;
let theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
// 阅读流：默认 scrolled（连贯滚动，正文一条长卷），↑↓ 分步滚动、←→ 翻一屏。
let flow = 'scrolled';
// 键盘绑定的 iframe 文档集合，每次换章 foliate 都会新建 iframe，需要重复绑定。
const boundDocs = new WeakSet();
// foliate 的 load/relocate 只绑一次，换书时用这里的钩子转给调用方。
let wired = false;
let relocateHook = null;

export async function ensureView(element) {
  if (view) return view;
  // vendor 目录由 desktop/scripts/prepare_vendor.py 在构建前拉取
  await import('../vendor/foliate-js/view.js');
  view = element;
  return view;
}

export function setBook(id, chapterList) {
  bookId = id;
  chapters = chapterList || [];
  // 换书：上一本的位置不能当成本书的起点
  lastSeq = 0;
  lastCfi = '';
}

/** 全局明暗主题切换后，把正文 iframe 的底色与字色一起换掉。 */
export function applyTheme(next) {
  theme = next === 'light' ? 'light' : 'dark';
  if (!view) return;
  try {
    view.renderer?.setStyles?.(themeStyles());
  } catch (error) {
    console.warn('applyTheme failed', error);
  }
}

function themeStyles() {
  const style = getComputedStyle(document.documentElement);
  const bg = style.getPropertyValue('--reader-bg').trim() || '#f7f7f5';
  const fg = style.getPropertyValue('--reader-text').trim() || '#1f2937';
  const accent = style.getPropertyValue('--accent').trim() || '#2dd4bf';
  return `
    html, body { background: ${bg} !important; color: ${fg} !important; }
    a { color: ${accent} !important; }
    ::selection { background: ${accent}55; }
  `;
}

export function spineOfCfi(cfi) {
  const match = /^epubcfi\(\/6\/(\d+)/.exec(String(cfi || ''));
  if (!match) return -1;
  return Math.floor(Number(match[1]) / 2) - 1;
}

export function chapterAtSpine(spine) {
  return chapters.find((c) => c.spine_index === spine) || null;
}

export async function open(book, { onRelocate, lastCfi: resumeCfi } = {}) {
  const blob = await api.blob(`/api/books/${book.id}/epub`);
  const file = new File([blob], `${book.title || 'book'}.epub`, {
    type: 'application/epub+zip',
  });
  // 换书：先把旧渲染器和旧 iframe 拆掉，否则 shadow DOM 里会一层层堆渲染器，
  // 旧 iframe 的键盘监听也跟着残留。
  if (view.book) {
    try {
      view.close();
    } catch (error) {
      console.warn('close previous book failed', error);
    }
  }
  await view.open(file);
  relocateHook = typeof onRelocate === 'function' ? onRelocate : null;
  if (!wired) {
    wired = true;
    view.addEventListener('load', (event) => bindKeysToDoc(event.detail?.doc));
    view.addEventListener('relocate', (event) => onRelocateEvent(event.detail || {}));
  }
  // 先定好排版再 init：第一次渲染就是连贯滚动。
  applyFlow();
  // 刷新/重开时从服务端存下的 CFI 续读，解析不了才退回正文第一屏。
  if (!(resumeCfi && (await initAt(resumeCfi)))) await view.init({ showTextStart: true });
  applyTheme(theme);
  bindKeysToDoc(view.renderer?.getContents?.()[0]?.doc);

  return view;
}

async function initAt(cfi) {
  try {
    if (!view?.resolveNavigation(cfi)) return false;
    await view.init({ lastLocation: cfi });
    return true;
  } catch (error) {
    console.warn('restore position failed', error);
    return false;
  }
}

function onRelocateEvent(detail) {
  const cfi = detail.cfi || '';
  const chapter = chapterAtSpine(spineOfCfi(cfi));
  const seq = chapter && chapter.seq_end ? chapter.seq_end : 0;
  // 章节变了要存（seq 是防剧透的上界），同一章里滚动过也要存（CFI 决定续读落点）。
  if (cfi && (cfi !== lastCfi || (seq && seq !== lastSeq))) {
    lastCfi = cfi;
    if (seq) lastSeq = seq;
    saveProgress(seq || lastSeq, cfi);
  }
  if (relocateHook) relocateHook(detail, chapter);
}

function saveProgress(seq, cfi) {
  if (!bookId || !seq) return;
  if (pending) clearTimeout(pending);
  pending = setTimeout(() => {
    api
      .put(`/api/books/${bookId}/progress`, { progress_seq: seq, progress_cfi: cfi })
      .catch(() => {
        /* 进度丢失不致命 */
      });
  }, 800);
}

export function toc() {
  const list = [];
  const walk = (items, depth) => {
    for (const item of items || []) {
      list.push({ label: item.label || item.href, href: item.href, depth });
      walk(item.subitems, depth + 1);
    }
  };
  walk(view?.book?.toc, 0);
  return list;
}

export async function goTo(target) {
  if (!view || !target) return false;
  try {
    const resolved = view.resolveNavigation(target);
    if (!resolved) return false;
    await view.goTo(target);
    return true;
  } catch (error) {
    console.warn('goTo failed', target, error);
    return false;
  }
}

/** 引用跳转：先按 CFI，再退回章节 href。 */
export async function goToCitation(citation) {
  if (!citation) return false;
  if (citation.cfi && (await goTo(citation.cfi))) return true;
  const chapterNo = citation.chapter_no;
  if (chapterNo) {
    const chapter = chapters.find((c) => c.idx === chapterNo - 1);
    if (chapter && chapter.nav_href && (await goTo(chapter.nav_href))) return true;
  }
  return false;
}

export async function goToChapter(delta) {
  const current = spineOfCfi(view?.lastLocation?.cfi || '');
  const next = Math.min(Math.max(current + delta, 0), chapters.length - 1);
  const chapter = chapters.find((c) => c.spine_index === next);
  if (chapter) await goTo(chapter.nav_href);
}

// ---------------------------------------------------------------- 排版与键盘

/**
 * 切换阅读流：`scrolled` 连贯滚动（默认，正文一条长卷，↑↓ 滚动不打断阅读），
 * `paged` 分栏翻页。切换时 foliate 会按当前锚点重新排版，不会跳段。
 */
export function setFlow(mode) {
  flow = mode === 'paged' ? 'paged' : 'scrolled';
  applyFlow();
  return flow;
}

function applyFlow() {
  const renderer = view?.renderer;
  if (!renderer) return;
  try {
    if (flow === 'scrolled') renderer.setAttribute('flow', 'scrolled');
    else renderer.removeAttribute('flow');
  } catch (error) {
    console.warn('applyFlow failed', error);
  }
}

/** 上下键的滚动步长：约一屏的 1/6，最少 96px。 */
function stepSize() {
  const height = view?.getBoundingClientRect?.().height || 640;
  return Math.max(96, Math.round(height / 6));
}

/**
 * 真的到头了才拦：本章还有可滚的余量就交给 foliate（它会自己跨章交接），
 * 没余量且已经是全书首/末时才停手，否则 foliate 会拿越界章节去 load 而抛错。
 */
function blockedAtEdge(direction) {
  const renderer = view?.renderer;
  if (!renderer) return true;
  const edge = direction < 0 ? Boolean(renderer.atStart) : Boolean(renderer.atEnd);
  if (!edge) return false;
  const { size, start, viewSize } = renderer;
  if (![size, start, viewSize].every((n) => Number.isFinite(n))) return true;
  const room = direction < 0 ? start > 2 : viewSize - (start + size) > 2;
  return !room;
}

// 连按排队：foliate 的 #turnPage 内部有锁，锁住期间的调用会被静默丢弃
//（换章 + 100ms 动画期间正好是锁住的），表现就是"快速连按/连按几下才动一下"。
// 这里把每次按键串成一条链，前一次做完再做下一次，键不丢。
let chain = Promise.resolve();

function enqueue(task) {
  chain = chain.then(task).catch((error) => {
    console.warn('reader action failed', error);
    return false;
  });
  return chain;
}

function turn(direction, distance) {
  return enqueue(async () => {
    if (!view || typeof view.next !== 'function' || !view.renderer) return false;
    if (blockedAtEdge(direction)) return false;
    // 自定义距离 = 滚动；不传距离 = 翻一屏。foliate 内部会跨章交接。
    if (direction > 0) await view.next(distance);
    else await view.prev(distance);
    return true;
  });
}

export function pageNext() {
  return turn(1);
}

export function pagePrev() {
  return turn(-1);
}

/**
 * 上下滚动（不是翻页）：按一屏的 1/6 递进，滚到本章边界由 foliate 交接给上一/下一章，
 * 所以正文始终是连贯的。
 */
export function scrollByStep(direction) {
  // distance 只传正数，方向由 foliate 的 prev/next 决定（它内部会自己加/减）
  return turn(direction, stepSize());
}

/**
 * ←→ / PgDn / 空格 翻一屏，↑↓ 滚动，其他键放行。返回是否已经处理。
 *
 * 渲染器还没起来时**不吞键**（返回 false，preventDefault 也不做），
 * 否则打开书的前几秒按什么都没反应，看着就像"键盘失效"。
 * 输入框/可编辑区里一律放行，否则对话框里的方向键会被抢走。
 */
export function handleKeyEvent(event) {
  if (!view || !view.renderer || event.defaultPrevented) return false;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
  const target = event.target;
  const tag = target?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return false;
  if (target?.isContentEditable) return false;

  const key = event.key || '';
  const code = event.code || '';
  const is = (...names) => names.includes(key) || names.includes(code);

  if (is('ArrowRight', 'PageDown', 'Space')) {
    event.preventDefault();
    pageNext();
    return true;
  }
  if (is('ArrowLeft', 'PageUp')) {
    event.preventDefault();
    pagePrev();
    return true;
  }
  if (is('ArrowDown')) {
    event.preventDefault();
    scrollByStep(1);
    return true;
  }
  if (is('ArrowUp')) {
    event.preventDefault();
    scrollByStep(-1);
    return true;
  }
  return false;
}

/**
 * 正文 iframe 内的按键不会冒泡到主文档，得逐个 doc 绑。
 *
 * 注意 foliate 给正文 iframe 加了 `sandbox="allow-same-origin allow-scripts"`，
 * 浏览器控制台会提示 "An iframe which has both allow-scripts and allow-same-origin
 * ... can escape its sandboxing"。这是 foliate-js 有意的设置（见 vendor 的
 * paginator.js）：`allow-scripts` 是 WebKit 下 iframe 内事件能工作的前提，
 * `allow-same-origin` 是本文件注入主题、取选区、绑键盘的前提。两者都不能去掉，
 * 警告本身只是一条提示，不影响滚动/翻屏；正文来自用户自己导入的 EPUB，风险可控。
 */
function bindKeysToDoc(doc) {
  if (!doc || boundDocs.has(doc)) return;
  boundDocs.add(doc);
  try {
    // capture：比正文里可能存在的其他监听更早拿到方向键
    doc.addEventListener('keydown', (event) => handleKeyEvent(event), true);
    doc.defaultView?.addEventListener('keydown', (event) => handleKeyEvent(event), true);
  } catch (error) {
    console.warn('bind keys to iframe failed', error);
  }
}

export function currentCfi() {
  return view?.lastLocation?.cfi || '';
}

/** 划线：取当前选区的 CFI。foliate 的 getCFI(index, range) 需要 range 参数。 */
export function selectionCfi() {
  try {
    const location = view?.lastLocation;
    if (!view || !location) return '';
    return view.getCFI(spineOfCfi(location.cfi), location.range) || '';
  } catch {
    return '';
  }
}

export function selectionText() {
  try {
    const selection = view?.renderer?.getContents?.()?.[0]?.doc?.getSelection?.();
    return selection ? selection.toString() : '';
  } catch {
    return '';
  }
}
