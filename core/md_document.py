"""Markdown を印刷用HTML／PDFにするだけの薄い層.

朝会1枚（morning_brief）と判定ルールブック（rulebook）は Markdown を正としつつ、
「印刷して会議に持ち込む」「PDFで配る」需要があるためここで変換する。
体裁は詳細レポート（report_html.CSS）と揃える。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:  # weasyprint はネイティブ依存があるため任意
    from weasyprint import HTML as WeasyHTML  # type: ignore

    _WEASYPRINT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _WEASYPRINT_AVAILABLE = False


_EXTRA_CSS = """
/* Markdown由来の文書用（朝会1枚・ルールブック） */
body { max-width: 900px; margin: 0 auto; padding: 24px; }
h1 { font-size: 20pt; border-bottom: 3px solid #1f4e79; padding-bottom: 6px; }
h2 { font-size: 14pt; margin-top: 22px; border-left: 6px solid #1f4e79;
     padding-left: 8px; background: #f4f7fb; }
h3 { font-size: 12pt; margin-top: 16px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0 14px; font-size: 10pt; }
th, td { border: 1px solid #c8d3e0; padding: 5px 7px; text-align: left;
         vertical-align: top; }
th { background: #eef3f9; }
blockquote { margin: 8px 0; padding: 8px 12px; background: #fffbe6;
             border-left: 4px solid #e0b400; }
code { background: #f2f4f7; padding: 1px 4px; border-radius: 3px; font-size: 9.5pt; }
ul { margin: 6px 0 10px; padding-left: 20px; }
li { margin: 2px 0; }
hr { border: none; border-top: 1px solid #ccd; margin: 18px 0 10px; }
@page { size: A4; margin: 14mm; }
"""


def markdown_to_html_document(title: str, markdown_text: str) -> str:
    """Markdown を単体で開けるHTML文書にする.

    `markdown` パッケージが無い環境では <pre> にフォールバックする
    （体裁は崩れるが内容は失わない）。
    """
    try:
        import markdown as _md

        body = _md.markdown(
            markdown_text,
            extensions=["tables", "sane_lists", "nl2br"],
        )
    except Exception:  # noqa: BLE001
        logger.warning("markdown パッケージが使えないため <pre> で出力します")
        escaped = (
            markdown_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        body = f"<pre>{escaped}</pre>"

    try:
        from .report_html import CSS as _REPORT_CSS
    except Exception:  # noqa: BLE001
        _REPORT_CSS = ""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{_REPORT_CSS}{_EXTRA_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def html_to_pdf(html: str) -> Optional[bytes]:
    """HTMLをPDFにする。weasyprint 未インストール時は None."""
    if not _WEASYPRINT_AVAILABLE:
        return None
    try:
        return WeasyHTML(string=html).write_pdf()
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"weasyprint PDF生成失敗: {exc}")
        return None


def is_pdf_available() -> bool:
    return _WEASYPRINT_AVAILABLE
