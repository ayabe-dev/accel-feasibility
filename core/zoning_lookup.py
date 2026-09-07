"""用途地域を Gemini（Google検索グラウンディング）で調べる補助.

## ⚠️ これは「候補の提示」であって自動確定ではない

用途地域は A1 ゲート＝許可可否の根幹で、`商業地域` と `第一種低層住居専用地域` を
間違えると判定が丸ごと反転する。LLMの検索結果をそのまま判定に流すことはしない。

  - 一次情報が取れる経路（優先）… 不動産情報ライブラリAPI（`REINFOLIB_API_KEY`）
  - 書面がある経路（確実）      … 重要事項説明書・レインズの記載を人が入力
  - **この経路（補助）**        … Gemini が調べた候補を出典つきで出し、**人が確認して選ぶ**

呼び出し側は必ず「候補」として扱い、判定に使う前に人の確認を挟むこと。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from google import genai as google_genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore

    _GEMINI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _GEMINI_AVAILABLE = False


# app.py / brief_cli.py と同じ13区分＋指定なし
ZONING_NAMES: Dict[str, str] = {
    "第一種低層住居専用地域": "first_low_residential",
    "第二種低層住居専用地域": "second_low_residential",
    "田園住居地域": "rural_residential",
    "第一種中高層住居専用地域": "first_mid_residential",
    "第二種中高層住居専用地域": "second_mid_residential",
    "第一種住居地域": "first_residential",
    "第二種住居地域": "second_residential",
    "準住居地域": "quasi_residential",
    "近隣商業地域": "neighborhood_commercial",
    "商業地域": "commercial",
    "準工業地域": "quasi_industrial",
    "工業地域": "industrial",
    "工業専用地域": "exclusive_industrial",
    "用途地域指定なし": "non_zoned",
}
FIRE_NAMES: Dict[str, str] = {
    "防火地域": "fire_district",
    "準防火地域": "quasi_fire_district",
    "指定なし": "no_district",
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "zoning_name": {"type": "string", "description": "用途地域名。13区分の正式名称か『用途地域指定なし』"},
        "fire_district_name": {"type": "string", "description": "防火地域 / 準防火地域 / 指定なし / 不明"},
        "floor_area_ratio_pct": {"type": "number", "description": "容積率(%)。分からなければ0"},
        "coverage_ratio_pct": {"type": "number", "description": "建ぺい率(%)。分からなければ0"},
        "source_url": {"type": "string", "description": "実際に開いた一次情報のURL（自治体の都市計画情報等）"},
        "quote": {"type": "string", "description": "根拠となる記載の原文引用"},
        "confidence": {"type": "string", "description": "高 / 中 / 低"},
        "note": {"type": "string", "description": "地番の特定可否・複数地域にまたがる可能性など"},
    },
    "required": ["zoning_name", "confidence"],
}

_SYSTEM = (
    "あなたは日本の都市計画情報を一次情報から確認する調査係です。"
    "自治体の都市計画情報（GIS・例規集・用途地域図）を検索して開き、"
    "指定された住所の用途地域を確認します。"
    "**推測で断定しないこと。**丁目までしか分からない場合や、"
    "地番によって用途地域が分かれる場合は confidence を『低』にし、note にそう書きます。"
    "URLは検索エンジンの中継URLではなく、実際に開いた自治体・省庁のURLを書きます。"
)


def is_available() -> bool:
    return _GEMINI_AVAILABLE and bool(os.getenv("GEMINI_API_KEY"))


def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """LLMの生出力を、呼び出し側が使える形に正規化する（ネットワーク不要・テスト対象）.

    - 用途地域名が13区分に一致しなければ `zoning_code=None`（＝採用させない）
    - 出典URLが公的ドメインでなければ `verified=False`
    """
    from .legal_research import _is_trusted_url

    name = str(raw.get("zoning_name") or "").strip()
    # 「東京都新宿区：商業地域」のような装飾を落とす
    for known in ZONING_NAMES:
        if known in name:
            name = known
            break
    code = ZONING_NAMES.get(name)

    fire_name = str(raw.get("fire_district_name") or "").strip()
    fire_code = FIRE_NAMES.get(fire_name)

    url = str(raw.get("source_url") or "").strip()
    quote = str(raw.get("quote") or "").strip()
    confidence = str(raw.get("confidence") or "").strip() or "低"

    def _num(key: str) -> Optional[float]:
        try:
            v = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    return {
        "zoning_name": name or None,
        "zoning_code": code,
        "fire_district_name": fire_name or None,
        "fire_district_code": fire_code,
        "floor_area_ratio_pct": _num("floor_area_ratio_pct"),
        "coverage_ratio_pct": _num("coverage_ratio_pct"),
        "source_url": url or None,
        "quote": quote or None,
        "confidence": confidence,
        "note": str(raw.get("note") or "").strip() or None,
        # 公的ドメイン＋原文引用が揃っているか（揃っていても人の確認は必要）
        "verified": bool(url and quote and _is_trusted_url(url)),
        "is_suggestion": True,
    }


def lookup(address: str, model: Optional[str] = None) -> Dict[str, Any]:
    """住所から用途地域の**候補**を返す.

    戻り値は normalize() の形。`error` が入っていたら失敗。
    **判定に使う前に必ず人が確認する。**
    """
    if not is_available():
        return {
            "error": (
                "GEMINI_API_KEY が未設定（または google-genai 未インストール）のため"
                "用途地域の調査をスキップしました。重要事項説明書・レインズの記載を"
                "手で入力してください。"
            ),
            "is_suggestion": True,
        }

    from . import gemini_models

    primary = model or os.getenv("ZONING_LOOKUP_MODEL") or None
    models: List[str] = gemini_models.candidate_chain(purpose="quality", primary=primary)
    client = google_genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    ask = (
        f"次の住所の**用途地域**を、自治体の都市計画情報から確認してください。\n\n"
        f"- 住所：{address}\n\n"
        "あわせて分かれば防火地域・容積率・建ぺい率も。"
        "実際に開いたURLと、根拠になる記載の原文引用を添えてください。"
    )

    last_error: Optional[Exception] = None
    for name in models:
        try:
            searched = client.models.generate_content(
                model=name,
                contents=ask,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_SYSTEM,
                    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                ),
            )
            notes = (getattr(searched, "text", "") or "").strip()
            if not notes:
                last_error = RuntimeError("調査応答が空でした")
                continue

            structured = client.models.generate_content(
                model=name,
                contents=(
                    "次の調査メモをJSONへ整形してください。"
                    "**メモに無い事実を足さないでください。**\n\n"
                    "=== 調査メモ ===\n" + notes
                ),
                config=genai_types.GenerateContentConfig(
                    system_instruction=(
                        _SYSTEM
                        + "\n\n出力は次のJSONスキーマに厳密に従うJSONのみ:\n"
                        + json.dumps(_SCHEMA, ensure_ascii=False)
                    ),
                    response_mime_type="application/json",
                ),
            )
            text = (getattr(structured, "text", "") or "").strip()
            raw = json.loads(text) if text.startswith("{") else None
            if not isinstance(raw, dict):
                last_error = RuntimeError("応答をJSONとして解釈できませんでした")
                continue
            out = normalize(raw)
            out["model"] = name
            return out
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "404" in msg or "NOT_FOUND" in msg or "not found" in msg.lower():
                last_error = e
                continue
            logger.warning("用途地域の調査に失敗: %s", e)
            return {"error": f"用途地域の調査でエラー：{e}", "is_suggestion": True}

    return {
        "error": f"用途地域の調査に失敗しました：{last_error}",
        "is_suggestion": True,
    }
