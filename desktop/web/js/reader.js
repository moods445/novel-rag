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
let pending = null;

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
}

export function spineOfCfi(cfi) {
  const match = /^epubcfi\(\/6\/(\d+)/.exec(String(cfi || ''));
  if (!match) return -1;
  return Math.floor(Number(match[1]) / 2) - 1;
}

export function chapterAtSpine(spine) {
  return chapters.find((c) => c.spine_index === spine) || null;
}

export async function open(book, { onRelocate } = {}) {
  const blob = await api.blob(`/api/books/${book.id}/epub`);
  const file = new File([blob], `${book.title || 'book'}.epub`, {
    type: 'application/epub+zip',
  });
  await view.open(file);
  await view.init({});

  view.addEventListener('relocate', (event) => {
    const detail = event.detail || {};
    const chapter = chapterAtSpine(spineOfCfi(detail.cfi));
    const seq = chapter && chapter.seq_end ? chapter.seq_end : 0;
    if (seq && seq !== lastSeq) {
      lastSeq = seq;
      saveProgress(seq, detail.cfi || '');
    }
    if (typeof onRelocate === 'function') onRelocate(detail, chapter);
  });

  return view;
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
