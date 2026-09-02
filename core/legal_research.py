"""自治体条例・手引きを Claude（web検索つき）で一次情報から調べる層.

## 役割分担

  判定そのものはやらない。判定は core/license_gate.py がルールで行う。
  ここは「その自治体で何が上乗せされているか」を一次情報から集めて
  Evidence に変換するだけの調査係。

## なぜこの設計か

  全国共通の法令（旅館業法・施行令・建基法）は core/evidence.py に人手で
  正本を持てるが、許可の可否を実際に左右するのは自治体の条例・審査基準・
  運用であり、これは数が多く改正も頻繁で、事前に全部YAML化するのは非現実的。
  一方で LLM に丸ごと判定させると数値基準を誤る。よって：

      数値基準・法令判定 → コード（決定的・再現可能）
      条例の探索と引用   → Claude + web検索（カバレッジ）
      検証              → 出典URL + 原文引用が揃ったものだけ採用

## キャッシュ

  調べた結果は config/municipality_research_cache.yaml に書き戻す。
  次回以降は即座に返る。条例改正に追随したいときは refresh=True で再取得。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from .evidence import Evidence, SourceTier
from .models import BusinessType

logger = logging.getLogger(__name__)

try:
    import anthropic  # type: ignore

    _ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ANTHROPIC_AVAILABLE = False


_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "municipality_research_cache.yaml"
)

# キャッシュの互換性キー。スキーマやプロンプトを変えたら上げる（古い結果を無効化）
SCHEMA_VERSION = "2026.08.31.1"

# 一次情報として信頼する（＝検証済み扱いにする）ドメイン。
# ここに載らないドメインの findings は表示はするが判定には使わない。
TRUSTED_DOMAIN_SUFFIXES = (
    ".go.jp",  # 国の機関
    ".lg.jp",  # 地方公共団体
    "e-gov.go.jp",
    "elaws.e-gov.go.jp",
    "g-reiki.net",  # 例規集ホスティング（自治体公式の例規データベース）
    "reiki.jp",
    "d1-law.com",  # 同上
)

DEFAULT_MODEL = "claude-opus-5"


# ---------------------------------------------------------------------------
# 出力スキーマ
# ---------------------------------------------------------------------------

TOPIC_LABELS: Dict[str, str] = {
    "distance_regulation": "学校等からの距離規制",
    "room_area": "客室面積・定員の上乗せ",
    "front_desk": "玄関帳場・無人運営の可否",
    "facility": "設備基準の上乗せ（便所・洗面・入浴等）",
    "procedure": "手続き（事前相談・近隣説明・標準処理期間・手数料）",
    "zoning_local": "自治体固有の立地制限（特別用途地区・地区計画等）",
    "minpaku_ordinance": "住宅宿泊事業の条例制限（区域・期間）",
    "other": "その他",
}

IMPACT_LABELS: Dict[str, str] = {
    "blocking": "⛔ 該当すると許可が下りない",
    "conditional": "⚠️ 対応すればクリアできる",
    "info": "ℹ️ 参考情報",
}

_RESEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "municipality_name": {"type": "string"},
        "permit_authority": {
            "type": "string",
            "description": "許可権者と申請窓口（例：目黒区長／目黒区保健所生活衛生課）",
        },
        "summary": {
            "type": "string",
            "description": "この自治体で旅館業許可を取るうえでの要点を300字以内で",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": list(TOPIC_LABELS.keys()),
                    },
                    "title": {"type": "string"},
                    "requirement": {
                        "type": "string",
                        "description": "何が求められるかを具体的に。数値があれば数値を含める",
                    },
                    "impact": {
                        "type": "string",
                        "enum": ["blocking", "conditional", "info"],
                    },
                    "law_name": {
                        "type": "string",
                        "description": "条例・要綱・手引きの正式名称",
                    },
                    "article": {"type": "string", "description": "条項番号。無ければ空文字"},
                    "quote": {
                        "type": "string",
                        "description": (
                            "出典からの原文引用。要約を入れてはならない。"
                            "原文を確認できなかった場合は空文字にすること"
                        ),
                    },
                    "url": {
                        "type": "string",
                        "description": (
                            "その記述が実際に読めるURL。推測したURLを書いてはならない。"
                            "検索で実際に開いたページのURLのみ"
                        ),
                    },
                    "publisher": {"type": "string"},
                },
                "required": [
                    "topic",
                    "title",
                    "requirement",
                    "impact",
                    "law_name",
                    "article",
                    "quote",
                    "url",
                    "publisher",
                ],
                "additionalProperties": False,
            },
        },
        "minpaku": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["allowed", "restricted", "prohibited", "unknown"],
                },
                "restriction_summary": {"type": "string"},
                "max_days_per_year": {
                    "type": "integer",
                    "description": "条例で短縮された年間営業日数上限。制限なしなら180",
                },
                "url": {"type": "string"},
            },
            "required": ["status", "restriction_summary", "max_days_per_year", "url"],
            "additionalProperties": False,
        },
        "unresolved": {
            "type": "array",
            "items": {"type": "string"},
            "description": "調べきれなかった論点。保健所へ直接確認すべき事項",
        },
    },
    "required": [
        "municipality_name",
        "permit_authority",
        "summary",
        "findings",
        "minpaku",
        "unresolved",
    ],
    "additionalProperties": False,
}


_SYSTEM_PROMPT = """あなたは日本の旅館業許可（旅館業法3条）と住宅宿泊事業（民泊）の\
実務に詳しい調査担当者です。指定された自治体について、許可の可否と条件を左右する\
「自治体固有のルール」を一次情報から調べ、構造化して返してください。

## 絶対に守ること

1. **必ず web 検索を使って実際のページを開いて確認する。** 記憶で答えてはいけません。
   自治体の条例・審査基準・手引きは頻繁に改正されており、記憶は古い可能性が高いためです。

2. **quote には必ず原文をそのまま入れる。** 要約・言い換えを quote に入れてはいけません。
   原文を確認できなかった項目は quote を空文字にしてください（項目自体は残してよい）。

3. **url は実際に開いたページのURLだけ。** 「たぶんこのURL」という推測を書いてはいけません。
   URLを確認できなかった項目は url を空文字にしてください。

4. **調べた結果「見つからなかった」ことは、findings に載せず unresolved に書く。**
   存在しない条例をでっち上げるより、「確認できなかった」と正直に返すほうが価値があります。

5. 優先して探す一次情報の順序：
   ① 自治体の例規集にある条例・規則の本文（○○市旅館業法施行条例 等）
   ② 自治体が公開している審査基準・事務取扱要綱・手引き（PDF可）
   ③ 自治体サイトの旅館業・民泊の案内ページ
   一般のブログ・行政書士事務所の解説記事は出典にしないでください。

## 調べる論点

- 旅館業法施行条例による構造設備の上乗せ（客室面積、便所・洗面の数、定員の算定）
- 玄関帳場（フロント）の要否と、ICT代替・無人運営の可否／条件
- 学校等からの距離規制（対象施設の範囲・距離・手続き）
- 事前相談・近隣住民への説明・標識設置などの手続き要件、標準処理期間、手数料
- 特別用途地区・地区計画など、その自治体固有の立地制限
- 住宅宿泊事業（民泊）の条例による区域・期間の制限（重要：2026年7月の国の技術的助言により、
  条例で営業日数を実質ゼロにする「ゼロ日規制」が容認されています。最新の条例を必ず確認）

impact の使い分け：
- blocking … 該当すると許可が下りない（例：文教地区でホテル建築不可）
- conditional … 対応・改修・協議でクリアできる（例：客室面積の上乗せ）
- info … 参考情報（例：手数料の額）
"""


class ResearchResult(BaseModel):
    """自治体調査の結果."""

    municipality_key: str = ""
    municipality_name: str = ""
    permit_authority: str = ""
    summary: str = ""
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    minpaku: Dict[str, Any] = Field(default_factory=dict)
    unresolved: List[str] = Field(default_factory=list)
    researched_on: str = ""
    model: str = ""
    schema_version: str = SCHEMA_VERSION
    from_cache: bool = False
    error: str = ""

    @property
    def available(self) -> bool:
        return not self.error and bool(self.findings or self.summary)

    def evidences(self, topics: Optional[List[str]] = None) -> List[Evidence]:
        """findings を Evidence に変換。topics 指定でその論点だけ."""
        out: List[Evidence] = []
        for f in self.findings:
            if topics and f.get("topic") not in topics:
                continue
            out.append(_finding_to_evidence(f, self.researched_on))
        return out

    def blocking_findings(self, topics: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """判定に使える（＝検証済みの） blocking 所見だけ."""
        out = []
        for f in self.findings:
            if topics and f.get("topic") not in topics:
                continue
            if f.get("impact") != "blocking":
                continue
            if not _is_verified_finding(f):
                continue
            out.append(f)
        return out

    def conditional_findings(self, topics: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        out = []
        for f in self.findings:
            if topics and f.get("topic") not in topics:
                continue
            if f.get("impact") != "conditional":
                continue
            if not _is_verified_finding(f):
                continue
            out.append(f)
        return out


def _is_trusted_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    if not u.startswith("http"):
        return False
    host = u.split("//", 1)[-1].split("/", 1)[0]
    return any(host.endswith(s) or s in host for s in TRUSTED_DOMAIN_SUFFIXES)


def _is_verified_finding(f: Dict[str, Any]) -> bool:
    """判定に使ってよい所見か。原文引用＋信頼できるドメインのURLが揃っていること."""
    return bool(f.get("quote")) and _is_trusted_url(f.get("url", ""))


def _finding_to_evidence(f: Dict[str, Any], checked_on: str) -> Evidence:
    verified = _is_verified_finding(f)
    name = str(f.get("law_name") or f.get("title") or "")
    if verified:
        # 条例本文か、自治体の手引き類かで階層を分ける
        is_ordinance = any(k in name for k in ("条例", "規則", "施行細則"))
        tier = SourceTier.ORDINANCE if is_ordinance else SourceTier.GUIDE
    else:
        tier = SourceTier.UNVERIFIED
    note = str(f.get("requirement") or "")
    if not verified:
        reason = []
        if not f.get("quote"):
            reason.append("原文引用なし")
        if not _is_trusted_url(f.get("url", "")):
            reason.append("公的ドメインの出典URLなし")
        note = (
            f"{note}\n\n⚠️ {'・'.join(reason)}のため未検証。判定には使用していません。"
            "保健所への直接確認が必要です。"
        )
    return Evidence(
        source_tier=tier,
        law_name=name,
        article=str(f.get("article") or ""),
        quote=str(f.get("quote") or ""),
        url=str(f.get("url") or ""),
        publisher=str(f.get("publisher") or ""),
        checked_on=checked_on,
        note=note,
    )


# ---------------------------------------------------------------------------
# キャッシュ
# ---------------------------------------------------------------------------


def _load_cache() -> Dict[str, Any]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        with _CACHE_PATH.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # pragma: no cover
        logger.warning("調査キャッシュの読み込みに失敗: %s", e)
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE_PATH.open("w", encoding="utf-8") as f:
            f.write(
                "# 自治体調査キャッシュ（core/legal_research.py が自動生成・追記）\n"
                "# 手で編集してもよい。編集した内容は次回以降そのまま使われる。\n"
                "# 条例改正に追随したいときは該当キーを削除するか、UIから再調査する。\n"
            )
            yaml.safe_dump(cache, f, allow_unicode=True, sort_keys=True, width=100)
    except Exception as e:  # pragma: no cover
        logger.warning("調査キャッシュの保存に失敗: %s", e)


def _cache_key(municipality_key: str, business_type: BusinessType) -> str:
    # 旅館業の2業態は同じ条例を見るので調査結果を共用する
    scope = "minpaku" if business_type == BusinessType.MINPAKU else "ryokan"
    return f"{municipality_key}__{scope}"


def cached_result(
    municipality_key: str, business_type: BusinessType
) -> Optional[ResearchResult]:
    """キャッシュにあれば返す（API呼び出しなし）."""
    cache = _load_cache()
    entry = cache.get(_cache_key(municipality_key, business_type))
    if not entry:
        return None
    if entry.get("schema_version") != SCHEMA_VERSION:
        return None
    return ResearchResult(**{**entry, "from_cache": True})


def _build_client() -> Any:
    """Anthropic クライアントを作る.

    ANTHROPIC_API_KEY が無いことは「資格情報が無い」ことを意味しない。
    SDK は ANTHROPIC_API_KEY → ANTHROPIC_AUTH_TOKEN → `ant auth login` の
    プロファイル → Workload Identity Federation の順に解決するため、
    引数なしで構築して SDK に任せる。解決できないときだけ例外が飛ぶ。
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    return anthropic.Anthropic()


# 資格情報を解決できなかったときの、人が読める説明
_NO_CREDENTIAL_MESSAGE = (
    "Anthropic API の資格情報が見つからないため自治体調査をスキップしました。"
    "ANTHROPIC_API_KEY（または ANTHROPIC_AUTH_TOKEN）を設定してください。"
    "Streamlit Cloud では Secrets に登録します。"
    "全国共通の法令（旅館業法・同施行令・建築基準法）に基づく判定のみ表示しています。"
)


def _has_credentials() -> bool:
    """資格情報が解決できるか.

    SDK は認証の解決を「送信時」まで遅延するため、クライアントを構築できた
    ことは資格情報がある証拠にならない。構築後のクライアントが実際に
    api_key / auth_token を保持しているかまで見る。
    """
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    try:
        client = _build_client()
    except Exception:
        return False
    return bool(
        getattr(client, "api_key", None) or getattr(client, "auth_token", None)
    )


def is_available() -> bool:
    """調査層が使える状態か（SDK＋資格情報）."""
    return _ANTHROPIC_AVAILABLE and _has_credentials()


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def research_municipality(
    municipality_key: str,
    municipality_name: str,
    address: str,
    business_type: BusinessType = BusinessType.HOTEL_RYOKAN,
    refresh: bool = False,
    max_searches: int = 12,
) -> ResearchResult:
    """自治体の条例・手引きを Claude（web検索つき）で調べる.

    Args:
        refresh: True ならキャッシュを無視して再取得
    """
    if not refresh:
        hit = cached_result(municipality_key, business_type)
        if hit is not None:
            return hit

    if not _ANTHROPIC_AVAILABLE:
        return ResearchResult(
            municipality_key=municipality_key,
            municipality_name=municipality_name,
            error="anthropic パッケージが未インストールのため自治体調査をスキップしました。",
        )
    if not _has_credentials():
        return ResearchResult(
            municipality_key=municipality_key,
            municipality_name=municipality_name,
            error=_NO_CREDENTIAL_MESSAGE,
        )

    model = os.getenv("LEGAL_RESEARCH_MODEL", DEFAULT_MODEL)
    try:
        client = _build_client()
    except Exception as e:
        return ResearchResult(
            municipality_key=municipality_key,
            municipality_name=municipality_name,
            error=f"{_NO_CREDENTIAL_MESSAGE}（詳細：{e}）",
        )

    biz_label = {
        BusinessType.HOTEL_RYOKAN: "旅館・ホテル営業",
        BusinessType.SIMPLE_LODGING: "簡易宿所営業",
        BusinessType.MINPAKU: "住宅宿泊事業（民泊）",
    }.get(business_type, "旅館・ホテル営業")

    user_prompt = f"""次の物件について、旅館業許可（および住宅宿泊事業）に関する\
自治体固有のルールを一次情報から調べてください。

- 物件所在地：{address}
- 自治体：{municipality_name}
- 検討している業態：{biz_label}

web検索で自治体の例規集・審査基準・手引きを実際に開いて確認し、\
指定のJSONスキーマで返してください。原文引用と実際に開いたURLを必ず添えてください。\
確認できなかったことは unresolved に入れてください。"""

    tools = [
        {
            "type": _web_search_type(model),
            "name": "web_search",
            "max_uses": max_searches,
        }
    ]

    try:
        raw = _run_research(client, model, tools, user_prompt)
    except Exception as e:  # pragma: no cover - API 依存
        logger.warning("自治体調査に失敗: %s", e)
        msg = str(e)
        if "authentication" in msg.lower() or "api_key" in msg.lower():
            err = _NO_CREDENTIAL_MESSAGE
        else:
            err = f"自治体調査でエラーが発生しました：{e}"
        return ResearchResult(
            municipality_key=municipality_key,
            municipality_name=municipality_name,
            error=err,
        )

    if raw is None:
        return ResearchResult(
            municipality_key=municipality_key,
            municipality_name=municipality_name,
            error="自治体調査の応答をJSONとして解釈できませんでした。",
        )

    result = ResearchResult(
        municipality_key=municipality_key,
        municipality_name=str(raw.get("municipality_name") or municipality_name),
        permit_authority=str(raw.get("permit_authority") or ""),
        summary=str(raw.get("summary") or ""),
        findings=list(raw.get("findings") or []),
        minpaku=dict(raw.get("minpaku") or {}),
        unresolved=list(raw.get("unresolved") or []),
        researched_on=date.today().isoformat(),
        model=model,
        schema_version=SCHEMA_VERSION,
    )

    cache = _load_cache()
    cache[_cache_key(municipality_key, business_type)] = result.model_dump(
        exclude={"from_cache"}
    )
    _save_cache(cache)
    return result


def _web_search_type(model: str) -> str:
    """モデルに応じた web 検索ツールの type を返す.

    動的フィルタリング付きの新しい variant は Opus 4.6 / Sonnet 4.6 以降でのみ使える。
    それ以前のモデルを環境変数で指定された場合は基本 variant にフォールバックする。
    """
    m = model.lower()
    modern = ("opus-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-5", "sonnet-4-6", "fable-5")
    if any(k in m for k in modern):
        return "web_search_20260209"
    return "web_search_20250305"


def _run_research(
    client: Any,
    model: str,
    tools: List[Dict[str, Any]],
    user_prompt: str,
) -> Optional[Dict[str, Any]]:
    """web検索ループを回してJSONを得る.

    server-side tool は Anthropic 側で実行されるので、こちらは pause_turn の
    再開だけ面倒を見ればよい（tool_result を返す必要はない）。
    """
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": 16000,
        "system": _SYSTEM_PROMPT,
        "tools": tools,
        "thinking": {"type": "adaptive"},
    }

    use_output_config = True
    response = None
    for _ in range(8):  # pause_turn の再開上限
        call_kwargs = dict(kwargs)
        call_kwargs["messages"] = messages
        if use_output_config:
            call_kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": _RESEARCH_SCHEMA}
            }
        try:
            response = client.messages.create(**call_kwargs)
        except TypeError as e:
            # 古い SDK は output_config / thinking を知らない
            if use_output_config and "output_config" in str(e):
                use_output_config = False
                kwargs["system"] = _SYSTEM_PROMPT + _json_fallback_instruction()
                continue
            if "thinking" in str(e):
                kwargs.pop("thinking", None)
                continue
            raise
        except Exception as e:
            msg = str(e)
            if use_output_config and ("output_config" in msg or "json_schema" in msg):
                use_output_config = False
                kwargs["system"] = _SYSTEM_PROMPT + _json_fallback_instruction()
                continue
            if "thinking" in msg and "thinking" in kwargs:
                kwargs.pop("thinking", None)
                continue
            raise

        if response.stop_reason == "pause_turn":
            # server-side tool が上限に達した。応答をそのまま積んで継続する
            messages = messages[:1] + [
                {"role": "assistant", "content": response.content}
            ]
            continue
        break

    if response is None:
        return None
    return _extract_json(response)


def _json_fallback_instruction() -> str:
    return (
        "\n\n## 出力形式\n"
        "調査が終わったら、最後に次のJSONスキーマに厳密に従うJSONだけを、"
        "```json コードブロックに入れて出力してください。前後に説明文を書かないでください。\n"
        + json.dumps(_RESEARCH_SCHEMA, ensure_ascii=False)
    )


def _extract_json(response: Any) -> Optional[Dict[str, Any]]:
    """応答からJSONを取り出す（output_config 経路・フォールバック経路の両対応）."""
    texts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
    for text in reversed(texts):  # 最後の text ブロックが本命
        parsed = _loads_lenient(text)
        if parsed is not None:
            return parsed
    return None


def _loads_lenient(text: str) -> Optional[Dict[str, Any]]:
    s = (text or "").strip()
    if not s:
        return None
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    # ```json ... ``` を剥がす
    if "```" in s:
        for chunk in s.split("```"):
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                try:
                    v = json.loads(c)
                    if isinstance(v, dict):
                        return v
                except json.JSONDecodeError:
                    continue
    # 最初の { から最後の } まで
    if "{" in s and "}" in s:
        cand = s[s.index("{") : s.rindex("}") + 1]
        try:
            v = json.loads(cand)
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None
