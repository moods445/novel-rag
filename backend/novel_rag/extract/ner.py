"""人名识别（NER）：内置 CPU 小模型 + 规则。

三档后端，配置项 ``NOVEL_RAG_NER_BACKEND``：

- ``jieba``：jieba 词性标注取 ``nr``，纯 CPU、词典约 5 MB。
  《三国演义》上「曹操/孔明/玄德/关公」这类高频实体的召回远超正则。
- ``regex``：对话/动作标记前缀 + 剥尾，零依赖兜底。
- ``auto``（默认）：jieba 可用就 **jieba ∪ regex** 求并集，
  因为文言文里仍有 jieba 标错词性的名字，正则能补上一部分。

另外用「姓X名Y字Z」规则抽别名对（刘备↔玄德），
这是高精度模式——它给实体归一提供了**原文锚定**的别名证据，
否则离线模式下玄德和刘备会成为两个人。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------- 通用清洗

# 白话 + 文言的对话/动作标记
_VERBS = (
    "说道|说|道|问道|问曰|问|答曰|答道|答|曰|喊道|喊|笑道|笑曰|笑|叫道|叫|"
    "叹道|叹曰|叹|点头|摇头|皱眉|冷笑|沉声|"
    "站在|坐在|抬头|转身|出手|握紧|侧身|"
    "大喜|大惊|大怒|大骂|大喝|大笑道|骂道|喝道"
)
_SPEAK = re.compile(rf"([\u4e00-\u9fa5]{{2,4}})(?={_VERBS})")

_STOPWORDS = {
    "他们", "我们", "你们", "众人", "大家", "一边", "此时", "然后", "忽然",
    "只是", "可是", "那个", "这个", "自己", "什么", "怎么", "为何", "只见", "不由",
    "心中", "脸上", "声音", "时候", "地方", "东西", "事情", "一个", "两个", "一下",
    "一阵", "人有诗", "有诗曰", "后人有", "后人", "有诗",
    "左右", "夫人", "诸将", "众将", "众官", "二人", "三人", "一人", "众军", "军士",
    "厉声", "大呼", "大呼曰", "厉声曰", "内容简介", "作者", "佚名", "无名氏",
}
_BAD_CHARS = set("的了是在和与就都也还只不我你他她它们这那")
_FUNCTION_HEAD = set("有后众皆一只但若虽遂乃且今时此其何未可使令而则故因人书回诸大")
# 尾部需要剥离的动词/副词（「孔明大笑曰」→「孔明大」→「孔明」）
_TAIL_STRIP = set("问道说答喊笑叫点摇皱站坐转出握侧抬望走跑喝骂哭怒冷沉哼看听想曰诗惊喜忙便即再又皆尽大赞叹声")
# 地名/集体名词后缀，出现即判为噪声（「魏兵」「巨鹿郡」）
_PLACE_SUFFIX = set("兵军将士众郡县州城寨营宫殿关府氏等")

# 「姓刘名备，字玄德」/「姓曹，名操，字孟德」
_ALIAS_RE = re.compile(
    r"姓([\u4e00-\u9fa5])[，,、]?\s*名([\u4e00-\u9fa5]{1,3})[，,、]?\s*字([\u4e00-\u9fa5]{1,3})"
)


def clean_name(name: str) -> str:
    while len(name) > 2 and (name[-1] in _TAIL_STRIP):
        name = name[:-1]
    return name


def is_plausible(name: str) -> bool:
    if len(name) < 2 or len(name) > 4:
        return False
    if name in _STOPWORDS:
        return False
    if any(ch in _BAD_CHARS for ch in name):
        return False
    if name[0] in _FUNCTION_HEAD or name[-1] in _TAIL_STRIP:
        return False
    if name[-1] in _PLACE_SUFFIX:
        return False
    return True


# ---------------------------------------------------------------- 结果结构


@dataclass(slots=True)
class NerResult:
    names: list[tuple[str, int]] = field(default_factory=list)
    alias_pairs: list[tuple[str, str]] = field(default_factory=list)

    def dictionary(self, top: int = 60) -> list[str]:
        return [name for name, _ in self.names[:top]]

    def alias_map(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for canonical, alias in self.alias_pairs:
            bucket = out.setdefault(canonical, [])
            if alias not in bucket:
                bucket.append(alias)
        return out


class NerBackend(Protocol):
    name: str

    def scan(self, text: str, *, min_count: int = 2, top: int = 60) -> NerResult: ...


# ---------------------------------------------------------------- 规则后端


class RegexNer:
    name = "regex"

    def scan(self, text: str, *, min_count: int = 2, top: int = 60) -> NerResult:
        counter: Counter[str] = Counter()
        for m in _SPEAK.finditer(text):
            name = clean_name(m.group(1))
            if is_plausible(name):
                counter[name] += 1
        return NerResult(
            names=[(n, c) for n, c in counter.most_common(top) if c >= min_count],
            alias_pairs=scan_alias_pairs(text),
        )


# ---------------------------------------------------------------- jieba 后端


class JiebaNer:
    """jieba 词性标注取 ``nr``。首次调用会加载词典（约 0.5s）。"""

    name = "jieba"

    def __init__(self) -> None:
        self._pseg = None

    def _load(self):
        if self._pseg is None:
            import jieba.posseg as pseg  # noqa: PLC0415

            self._pseg = pseg
        return self._pseg

    def scan(self, text: str, *, min_count: int = 2, top: int = 60) -> NerResult:
        pseg = self._load()
        counter: Counter[str] = Counter()
        for word, flag in pseg.cut(text):
            if flag != "nr":
                continue
            name = clean_name(word)
            if is_plausible(name):
                counter[name] += 1
        return NerResult(
            names=[(n, c) for n, c in counter.most_common(top) if c >= min_count],
            alias_pairs=scan_alias_pairs(text),
        )


class CompositeNer:
    """并集：jieba 保召回，正则补漏。

    两个后端数的是**同一批出现**，因此计数取 max 而不是相加——
    相加会让同一个名字被数两遍，把「出现次数阈值」直接架空。
    """

    def __init__(self, backends: list[NerBackend]) -> None:
        self.backends = backends
        self.name = "+".join(b.name for b in backends)

    def scan(self, text: str, *, min_count: int = 2, top: int = 60) -> NerResult:
        counter: Counter[str] = Counter()
        aliases: dict[tuple[str, str], None] = {}
        for backend in self.backends:
            result = backend.scan(text, min_count=1, top=top * 2)
            for name, count in result.names:
                if count > counter[name]:
                    counter[name] = count
            for pair in result.alias_pairs:
                aliases[pair] = None
        counter = _drop_contained(counter)
        return NerResult(
            names=[(n, c) for n, c in counter.most_common(top) if c >= min_count],
            alias_pairs=list(aliases),
        )


def _drop_contained(counter: Counter[str]) -> Counter[str]:
    """合并「同一名字被切短」的碎片。

    jieba 对生僻名会切出前缀（「苏清月」→「苏清」），若不处理，
    同一个人会变成两个实体。判定条件：短名是长名的前缀，且出现次数
    不比长名多太多（长名罕见时才保留长名）。
    """
    items = list(counter.items())
    drop: set[str] = set()
    for short, short_count in items:
        if len(short) < 2:
            continue
        for long, long_count in items:
            if long == short or len(long) <= len(short):
                continue
            if long.startswith(short) and short_count <= long_count * 1.5:
                drop.add(short)
                break
    return Counter({k: v for k, v in items if k not in drop})


# ---------------------------------------------------------------- 通用工具


def scan_alias_pairs(text: str) -> list[tuple[str, str]]:
    """「姓X名Y字Z」→ ``(姓+名, 字)``。高精度，宁缺毋滥。"""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for surname, given, courtesy in _ALIAS_RE.findall(text):
        canonical = clean_name(surname + given)
        alias = clean_name(courtesy)
        if len(alias) < 2:
            continue
        if not is_plausible(canonical) or not is_plausible(alias):
            # 名字里可能带动词字，放宽一次：只要求长度与脏字符
            if not (_ok_len(canonical) and _ok_len(alias)):
                continue
        key = (canonical, alias)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def _ok_len(name: str) -> bool:
    return 2 <= len(name) <= 4 and not any(ch in _BAD_CHARS for ch in name)


# ---------------------------------------------------------------- 工厂

_default: NerBackend | None = None


def build_ner(backend: str = "auto") -> NerBackend:
    backend = (backend or "auto").lower()
    if backend == "regex":
        return RegexNer()
    if backend == "jieba":
        return JiebaNer()
    try:
        import jieba.posseg  # noqa: F401, PLC0415

        return CompositeNer([JiebaNer(), RegexNer()])
    except ImportError:
        return RegexNer()


def default_ner() -> NerBackend:
    global _default
    if _default is None:
        from ..config import get_settings

        _default = build_ner(get_settings().ner_backend)
    return _default


def reset_default() -> None:
    global _default
    _default = None
