"""追加情報を LLM で解釈し、判定結果に影響を反映するモジュール.

ユーザー記述の追加情報（例：「元クリニック」「上階に住民」「インバウンド需要強い」等）を
Gemini に渡して、各観点（建基法・消防・コスト・事業性）への影響を構造化抽出する。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .models import ContextImpact, ContextImpactItem, FeasibilityReport, ProjectInput

logger = logging.getLogger(__name__)

try:
    from google import genai as google_genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore

    _GEMINI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _GEMINI_AVAILABLE = False


# ----------------------------------------------------------------------
# サジェスト（追加情報を引き出すためのプロンプト）
# ----------------------------------------------------------------------

CONTEXT_SUGGESTIONS = [
    {
        "title": "元の用途",
        "examples": [
            "元クリニック（避難経路・電気容量・水回り設備が充実）",
            "元事務所（防火区画は別途設置必要）",
            "元学習塾／学校（耐火建築物の可能性高い）",
            "元住宅（用途変更の重さが標準的）",
            "元飲食店（給排水・排煙設備充実、構造変更要）",
        ],
    },
    {
        "title": "既存設備",
        "examples": [
            "自動火災報知設備（自火報）すでに設置済み",
            "スプリンクラー設備設置済み",
            "屋内消火栓あり",
            "二方向避難（屋外階段あり／非常階段あり）",
            "EV（エレベーター）あり",
            "受水槽・高架水槽あり",
        ],
    },
    {
        "title": "建物の状態",
        "examples": [
            "上階に住民が住んでいる（複合用途・異種用途区画要）",
            "1階が店舗（テナント）",
            "全フロア空室",
            "オーナーが現在も使用中",
            "雨漏り・耐震に懸念あり",
        ],
    },
    {
        "title": "周辺状況・事業性",
        "examples": [
            "最寄駅徒歩5分以内（インバウンド需要強い）",
            "観光地に近い（銀座／浅草／京都市内等）",
            "大手チェーンの近接ホテルあり（競合厳しい）",
            "夜間住民とのトラブル可能性（住宅街）",
            "管理組合の同意が必要（マンション）",
        ],
    },
    {
        "title": "コスト・資金面",
        "examples": [
            "リフォーム済（直近5年以内に大規模改修済）",
            "築古で配管・電気設備の更新必要",
            "解体予定の上階あり",
            "予算上限あり（投資額XX万円まで）",
        ],
    },
    {
        "title": "規制・法的リスク",
        "examples": [
            "近隣に小学校あり（距離規制要協議）",
            "地区計画あり（自治体に照会要）",
            "既存不適格（容積率・建ぺい率超過）",
            "私道・接道難ありの可能性",
            "市街化調整区域内",
        ],
    },
]


# ----------------------------------------------------------------------
# システムプロンプト
# ----------------------------------------------------------------------

SYSTEM_PROMPT = """あなたは建築・不動産の用途変更コンサルタントです。
ユーザーが入力した物件の特記事項を読み、住宅・マンションから旅館・ホテル営業への
用途変更フィジビリティ判定にどう影響するかを構造化してJSON形式で返してください。

入力情報:
- 物件の基本情報（住所、用途地域、構造、延床、築年等）
- 評価結果のサマリ（総合判定、調査パターン、コスト目安、スコア）
- ユーザーが記入した追加情報（自由記述）

出力スキーマ（必ずこの形式で）:
{
  "overall_summary": "...",
  "impacts": [
    {
      "aspect": "建基法 / 消防 / コスト / 事業性 / 立地・規制 / 既存建物 / 旅館業法 のいずれか",
      "direction": "プラス / マイナス / 中立 のいずれか",
      "summary": "150字以内で簡潔に説明",
      "affected_rules": ["影響を受ける規定や項目（複数可）"],
      "score_adjustment_hint": -20 〜 +20 の整数（評価への目安）
    }
  ],
  "suggested_additional_questions": [
    "更に評価を精緻化するためにユーザーに聞いたほうがいい質問（3〜5個）"
  ]
}

判定の指針:

【元用途のメリット】
- 元クリニック・元学習塾・元事務所など特殊建築物だった建物は、避難経路・防火区画・
  耐火構造などが既に整っている可能性が高く、Phase 4建基法・Phase 5消防の負担が
  大幅に減るため「プラス」評価。
- ただし「避難・防火系のメリット」と「構造系のメリット」は別物。下記参照。

【⚠️ 旧耐震（1981年6月以前）の重要ルール】
- 旧耐震建物は、元クリニック等の特殊建築物だったとしても、用途変更時に
  **構造耐力の再評価（法20条遡及）が高確率で発生** する。理由：
  ・クリニックは別表第一(6)項、旅館は(2)項 → 法令カテゴリが変わる
  ・大規模修繕を伴うことが多く、法86条の7の緩和を超えて遡及適用される
- そのため、元クリニックの「避難・防火メリット」は活きるが、
  **「構造メリット」は得られない**。コストは下記のように跳ね上がる。
  ・耐震診断：RC造 2,000〜3,500円/㎡（小規模で数十万円、大規模で300〜400万円）
  ・耐震補強工事：10〜30万円/㎡（壁増設等、規模による）
  ・構造設計再計算：50〜300万円
  ・既存図書なしのガイドライン調査：200〜800万円
- 旧耐震フラグが立っていたら、必ず「マイナス」のimpactを1つ以上含め、
  affected_rulesに「法20条」「法86条の7」「耐震診断」を含めること。
  score_adjustment_hint は -15〜-25 が目安。

【新耐震（1981年6月以降）の場合】
- 元クリニック等の特殊用途経歴は、避難・防火・構造すべてで大幅プラス。
- 2007年構造計算改正をまたぐ場合（1981〜2007年築）は要注意：構造計算書の
  再評価が望ましいが、補強工事までは不要なことが多い。

【複合用途・上階住居】
- 複合用途（上階に住居・店舗）は異種用途区画（令112条）が必要で、改修コスト上昇要因。
- 動線分離・管理組合同意・近隣住民との摩擦リスクも考慮する。

【立地・事業性】
- 高地価エリア（銀座・日本橋・新宿等）はADR（客室単価）が上げやすく、事業性プラス。
  ただし投資額も大きい。
- インバウンド需要が強い地区は事業性プラス、近隣住民との摩擦リスクは事業性マイナス。

【既存設備】
- 既存設備（自火報・SP・屋内消火栓・直通階段2以上）の有無は Phase 5 消防、
  Phase 4 建基法のスコアに直接影響。
- 建物の劣化・老朽化は調査パターンを重く（C/D寄り）し、改修費を押し上げる。

【⚠️ 旅館業条例による営業者常駐義務（区ごとに大きく異なる）】
東京23区を中心に、近年「営業従事者の常駐義務」の強化が進行中。事業性スコアに直接効く重要因子。

▼ 中央区（最も厳しいクラス）
  - 旅館業法施行条例で **営業施設内** に従業者を常駐させる義務（既存・新規共通）
  - 「同一敷地」「隣接敷地」を明示する規定がなく、施設内常駐が原則
  - 帳場（フロント）を 1階エントランス付近 に設けることが運用上必須
  - 影響：人件費 24h×3〜4人体制 → 年間 1,800〜2,500万円のランニング上乗せ

▼ 渋谷区（令和8年7月1日以降の新規申請から強化）
  - **改正前（〜2026年6月30日申請まで）**：10分駆けつけ可・ICT代替（タブレット応対等）可で実質無人運営可
  - **改正後（2026年7月1日〜の新規申請）**：営業従事者の常駐義務化
    ・常駐場所：施設内 / 同一敷地内 / **隣接敷地内** のいずれか（中央区よりわずかに柔軟）
    ・常駐場所要件：客室を通らず出入りできる部屋＋客室外便所
    ・常駐時間：原則営業時間中
  - その他改正：標識60日前設置（縦1.2m×横0.9m）、近隣説明会、緊急連絡先表示（既存施設も対象）
  - 既存施設は経過措置で旧基準維持可

▼ 新宿区・港区・台東区
  - 「速やかに駆けつけ可能（10分以内目安）」が一般的。常駐は明示義務ではない
  - 帳場設置義務はあるが、ICT 代替（旅館業法改正で 2018年〜許容）の運用が広い
  - 影響：人件費は 1名 + ICT で年間 400〜700万円

▼ 世田谷区（運営面は柔軟・土地確保が難点）
  - 旅館業法施行条例 平成24年条例20号（令和5年最終改正）。条例上の常駐義務なし
  - フロント不要・スタッフ常駐不要・<strong>駆けつけ10分以内</strong>・<strong>キーボックス不可</strong>
  - 申請添付に <strong>「半径200m以内の見取図」</strong>（中央区100mより広い）と「経路図」が必要
  - <strong>区面積の約91%が住居系用途地域</strong>（旅館業の物理的な営業可能エリアが極端に限られる）
  - 学校・児童福祉施設・図書館から <strong>110m以内</strong> は意見聴取の結果次第で不許可
  - <strong>マンション運営は管理組合の許諾必須</strong>（運営要領第3条第2項）
  - 苦情対応 30分以内（保健所運用）
  - 住居専用地域での営業制限条例案を検討中（民泊側に主に影響）
  - 影響：人件費 1名+ICT で 400〜700万円/年（中央区より大幅安）。ただし候補物件選定で苦戦するため、aspect="立地・規制" / direction="マイナス" / score_adjustment -10〜-15 を物件用途地域が住居系ならセット

▼ 京都市
  - 「駆けつけ要員10分以内」が厳格運用。条例で施設外でも可だが 800m以内が目安
  - 民泊（住宅宿泊事業）は別途、玄関帳場類似設備要

判定への反映：
- 物件の住所から該当区を特定し、上記条例の負担を impacts に必ず含める
- 中央区・渋谷区（新規申請ベース）→ aspect="旅館業法" / direction="マイナス" /
  score_adjustment_hint -10〜-20、affected_rules に「区条例」「営業者常駐義務」「人件費」を含める
- 渋谷区物件で「隣に自社ビル」「同一所有者の隣接物件」がある場合は緩和要因（プラス側）として記載

【質問の戻し方】
- suggested_additional_questions には、入力情報からまだ未確定で、判定を変える可能性のある
  具体的な質問を 3〜5 個入れる。例：
  ・「築年が1981年6月以前か以降か」（旧耐震判定）
  ・「異種用途区画の現況はどうなっているか」
  ・「直通階段は2以上あるか」
  ・「上階住民との利用区分の取り決めはあるか」
  ・「申請予定時期は2026年7月以降か」（渋谷区物件の場合）
  ・「隣接敷地に自社所有の管理拠点候補があるか」（渋谷区物件の場合）

必ず JSON のみを返してください（コードブロック・前置きは不要）。
"""


# ----------------------------------------------------------------------
# メイン関数
# ----------------------------------------------------------------------


def interpret_context(
    additional_context: str,
    project: ProjectInput,
    report_summary: Dict[str, Any],
) -> Optional[ContextImpact]:
    """追加情報を Gemini で解釈し、ContextImpact を返す."""
    if not additional_context or not additional_context.strip():
        return None

    api_key = os.getenv("GEMINI_API_KEY")
    if not _GEMINI_AVAILABLE or not api_key:
        return ContextImpact(
            raw_context=additional_context,
            warning="LLMが未設定のため追加情報の解釈をスキップしました。"
            "重みやスコアへの自動反映は行われませんが、レポートには記述として残ります。",
        )

    model_primary = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    # 1.5-flash は v1beta API でサポートされなくなったため除外
    fallback_models = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
    models_to_try = [model_primary] + [m for m in fallback_models if m != model_primary]

    user_input = json.dumps(
        {
            "project": {
                "address": project.address,
                "business_type": project.business_type.value,
                "structure": project.structure,
                "floor_area_m2": project.floor_area_m2,
                "floors_above": project.floors_above,
                "built_year": project.built_year,
                "has_inspection_certificate": project.has_inspection_certificate,
            },
            "report_summary": report_summary,
            "additional_context": additional_context,
        },
        ensure_ascii=False,
        indent=2,
    )

    client = google_genai.Client(api_key=api_key)
    config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        max_output_tokens=4096,
    )

    last_error: Optional[Exception] = None
    for model_name in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[user_input],
                    config=config,
                )
                text = response.text or "{}"
                return _parse_impact(text, additional_context)
            except Exception as exc:  # noqa: BLE001
                err_str = str(exc)
                last_error = exc
                if "503" in err_str or "UNAVAILABLE" in err_str:
                    time.sleep(2 ** attempt)
                    continue
                if "429" in err_str:
                    time.sleep(5)
                    continue
                logger.warning(f"Context interpretation error on {model_name}: {exc}")
                break

    return ContextImpact(
        raw_context=additional_context,
        warning=f"追加情報の解釈に失敗：{last_error}",
    )


def _parse_impact(text: str, raw_context: str) -> ContextImpact:
    """LLM応答をContextImpactに変換."""
    text = text.strip()
    if text.startswith("```"):
        lines = [l for l in text.split("\n") if not l.startswith("```")]
        text = "\n".join(lines)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ContextImpact(
            raw_context=raw_context,
            warning=f"LLM応答のJSONパース失敗：{text[:200]}",
        )

    impacts: List[ContextImpactItem] = []
    for item in payload.get("impacts", []) or []:
        try:
            impacts.append(
                ContextImpactItem(
                    aspect=str(item.get("aspect", "その他")),
                    direction=str(item.get("direction", "中立")),
                    summary=str(item.get("summary", "")),
                    affected_rules=[str(x) for x in (item.get("affected_rules") or [])],
                    score_adjustment_hint=int(item.get("score_adjustment_hint", 0)),
                )
            )
        except Exception:  # noqa: BLE001
            continue

    return ContextImpact(
        raw_context=raw_context,
        overall_summary=str(payload.get("overall_summary", "")),
        impacts=impacts,
        suggested_additional_questions=[
            str(x) for x in (payload.get("suggested_additional_questions") or [])
        ],
    )
