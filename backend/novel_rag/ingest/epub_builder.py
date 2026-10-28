"""TXT → 内存 EPUB3（决策 #22）。

只做两件事：切块 + XML 转义。**不规范化空白、不改写任何字符**，
否则 ``seq`` 指向的原文会与用户实际看到的差几个字符（风险 #3）。

产出的 EPUB 直接交给 foliate-js 渲染，章节识别结果即 nav + spine。
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

from ..domain.models import Chapter

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _xhtml(title: str, body: str) -> str:
    paragraphs = "".join(
        f"<p>{escape(line)}</p>\n" for line in body.split("\n") if line.strip()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">\n'
        f"<head><title>{escape(title)}</title>"
        '<link rel="stylesheet" href="style.css"/></head>\n'
        f"<body><h1>{escape(title)}</h1>\n{paragraphs}</body>\n</html>\n"
    )


def _nav(chapters: list[Chapter]) -> str:
    items = "".join(
        f'<li><a href="{ch.nav_href}">{escape(ch.title)}</a></li>\n' for ch in chapters
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        "<head><title>目录</title></head>\n"
        f'<body><nav epub:type="toc" id="toc"><h1>目录</h1><ol>\n{items}</ol></nav></body>\n</html>\n'
    )


def _opf(title: str, author: str, chapters: list[Chapter]) -> str:
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    for spine_index, ch in enumerate(chapters):
        item_id = f"ch{spine_index + 1:04d}"
        manifest.append(f'<item id="{item_id}" href="{ch.nav_href}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{item_id}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'<dc:identifier id="bookid">novel-rag</dc:identifier>\n'
        f"<dc:title>{escape(title)}</dc:title>\n"
        f"<dc:language>zh-CN</dc:language>\n"
        + (f"<dc:creator>{escape(author)}</dc:creator>\n" if author else "")
        + "</metadata>\n<manifest>\n"
        + "\n".join(manifest)
        + "\n</manifest>\n<spine>\n"
        + "\n".join(spine)
        + "\n</spine>\n</package>\n"
    )


_CSS = """html { font-size: 100%; }
body { margin: 0 auto; padding: 1em; line-height: 1.75; }
h1 { font-size: 1.25em; margin: 1em 0; text-align: center; }
p { text-indent: 2em; margin: 0 0 0.6em; }
"""


def build_epub(title: str, author: str, chapters: list[Chapter]) -> bytes:
    """返回完整的 EPUB 字节流。调用方可直接写入磁盘或流式返回。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须是第一个条目且不压缩
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _opf(title, author, chapters))
        zf.writestr("OEBPS/nav.xhtml", _nav(chapters))
        zf.writestr("OEBPS/style.css", _CSS)
        for spine_index, ch in enumerate(chapters):
            zf.writestr(f"OEBPS/ch{spine_index + 1:04d}.xhtml", _xhtml(ch.title, ch.body))
    return buf.getvalue()
