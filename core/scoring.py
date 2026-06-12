"""総合スコアリングエンジン.

各評価項目に「項目スコア（0〜100）」と「重み（0〜10）」を持たせ、
重み付き平均で総合スコア（0〜100）を算出する。

Hard constraint（立地不可・市街化調整区域）はスコアではなく
overall_level=NO_GO で強制処理されるため、ここでは扱わない。

スコアの意味：
  100 = 全く問題なし（GO）
   50 = 要対応・条件付き（CONDITIONAL/NEEDS_REVIEW）
    0 = 不適合・要改修（NON_COMPLIANT）
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .models import (
    CheckStatus,
    FeasibilityReport,
    InvestigationPattern,
    JudgmentLevel,
)


class ScoreItem(BaseModel):
    """単一項目のスコア."""

    key: str  # 内部キー
    label: str  # 表示名
    category: str  # 「立地」「既存建物」「建基法」「消防」「旅館業」等
    score: float  # 0-100
    weight: float  # 0-10
    note: str = ""  # 評価コメント

    @property
    def weighted(self) -> float:
        return self.score * self.weight


class ScoreBreakdown(BaseModel):
    """総合スコアの分解."""

    total: float = 0.0  # 0-100
    items: List[ScoreItem] = Field(default_factory=list)
    grade: str = "C"  # S/A/B/C/D
    blocked: bool = False  # NO-GO ならスコアは無効
    blocked_reason: str = ""

    @property
    def total_weight(self) -> float:
        return sum(item.weight for item in self.items) or 1.0


# ---------------------------------------------------------------------------
# デフォルト重み
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: Dict[str, float] = {
    # 立地・規制系
    "distance_regulation": 3.0,  # 学校等100m（手続き上の連絡が主、却下は稀なため軽め）
    "district_plan": 6.0,  # 地区計画
    "fire_district": 5.0,  # 防火地域
    "coverage_far": 4.0,  # 建ぺい率・容積率の余裕
    # 既存建物
    "investigation_pattern": 7.0,  # 調査パターン A/B/C/D
    "milestone_seismic": 8.0,  # 新耐震/木造2000/構造2007
    # 申請・法適合
    "application_required": 5.0,  # 確認申請の重さ
    # 建基法（チェックリスト集約）
    "building_code_law27": 9.0,  # 法27条 耐火
    "building_code_evac": 8.0,  # 令121 二方向避難
    "building_code_other": 5.0,  # その他建基法
    "structural_load": 7.0,  # 法20条 構造耐力
    # 消防
    "fire_safety_critical": 6.0,  # SP・屋内消火栓
    "fire_safety_basic": 3.0,  # 自火報・誘導灯
    # 旅館業法
    "room_area": 4.0,  # 客室面積
    "front_desk": 3.0,  # 玄関帳場
    # コスト・期間
    "cost_efficiency": 5.0,  # コストレンジ
    "timeline": 4.0,  # 期間
}


def compute_score(
    report: FeasibilityReport,
    weights: Optional[Dict[str, float]] = None,
) -> ScoreBreakdown:
    """FeasibilityReport から総合スコアを計算."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    # NO-GO は強制ブロック
    if report.overall_level == JudgmentLevel.NO_GO:
        return ScoreBreakdown(
            total=0.0,
            grade="✕",
            blocked=True,
            blocked_reason=report.overall_summary,
        )

    items: List[ScoreItem] = []

    # 1. 距離規制
    #    実務上は「都道府県知事への意見聴取が必要」という手続きが課されるだけで、
    #    施設があってもほぼ却下されない。よって減点は軽微（100→75）
    if report.geo.nearby_facilities:
        distance_score = 75.0
        note = (
            f"半径100m以内に{len(report.geo.nearby_facilities)}件 → "
            "保健所への連絡・意見聴取が必要（手続き上の対応のみ、却下はほぼなし）"
        )
    else:
        distance_score = 100.0
        note = "100m以内に対象施設なし"
    items.append(
        ScoreItem(
            key="distance_regulation",
            label="距離規制（学校・保育園100m）",
            category="立地・規制",
            score=distance_score,
            weight=w["distance_regulation"],
            note=note,
        )
    )

    # 2. 地区計画
    if report.geo.district_plan_name:
        dp_score = 50.0
        note = f"地区計画あり：{report.geo.district_plan_name}"
    else:
        dp_score = 100.0
        note = "地区計画指定なし"
    items.append(
        ScoreItem(
            key="district_plan",
            label="地区計画",
            category="立地・規制",
            score=dp_score,
            weight=w["district_plan"],
            note=note,
        )
    )

    # 3. 防火地域（耐火コストへの影響）
    fd = report.geo.fire_district
    if fd == "fire_district":
        fd_score = 40.0
        note = "防火地域 → 耐火建築物等が必須"
    elif fd == "quasi_fire_district":
        fd_score = 65.0
        note = "準防火地域 → 規模・階数による要件"
    else:
        fd_score = 100.0
        note = "防火指定なし"
    items.append(
        ScoreItem(
            key="fire_district",
            label="防火地域",
            category="立地・規制",
            score=fd_score,
            weight=w["fire_district"],
            note=note,
        )
    )

    # 4. 建ぺい率・容積率の余裕
    cov = report.geo.coverage_ratio_pct or 60
    far = report.geo.floor_area_ratio_pct or 200
    cf_score = min(100.0, (far / 200.0) * 50 + 50)  # 容積率200%でちょうど100点
    items.append(
        ScoreItem(
            key="coverage_far",
            label="建ぺい率・容積率",
            category="立地・規制",
            score=cf_score,
            weight=w["coverage_far"],
            note=f"建ぺい{cov}% / 容積{far}%",
        )
    )

    # 5. 調査パターン A〜D
    pattern_scores = {
        InvestigationPattern.A: 100,
        InvestigationPattern.B: 75,
        InvestigationPattern.C: 50,
        InvestigationPattern.D: 25,
        InvestigationPattern.UNKNOWN: 50,
    }
    items.append(
        ScoreItem(
            key="investigation_pattern",
            label="既存建物 調査パターン",
            category="既存建物",
            score=pattern_scores[report.pattern],
            weight=w["investigation_pattern"],
            note=f"パターン {report.pattern.value}",
        )
    )

    # 6. 法改正節目またぎ
    milestone_count = sum(
        1 for warning in report.warnings if "新耐震" in warning or "木造2000" in warning or "2007年" in warning
    )
    ms_score = max(0.0, 100.0 - milestone_count * 35)
    items.append(
        ScoreItem(
            key="milestone_seismic",
            label="法改正節目またぎ",
            category="既存建物",
            score=ms_score,
            weight=w["milestone_seismic"],
            note=f"節目またぎ {milestone_count} 件" if milestone_count else "節目またぎなし",
        )
    )

    # 7. 確認申請
    if report.application_requirement:
        ar_score = 50.0 if report.application_requirement.required else 90.0
        note = "申請必要（事前協議2〜3ヶ月）" if report.application_requirement.required else "申請不要（遡及あり）"
    else:
        ar_score = 70.0
        note = "判定不能"
    items.append(
        ScoreItem(
            key="application_required",
            label="用途変更確認申請",
            category="申請",
            score=ar_score,
            weight=w["application_required"],
            note=note,
        )
    )

    # 8. 建基法 - 法27条 耐火
    law27 = next((c for c in report.building_code_checks if c.rule_id == "law_27"), None)
    if law27:
        l27_score = _status_to_score(law27.status)
        items.append(
            ScoreItem(
                key="building_code_law27",
                label="法27条 特殊建築物の耐火",
                category="建基法",
                score=l27_score,
                weight=w["building_code_law27"],
                note=law27.requirement[:60],
            )
        )

    # 9. 建基法 - 二方向避難
    evac = next((c for c in report.building_code_checks if c.rule_id == "ord_121"), None)
    if evac:
        items.append(
            ScoreItem(
                key="building_code_evac",
                label="令121条 二方向避難",
                category="建基法",
                score=_status_to_score(evac.status),
                weight=w["building_code_evac"],
                note=evac.requirement[:60],
            )
        )

    # 10. 建基法 その他平均
    other_codes = [
        c
        for c in report.building_code_checks
        if c.rule_id not in {"law_27", "ord_121", "law_20"}
    ]
    if other_codes:
        avg = sum(_status_to_score(c.status) for c in other_codes) / len(other_codes)
        items.append(
            ScoreItem(
                key="building_code_other",
                label="建基法その他（廊下・採光・防火区画 等）",
                category="建基法",
                score=avg,
                weight=w["building_code_other"],
                note=f"{len(other_codes)}規定の平均",
            )
        )

    # 11. 構造耐力（法20条）
    law20 = next((c for c in report.building_code_checks if c.rule_id == "law_20"), None)
    if law20:
        items.append(
            ScoreItem(
                key="structural_load",
                label="法20条 構造耐力",
                category="建基法",
                score=_status_to_score(law20.status),
                weight=w["structural_load"],
                note=law20.requirement[:60],
            )
        )

    # 12. 消防 - SP・屋内消火栓
    critical_fire = [
        f for f in report.fire_safety_checks if f.rule_id in {"sprinkler", "indoor_hydrant"}
    ]
    if critical_fire:
        # 設置義務「あり」だが「未設」→低スコア
        score_sum = 0
        for f in critical_fire:
            score_sum += 60 if f.required else 90
        score = score_sum / len(critical_fire)
        items.append(
            ScoreItem(
                key="fire_safety_critical",
                label="消防 SP・屋内消火栓",
                category="消防",
                score=score,
                weight=w["fire_safety_critical"],
                note=f"{len(critical_fire)}設備の平均",
            )
        )

    # 13. 消防 - 自火報・誘導灯
    basic_fire = [
        f
        for f in report.fire_safety_checks
        if f.rule_id in {"auto_fire_alarm", "emergency_light", "extinguisher"}
    ]
    if basic_fire:
        # 自火報は実質必須なので「要設置」を50点扱い
        items.append(
            ScoreItem(
                key="fire_safety_basic",
                label="消防 自火報・誘導灯",
                category="消防",
                score=70.0,
                weight=w["fire_safety_basic"],
                note=f"{len(basic_fire)}設備：全件設置要",
            )
        )

    # 14. 旅館業法 - 客室面積
    room = next(
        (c for c in report.lodging_business_checks if c.rule_id == "room_area"),
        None,
    )
    if room:
        items.append(
            ScoreItem(
                key="room_area",
                label="客室面積",
                category="旅館業法",
                score=70.0,  # 図面要確認なのでデフォルト中央
                weight=w["room_area"],
                note=room.standard[:60],
            )
        )

    # 15. 玄関帳場
    fd_check = next(
        (c for c in report.lodging_business_checks if c.rule_id == "front_desk"),
        None,
    )
    if fd_check:
        items.append(
            ScoreItem(
                key="front_desk",
                label="玄関帳場（フロント）",
                category="旅館業法",
                score=70.0,
                weight=w["front_desk"],
                note="ICT代替可（自治体ごとに運用差）",
            )
        )

    # 16. コストレンジ
    if report.cost_estimate:
        ce = report.cost_estimate
        cost_total = ce.total_cost_max
        if cost_total < 2000:
            cost_score = 95.0
        elif cost_total < 5000:
            cost_score = 80.0
        elif cost_total < 10000:
            cost_score = 60.0
        elif cost_total < 20000:
            cost_score = 40.0
        else:
            cost_score = 20.0
        items.append(
            ScoreItem(
                key="cost_efficiency",
                label="総コスト見通し",
                category="コスト・期間",
                score=cost_score,
                weight=w["cost_efficiency"],
                note=f"上限 {cost_total:,}万円",
            )
        )

        # 17. 期間
        months = ce.total_months_max
        if months <= 6:
            tl_score = 95.0
        elif months <= 12:
            tl_score = 80.0
        elif months <= 18:
            tl_score = 60.0
        elif months <= 24:
            tl_score = 40.0
        else:
            tl_score = 20.0
        items.append(
            ScoreItem(
                key="timeline",
                label="期間見通し",
                category="コスト・期間",
                score=tl_score,
                weight=w["timeline"],
                note=f"最大 {months} ヶ月",
            )
        )

    # 加重平均
    total_w = sum(item.weight for item in items) or 1.0
    total = sum(item.weighted for item in items) / total_w

    grade = _to_grade(total)

    return ScoreBreakdown(total=round(total, 1), items=items, grade=grade)


def _status_to_score(status: CheckStatus) -> float:
    return {
        CheckStatus.COMPLIANT: 100.0,
        CheckStatus.NOT_APPLICABLE: 90.0,
        CheckStatus.NEEDS_REVIEW: 60.0,
        CheckStatus.UNKNOWN: 50.0,
        CheckStatus.NON_COMPLIANT: 20.0,
    }.get(status, 50.0)


def _to_grade(score: float) -> str:
    if score >= 85:
        return "S"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    return "D"


# ---------------------------------------------------------------------------
# 重み項目のメタデータ（ガイドページで使う）
# ---------------------------------------------------------------------------


WEIGHT_METADATA = [
    {
        "key": "distance_regulation",
        "label": "距離規制（学校・保育園100m）",
        "category": "立地・規制",
        "description": "旅館業法3条3項・自治体条例による距離規制。100m以内に学校・児童福祉施設等があると都道府県知事への意見聴取が必要。XKT006/007で自動取得。",
    },
    {
        "key": "district_plan",
        "label": "地区計画",
        "category": "立地・規制",
        "description": "都市計画法に基づく地区計画。用途地域の上に被さる詳細規制で、ホテル禁止のケースもある。XKT023で取得。",
    },
    {
        "key": "fire_district",
        "label": "防火地域",
        "category": "立地・規制",
        "description": "建基法61条。防火地域なら原則耐火建築物、準防火地域なら規模・階数により要件。XKT014で取得。",
    },
    {
        "key": "coverage_far",
        "label": "建ぺい率・容積率",
        "category": "立地・規制",
        "description": "建基法52・53条。容積率の余裕が大きいほど客室数・付帯設備の自由度が高い。",
    },
    {
        "key": "investigation_pattern",
        "label": "既存建物 調査パターン",
        "category": "既存建物",
        "description": "Phase 2マトリクスのA〜D判定。検査済証×築年数×改修履歴で調査負荷が決まる。",
    },
    {
        "key": "milestone_seismic",
        "label": "法改正節目またぎ",
        "category": "既存建物",
        "description": "新耐震1981 / 木造2000年基準 / 構造計算2007年改正をまたぐと構造調査が必須に。",
    },
    {
        "key": "application_required",
        "label": "用途変更確認申請",
        "category": "申請",
        "description": "建基法6条1項1号・87条。200㎡超で非類似用途間なら申請必要。",
    },
    {
        "key": "building_code_law27",
        "label": "法27条 特殊建築物の耐火",
        "category": "建基法",
        "description": "3階以上または2階300/600㎡基準で耐火建築物等が必要。",
    },
    {
        "key": "building_code_evac",
        "label": "令121条 二方向避難",
        "category": "建基法",
        "description": "避難階以外の宿泊室合計100㎡/200㎡超で直通階段が2以上必要。",
    },
    {
        "key": "building_code_other",
        "label": "建基法その他",
        "category": "建基法",
        "description": "廊下幅(令119)、歩行距離(令120)、排煙(令126の2)、内装制限(令128の4)、採光(法28)、防火区画(令112)等の平均。",
    },
    {
        "key": "structural_load",
        "label": "法20条 構造耐力",
        "category": "建基法",
        "description": "旅館の積載荷重(1,800N/㎡相当)で構造再評価が必要。",
    },
    {
        "key": "fire_safety_critical",
        "label": "消防 SP・屋内消火栓",
        "category": "消防",
        "description": "スプリンクラー(11階以上or3,000㎡以上)、屋内消火栓(700/1,400㎡以上)。設置義務の有無で費用大きく変動。",
    },
    {
        "key": "fire_safety_basic",
        "label": "消防 自火報・誘導灯",
        "category": "消防",
        "description": "自動火災報知設備(旅館は原則全件)、誘導灯(全件)、消火器(150㎡以上)。",
    },
    {
        "key": "room_area",
        "label": "客室面積",
        "category": "旅館業法",
        "description": "1室7㎡以上(寝台のみ9㎡以上)。自治体条例で上乗せあり。",
    },
    {
        "key": "front_desk",
        "label": "玄関帳場",
        "category": "旅館業法",
        "description": "対面可能な構造、またはICT代替設備+緊急対応体制。自治体運用差大きい。",
    },
    {
        "key": "cost_efficiency",
        "label": "総コスト見通し",
        "category": "コスト・期間",
        "description": "調査+申請+改修の合計コストレンジ。事業性に直結。",
    },
    {
        "key": "timeline",
        "label": "期間見通し",
        "category": "コスト・期間",
        "description": "案件着手から営業開始までの期間。投資回収のスピードに直結。",
    },
]
