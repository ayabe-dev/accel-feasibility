"""LLM による書類解析（マルチプロバイダ対応）.

PDF/画像をアップロード → LLMのマルチモーダルに渡して
「書類種別 + 重要フィールド」を JSON で抽出する。

対応プロバイダ:
  - gemini (Google AI Studio) ★デフォルト・推奨
  - claude (Anthropic)
  - openai (OpenAI ChatGPT)

環境変数:
  LLM_PROVIDER=gemini | claude | openai
  GEMINI_API_KEY=...
  ANTHROPIC_API_KEY=...
  OPENAI_API_KEY=...
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import gemini_models
from .models import DocumentType, ExtractedDocument

logger = logging.getLogger(__name__)

# 必要な時だけインポート（テストやAPIキー無し時に動かせるように）
try:
    import anthropic  # type: ignore

    _ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ANTHROPIC_AVAILABLE = False

try:
    from google import genai as google_genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore

    _GEMINI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _GEMINI_AVAILABLE = False


SYSTEM_PROMPT = """あなたは建築・不動産書類の解析エキスパートです。
ユーザーから渡された PDF / 画像を読み、以下を厳密にJSONで返してください。

【重要】マイソク（不動産販売資料・物件概要書）は情報の宝庫です。所在地・用途地域・防火地域・
建ぺい率・容積率・構造・延床面積・築年月・検査済証・道路幅員などが1枚に整理されているので、
見落とさず可能な限り全項目を埋めてください。

出力スキーマ:
{
  "document_type": "<下記enumのいずれか>",
  "confidence": 0.0-1.0,
  "extracted_fields": {
    // 該当する項目だけ含める。不明はキー自体を省略
    "address": "...",                  // 所在地（都道府県＋市区町村＋番地まで）
    "use_purpose": "...",              // 用途（住宅/共同住宅/旅館/事務所 等）
    "property_type": "...",            // 物件種別（一棟マンション/一戸建/共同住宅 等）
    "structure": "...",                // 構造（木造/RC造/S造/SRC造/鉄筋コンクリート造 等）
    "floors_above": int,               // 地上階数
    "floors_below": int,               // 地下階数
    "total_floor_area_m2": float,      // 延床面積（㎡）
    "building_area_m2": float,         // 建築面積（㎡）
    "site_area_m2": float,             // 敷地面積（㎡）
    "zoning": "...",                   // 用途地域（例：第二種住居地域、商業地域）
    "fire_district": "...",            // 防火地域/準防火地域/指定なし
    "height_district": "...",          // 高度地区（第一種高度地区 等）
    "coverage_ratio_pct": float,       // 建ぺい率（%数字のみ）
    "floor_area_ratio_pct": float,     // 容積率（%数字のみ）
    "city_planning": "...",            // 都市計画（市街化区域/市街化調整区域 等）
    "road_width_m": float,             // 接道幅員（複数あれば狭い方）
    "road_widths": [float],            // 複数道路の幅員リスト
    "road_details": "...",             // 道路詳細（公道/私道 等の説明）
    "confirmation_number": "...",      // 確認番号
    "confirmation_date": "YYYY-MM-DD", // 確認年月日
    "confirmation_obtained": bool,     // 建築確認取得済か
    "inspection_number": "...",        // 検査番号
    "inspection_date": "YYYY-MM-DD",   // 検査年月日
    "inspection_obtained": bool,       // 検査済証 有/無
    "built_year": int,                 // 建築年（西暦4桁）
    "built_month": int,                // 建築月（1-12）
    "total_units": int,                // 総戸数
    "layout_summary": "...",           // 間取りサマリ
    "constructor": "...",              // 施工会社
    "owner": "...",                    // 所有者
    "renovation_history": "...",       // 修繕・改修履歴
    "price_jpy_man": int,              // 価格（万円単位）
    "rent_income_annual_jpy": int,     // 年間想定賃料収入（円）
    "yield_pct": float,                // 想定利回り（%）
    "current_status": "...",           // 現況（入居中/空室 等）
    "parking": "...",                  // 駐車場の有無・台数
    "setback_required": bool           // セットバック有無
  },
  "raw_text_summary": "...",  // 50-200字程度のサマリ
  "warnings": ["..."]         // OCR読取困難な箇所、不整合、欠落等
}

document_type の enum:
  - inspection_certificate     検査済証
  - confirmation_certificate   確認済証
  - building_plan_summary      建築計画概要書
  - important_matters          重要事項説明書
  - land_registry              登記簿（土地）
  - building_registry          登記簿（建物）
  - sales_contract             売買契約書
  - design_drawing             設計図書
  - structural_calc            構造計算書
  - real_estate_flyer          マイソク／物件販売資料／物件概要書
  - other                      上記以外

マイソクの見分け方：
  ・「一棟売」「物件概要」「マイソク」「想定利回り」「年間想定賃料」のような不動産売却資料の文言
  ・物件写真＋間取り図＋【物件概要】テーブル＋不動産会社の連絡先がワンページに集約
  ・「土地」「建物」「制限」「施設」のように区分された表形式の物件情報

【注意】
  ・「鉄筋コンクリート造陸屋根4階建」のような表記から構造="RC造"、floors_above=4 を抽出
  ・「1989年8月」のような表記から built_year=1989, built_month=8 を抽出
  ・「準防火地域」→ "準防火地域" のまま入れる（後処理でコード化）
  ・「検査済証 有」→ inspection_obtained=true, inspection_obtained="無" → false
  ・住所は「東京都目黒区南三丁目12-5」のように都道府県から番地まで完全形で
  ・必ず JSON のみを返してください（コードブロック・前置きは不要）
"""


@dataclass
class UploadedFile:
    """Streamlit の UploadedFile を扱いやすく包んだもの."""

    name: str
    bytes_data: bytes
    mime_type: str


def parse_document(uploaded: UploadedFile) -> ExtractedDocument:
    """1ファイルを LLM に渡して構造化抽出.

    LLM_PROVIDER 環境変数で gemini / claude / openai を切り替え。
    キー未設定なら other 扱いの空抽出を返す（UIで警告表示）.
    """
    provider = os.getenv("LLM_PROVIDER", "gemini").lower().strip()

    if provider == "gemini":
        return _parse_with_gemini(uploaded)
    if provider == "claude":
        return _parse_with_claude(uploaded)
    if provider == "openai":
        return _parse_with_openai(uploaded)

    return ExtractedDocument(
        file_name=uploaded.name,
        document_type=DocumentType.OTHER,
        confidence=0.0,
        warnings=[f"LLM_PROVIDER='{provider}' は未対応です。gemini/claude/openaiから選択。"],
    )


# ---------------------------------------------------------------------------
# 読み取り精度のための前処理・後検証
# ---------------------------------------------------------------------------


def _pdf_to_page_images(pdf_bytes: bytes, dpi: int, max_pages: int):
    """PDFを高解像度のページ画像にする（スキャン書類の読み取り精度対策）.

    PDFをそのままLLMに渡すと、スキャン原稿は内部解像度のまま扱われて文字が潰れる。
    300dpi 程度に焼き直すと、重説・検査済証・マイソクの小さな表組みが読めるようになる。

    pdf2image / poppler が無い環境では None を返し、呼び出し側が生PDFにフォールバックする。
    """
    try:
        from pdf2image import convert_from_bytes  # type: ignore
    except ImportError:
        logger.info("pdf2image が無いため PDF をそのまま渡します（精度は落ちます）")
        return None

    try:
        import io

        pages = convert_from_bytes(pdf_bytes, dpi=dpi, fmt="png")
    except Exception as exc:  # noqa: BLE001 - poppler未導入・破損PDF等
        logger.warning(f"PDFの画像化に失敗（生PDFで続行）: {exc}")
        return None

    out = []
    for page in pages[:max_pages]:
        import io as _io

        buf = _io.BytesIO()
        page.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out or None


# 実務でありえない値を弾くための範囲（誤読の検知が目的。値は書き換えない）
_SANITY_RANGES = {
    "total_floor_area_m2": (5.0, 100000.0, "延床面積"),
    "building_area_m2": (5.0, 100000.0, "建築面積"),
    "site_area_m2": (5.0, 100000.0, "敷地面積"),
    "floors_above": (1, 60, "地上階数"),
    "floors_below": (0, 10, "地下階数"),
    "coverage_ratio_pct": (10, 100, "建ぺい率"),
    "floor_area_ratio_pct": (50, 1500, "容積率"),
    "road_width_m": (0.5, 50.0, "接道幅員"),
    "built_year": (1900, 2100, "建築年"),
    "built_month": (1, 12, "建築月"),
    "total_units": (1, 2000, "総戸数"),
}


def sanity_warnings(fields: dict) -> List[str]:
    """抽出値の明らかな誤読を警告にする（値は自動修正しない）."""
    out: List[str] = []
    for key, (lo, hi, label) in _SANITY_RANGES.items():
        v = fields.get(key)
        if v is None or isinstance(v, bool):
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            out.append(f"⚠️ {label}が数値として読めません（抽出値: {v!r}）。原本を確認してください")
            continue
        if not (lo <= num <= hi):
            out.append(
                f"⚠️ {label} {v} は想定範囲（{lo:g}〜{hi:g}）の外です。"
                "誤読の可能性＝原本を確認してください"
            )
    # 面積の整合（建築面積 > 延床 はありえない）
    try:
        ba = float(fields.get("building_area_m2") or 0)
        fa = float(fields.get("total_floor_area_m2") or 0)
        if ba > 0 and fa > 0 and ba > fa * 1.01:
            out.append(
                f"⚠️ 建築面積 {ba:g}㎡ が延床面積 {fa:g}㎡ を超えています＝どちらかが誤読の可能性"
            )
    except (TypeError, ValueError):
        pass
    return out


def _parse_with_gemini(uploaded: UploadedFile) -> ExtractedDocument:
    """Google Gemini で書類解析（リトライ＋フォールバックモデル付き）."""
    api_key = os.getenv("GEMINI_API_KEY")
    # 読み取りは精度優先。モデル名の解決（廃止への自己修復含む）は core/gemini_models へ委譲する。
    # 明示指定したいときだけ GEMINI_EXTRACT_MODEL / GEMINI_MODEL を設定する。
    model_primary = os.getenv("GEMINI_EXTRACT_MODEL") or os.getenv("GEMINI_MODEL") or None

    if not _GEMINI_AVAILABLE or not api_key:
        return ExtractedDocument(
            file_name=uploaded.name,
            document_type=DocumentType.OTHER,
            confidence=0.0,
            warnings=[
                "GEMINI_API_KEY が未設定 or google-genai 未インストールのため書類解析をスキップしました。"
            ],
        )

    client = google_genai.Client(api_key=api_key)

    mime = uploaded.mime_type.lower()
    if mime == "image/jpg":
        mime = "image/jpeg"

    prep_notes: List[str] = []
    parts = []
    if mime == "application/pdf":
        dpi = int(os.getenv("EXTRACT_PDF_DPI", "300"))
        max_pages = int(os.getenv("EXTRACT_MAX_PAGES", "12"))
        page_images = _pdf_to_page_images(uploaded.bytes_data, dpi=dpi, max_pages=max_pages)
        if page_images:
            for img in page_images:
                parts.append(genai_types.Part.from_bytes(data=img, mime_type="image/png"))
            prep_notes.append(
                f"PDFを{dpi}dpiの画像{len(page_images)}ページに焼き直して読みました"
                + ("（以降のページは未読）" if len(page_images) >= max_pages else "")
            )
        else:
            parts.append(
                genai_types.Part.from_bytes(data=uploaded.bytes_data, mime_type=mime)
            )
            prep_notes.append(
                "PDFをそのまま読みました（pdf2image/poppler が無い環境。"
                "スキャン原稿だと精度が落ちます）"
            )
    else:
        parts.append(genai_types.Part.from_bytes(data=uploaded.bytes_data, mime_type=mime))

    contents = parts + [
        f"ファイル名: {uploaded.name}\n"
        "上記スキーマで抽出してください。**書かれていない項目はキーを省略**し、"
        "推測で埋めないこと。数字は原本の表記をそのまま数値化し、単位換算が必要なときは"
        "warnings に換算内容を書いてください。",
    ]

    config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        max_output_tokens=8192,
        temperature=0.0,  # 抽出は毎回同じ答えになってほしい（既定1.0だと値がブレる）
    )

    def _run(model_name: str):
        """1モデルで実行（503/429 は同一モデル内でリトライ）."""
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                return client.models.generate_content(
                    model=model_name, contents=contents, config=config
                )
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                last = exc
                if "503" in err or "UNAVAILABLE" in err:
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s
                    continue
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    time.sleep(5 * (attempt + 1))  # 5s, 10s, 15s
                    continue
                raise
        raise last if last else RuntimeError("unreachable")

    try:
        response, used_model, tried = gemini_models.call_with_fallback(
            client, _run, purpose="quality", primary=model_primary
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gemini 書類解析が全モデルで失敗")
        hint = ""
        if gemini_models.is_model_not_found(exc):
            hint = (
                "モデル名が廃止されている可能性があります。"
                "`GEMINI_MODEL_CHAIN` に現行モデル名を指定すると即座に追随できます。"
            )
        return ExtractedDocument(
            file_name=uploaded.name,
            document_type=DocumentType.OTHER,
            confidence=0.0,
            warnings=[
                "❌ 書類の読み取りに失敗しました（**フィールドは1件も抽出できていません**）",
                f"最後のエラー: {exc}",
                hint or "対処：数分待ってリトライ、または LLM_PROVIDER=claude に切替",
            ],
        )

    result = _parse_response(uploaded.name, response.text or "")
    result.warnings.extend(sanity_warnings(result.extracted_fields or {}))
    result.warnings.extend(prep_notes)
    result.warnings.append(f"読み取りモデル: {used_model}")
    if len(tried) > 1:
        result.warnings.append(f"（試したモデル: {' → '.join(tried)}）")
    return result


def _parse_with_claude(uploaded: UploadedFile) -> ExtractedDocument:
    """Anthropic Claude で書類解析."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

    if not _ANTHROPIC_AVAILABLE or not api_key:
        return ExtractedDocument(
            file_name=uploaded.name,
            document_type=DocumentType.OTHER,
            confidence=0.0,
            warnings=[
                "ANTHROPIC_API_KEY が未設定のため書類解析をスキップしました。"
            ],
        )

    client = anthropic.Anthropic(api_key=api_key)
    content_block = _build_content_block_claude(uploaded)
    if content_block is None:
        return ExtractedDocument(
            file_name=uploaded.name,
            document_type=DocumentType.OTHER,
            confidence=0.0,
            warnings=[f"サポートされていないファイル形式: {uploaded.mime_type}"],
        )

    try:
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        content_block,
                        {
                            "type": "text",
                            "text": (
                                f"ファイル名: {uploaded.name}\n"
                                "この書類を上記スキーマで抽出してください。"
                            ),
                        },
                    ],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Claude API call failed")
        return ExtractedDocument(
            file_name=uploaded.name,
            document_type=DocumentType.OTHER,
            confidence=0.0,
            warnings=[f"Claude API エラー: {exc}"],
        )

    text = "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )
    return _parse_response(uploaded.name, text)


def _parse_with_openai(uploaded: UploadedFile) -> ExtractedDocument:
    """OpenAI GPT で書類解析（PDF は画像化を想定）."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return ExtractedDocument(
            file_name=uploaded.name,
            document_type=DocumentType.OTHER,
            confidence=0.0,
            warnings=["OPENAI_API_KEY が未設定。 GEMINI に切り替え推奨。"],
        )
    return ExtractedDocument(
        file_name=uploaded.name,
        document_type=DocumentType.OTHER,
        confidence=0.0,
        warnings=["OpenAI 対応は雛形のみ。 GEMINI を使ってください（LLM_PROVIDER=gemini）。"],
    )


def _build_content_block_claude(uploaded: UploadedFile) -> Optional[dict]:
    mime = uploaded.mime_type.lower()
    encoded = base64.standard_b64encode(uploaded.bytes_data).decode("utf-8")

    if mime == "application/pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": encoded,
            },
        }
    if mime in {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}:
        media_type = "image/jpeg" if mime == "image/jpg" else mime
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": encoded,
            },
        }
    return None


def _parse_response(file_name: str, text: str) -> ExtractedDocument:
    """Claude の返答（JSON文字列）を ExtractedDocument に変換."""
    text = text.strip()
    if text.startswith("```"):
        # コードフェンス除去
        lines = [l for l in text.split("\n") if not l.startswith("```")]
        text = "\n".join(lines)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ExtractedDocument(
            file_name=file_name,
            document_type=DocumentType.OTHER,
            confidence=0.0,
            warnings=[f"Claude応答のJSONパース失敗: {text[:200]}"],
        )

    try:
        doc_type = DocumentType(payload.get("document_type", "other"))
    except ValueError:
        doc_type = DocumentType.OTHER

    return ExtractedDocument(
        file_name=file_name,
        document_type=doc_type,
        confidence=float(payload.get("confidence", 0.5)),
        extracted_fields=payload.get("extracted_fields", {}),
        raw_text_summary=payload.get("raw_text_summary"),
        warnings=payload.get("warnings", []),
    )


# ---------------------------------------------------------------------------
# Streamlit ヘルパー
# ---------------------------------------------------------------------------


def from_streamlit_uploaded_file(uploaded_file) -> UploadedFile:
    """Streamlit の UploadedFile を UploadedFile dataclass に変換."""
    return UploadedFile(
        name=uploaded_file.name,
        bytes_data=uploaded_file.read(),
        mime_type=uploaded_file.type or _guess_mime(uploaded_file.name),
    )


def _guess_mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
