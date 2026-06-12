"""Phase 3：用途変更確認申請の要否判定エンジン.

根拠条文:
  - 建基法6条1項一号（特殊建築物の確認申請）
  - 建基法87条（用途の変更に対する準用）
  - 建基法施行令137条の18（類似用途間）
  - 建基法施行令137条の19（既存不適格遡及）
  - 2019年6月25日改正：用途変更床面積200㎡基準（旧100㎡から拡大）

判定ロジック:
  1. 旅館・ホテルは特殊建築物（法6条1項1号 別表第一(2)項）
  2. 用途変更床面積 ≤ 200㎡ → 確認申請不要（ただし遡及あり）
  3. 用途変更床面積 > 200㎡ かつ 類似用途間（旅館↔ホテル等）→ 不要
  4. 用途変更床面積 > 200㎡ かつ 非類似用途間（住宅→旅館 等）→ 必要

確認申請が不要でも、以下は遡及適用される（要対応）:
  - 法27条（耐火建築物等）
  - 令119条（廊下幅）令120条（歩行距離）令121条（二方向避難）
  - 令126条の2/3（排煙）126条の4/5（非常用照明）
  - 令128条の4/5（内装制限）
  - 法28条（採光・換気）
  - 令112条（防火区画）
"""
from __future__ import annotations

from typing import Optional

from .models import (
    ApplicationRequirementJudgment,
    BusinessType,
    GeoLookupResult,
    ProjectInput,
    ZoningJudgment,
)

# 旅館業法の用途は建基法上「特殊建築物」(別表第一(2)項)
SPECIAL_BUILDING_BUSINESS_TYPES = {
    BusinessType.HOTEL_RYOKAN,
}

# 旅館・ホテルからみた類似用途（住宅は非類似）
SIMILAR_USES_TO_HOTEL = {
    "hotel_ryokan_to_hotel_ryokan",  # 旅館↔ホテル間
}

THRESHOLD_M2 = 200  # 用途変更確認申請の閾値（200㎡）

# 確認申請不要でも遡及する代表的な規定
RETROACTIVE_OBLIGATIONS = [
    "法27条（耐火建築物等とすべき特殊建築物）",
    "令119条（廊下幅 / 旅館客室部分は両側1.6m・片側1.2m以上）",
    "令120条（直通階段までの歩行距離）",
    "令121条（二方向避難 / 宿泊室階の床面積基準）",
    "令126条の2・3（排煙設備）",
    "令126条の4・5（非常用照明）",
    "令128条の4・5（内装制限）",
    "法28条（採光・換気 / 旅館客室の有効採光1/10以上）",
    "令112条（防火区画 / 異種用途区画・竪穴区画）",
    "消防法施行令(5)項イ（自火報・誘導灯・SP等）",
]


def judge_application_requirement(
    project: ProjectInput,
    geo: GeoLookupResult,
    zoning: ZoningJudgment,
) -> ApplicationRequirementJudgment:
    """用途変更確認申請の要否判定."""

    is_special = project.business_type in SPECIAL_BUILDING_BUSINESS_TYPES
    is_similar = False  # 住宅→旅館は非類似で固定（MVPスコープ）
    floor_area = project.floor_area_m2

    articles = [
        "建築基準法6条1項1号（特殊建築物の確認申請）",
        "建築基準法87条（用途変更への準用）",
        "建築基準法施行令137条の18（類似用途）",
    ]

    # 業態が特殊建築物でない → 確認申請不要（このMVPでは旅館固定のため通常通らない）
    if not is_special:
        return ApplicationRequirementJudgment(
            required=False,
            reason="対象業態が特殊建築物に該当しないため、用途変更確認申請は不要です。",
            is_special_building=False,
            is_similar_use=False,
            floor_area_subject_m2=floor_area,
            threshold_m2=THRESHOLD_M2,
            applicable_articles=articles,
            related_obligations=[],
        )

    # 床面積が不明
    if floor_area is None:
        return ApplicationRequirementJudgment(
            required=True,  # 安全側で「必要」と仮置き
            reason=(
                "床面積が未入力のため確定判定できません。安全側で「申請必要」と仮置き。"
                "200㎡以下なら申請不要、200㎡超なら必要（住宅→旅館は非類似のため）。"
            ),
            is_special_building=True,
            is_similar_use=False,
            floor_area_subject_m2=None,
            threshold_m2=THRESHOLD_M2,
            applicable_articles=articles,
            related_obligations=RETROACTIVE_OBLIGATIONS,
        )

    # 200㎡以下
    if floor_area <= THRESHOLD_M2:
        return ApplicationRequirementJudgment(
            required=False,
            reason=(
                f"用途変更部分 {floor_area:,.1f}㎡ ≤ {THRESHOLD_M2}㎡ のため、"
                "確認申請は不要（2019年6月法改正以降）。"
                "ただし、建基法・消防法の各規定は遡及適用されます。"
            ),
            is_special_building=True,
            is_similar_use=False,
            floor_area_subject_m2=floor_area,
            threshold_m2=THRESHOLD_M2,
            applicable_articles=articles,
            related_obligations=RETROACTIVE_OBLIGATIONS,
        )

    # 200㎡超 + 類似用途間（住宅→旅館はここに該当しない）
    if is_similar:
        return ApplicationRequirementJudgment(
            required=False,
            reason=(
                "用途変更部分が200㎡超でも、類似用途間（施行令137条の18）のため"
                "確認申請は不要です。"
            ),
            is_special_building=True,
            is_similar_use=True,
            floor_area_subject_m2=floor_area,
            threshold_m2=THRESHOLD_M2,
            applicable_articles=articles,
            related_obligations=RETROACTIVE_OBLIGATIONS,
        )

    # 200㎡超 + 非類似 → 確認申請必要
    return ApplicationRequirementJudgment(
        required=True,
        reason=(
            f"用途変更部分 {floor_area:,.1f}㎡ > {THRESHOLD_M2}㎡ かつ "
            "住宅→旅館・ホテルは非類似用途間のため、用途変更確認申請が必要です。"
            "申請先：指定確認検査機関 または 建築主事。"
        ),
        is_special_building=True,
        is_similar_use=False,
        floor_area_subject_m2=floor_area,
        threshold_m2=THRESHOLD_M2,
        applicable_articles=articles,
        related_obligations=RETROACTIVE_OBLIGATIONS,
    )
