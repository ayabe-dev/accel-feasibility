"""用途地域 × 業態の判定エンジン.

config/zoning_rules.yaml を読み込み、GIS で取得した用途地域コード・延床面積から
GO / CONDITIONAL / NO_GO を返す.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .models import (
    BusinessType,
    GeoLookupResult,
    JudgmentLevel,
    ProjectInput,
    ZoningJudgment,
)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "zoning_rules.yaml"


def _load_rules() -> Dict[str, Any]:
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


_RULES = _load_rules()


def judge_zoning(
    geo: GeoLookupResult,
    project: ProjectInput,
) -> ZoningJudgment:
    """用途地域に基づく可否判定."""
    # 市街化調整区域は原則として旅館・ホテル建築不可（都市計画法34条許可が必要）
    if geo.city_planning_classification and "市街化調整区域" in geo.city_planning_classification:
        return ZoningJudgment(
            level=JudgmentLevel.NO_GO,
            zoning_name="市街化調整区域",
            reason=(
                "【立地原則不可】市街化調整区域：都市計画法上、原則として旅館・ホテルの新築・"
                "用途変更は不可。都市計画法34条の許可（開発許可・建築許可）が個別に得られれば検討可能だが、"
                "標準的なフローではNO-GO扱い。"
            ),
        )

    zoning_code = geo.zoning_code

    if zoning_code is None:
        return ZoningJudgment(
            level=JudgmentLevel.CONDITIONAL,
            zoning_name=None,
            reason=(
                "用途地域を特定できませんでした。GIS APIキーが未設定か、"
                "住所が市街化調整区域等の可能性があります。手動で用途地域を確認してください。"
            ),
        )

    area_rule = _RULES.get("zoning_areas", {}).get(zoning_code)
    if area_rule is None:
        return ZoningJudgment(
            level=JudgmentLevel.CONDITIONAL,
            zoning_name=geo.zoning_name,
            reason=f"用途地域コード '{zoning_code}' はルールに未登録です。",
        )

    biz_key = _business_key(project.business_type)
    zoning_name = area_rule.get("name", geo.zoning_name)
    if biz_key is None:
        # 住宅宿泊事業（民泊）。建基法上は「住宅」のままなので用途地域の制限は
        # 原則かからない。可否を決めるのは住宅宿泊事業法18条の条例（区域・期間）。
        return ZoningJudgment(
            level=JudgmentLevel.GO,
            zoning_name=zoning_name,
            reason=(
                f"【立地可】{zoning_name}：住宅宿泊事業は住宅を活用する事業のため、"
                "原則として用途地域にかかわらず実施できます（旅館業と異なり"
                "第一種低層住居専用地域でも可）。ただし可否を実際に決めるのは"
                "住宅宿泊事業法18条に基づく自治体条例の区域・期間制限です。"
                "2026年7月の国の技術的助言により、条例で営業日数を実質ゼロにする"
                "『ゼロ日規制』が容認されているため、最新の条例確認が必須です。"
            ),
        )
    biz_rule = area_rule.get(biz_key)

    if biz_rule is None:
        return ZoningJudgment(
            level=JudgmentLevel.CONDITIONAL,
            zoning_name=zoning_name,
            reason=f"業態 '{project.business_type.value}' のルールが未定義です。",
        )

    status = biz_rule.get("status")
    reason = biz_rule.get("reason", "")
    max_area = biz_rule.get("max_floor_area_m2")
    floor_area = project.floor_area_m2

    if status == "prohibited":
        return ZoningJudgment(
            level=JudgmentLevel.NO_GO,
            zoning_name=zoning_name,
            reason=f"【立地不可】{zoning_name}：{reason}",
        )

    if status == "allowed":
        return ZoningJudgment(
            level=JudgmentLevel.GO,
            zoning_name=zoning_name,
            reason=f"【立地可】{zoning_name}：{reason}",
        )

    if status == "conditional":
        # 規模制限の確認
        if max_area is None:
            return ZoningJudgment(
                level=JudgmentLevel.CONDITIONAL,
                zoning_name=zoning_name,
                reason=f"【条件付き可】{zoning_name}：{reason}",
                max_floor_area_m2=max_area,
            )
        if floor_area is None:
            return ZoningJudgment(
                level=JudgmentLevel.CONDITIONAL,
                zoning_name=zoning_name,
                reason=(
                    f"【条件付き可】{zoning_name}：{reason} "
                    f"床面積が未入力のため最終判定不可。"
                ),
                max_floor_area_m2=max_area,
                floor_area_check="床面積を入力すると自動判定可能",
            )
        if floor_area > max_area:
            return ZoningJudgment(
                level=JudgmentLevel.NO_GO,
                zoning_name=zoning_name,
                reason=(
                    f"【立地不可】{zoning_name}：床面積 {floor_area:,.0f}㎡ が "
                    f"上限 {max_area:,.0f}㎡ を超過。減築するか別地域を検討。"
                ),
                max_floor_area_m2=max_area,
                floor_area_check=f"{floor_area:,.0f}㎡ > {max_area:,.0f}㎡",
            )
        return ZoningJudgment(
            level=JudgmentLevel.GO,
            zoning_name=zoning_name,
            reason=(
                f"【立地可】{zoning_name}：床面積 {floor_area:,.0f}㎡ ≤ "
                f"上限 {max_area:,.0f}㎡ で条件クリア。"
            ),
            max_floor_area_m2=max_area,
            floor_area_check=f"{floor_area:,.0f}㎡ ≤ {max_area:,.0f}㎡",
        )

    if status == "special_only":
        return ZoningJudgment(
            level=JudgmentLevel.CONDITIONAL,
            zoning_name=zoning_name,
            reason=(
                f"【特定行政庁の許可が必要】{zoning_name}：{reason} "
                f"建築基準法48条ただし書きによる許可申請が必要。"
            ),
        )

    return ZoningJudgment(
        level=JudgmentLevel.CONDITIONAL,
        zoning_name=zoning_name,
        reason=f"status '{status}' は未定義です。手動確認してください。",
    )


def _business_key(business_type: BusinessType) -> Optional[str]:
    """業態 → zoning_rules.yaml の業態キー.

    簡易宿所は建築基準法上「旅館・ホテル」と同じ用途区分なので hotel を使う
    （目黒区資料でも旅館業法の許可対象として旅館・ホテル営業と簡易宿所営業を並記）。
    民泊（住宅宿泊事業）は建基法上「住宅」扱いで、用途地域による制限を受けない。
    zoning_rules.yaml に引くべき業態ルールが存在しないため None を返し、
    呼び出し側（judge_zoning）が民泊専用の判定文を返す。
    """
    return {
        BusinessType.HOTEL_RYOKAN: "hotel",
        BusinessType.SIMPLE_LODGING: "hotel",
        BusinessType.MINPAKU: None,
    }.get(business_type)


def fire_district_note(geo: GeoLookupResult) -> Optional[str]:
    """防火地域の説明テキストを返す."""
    if not geo.fire_district:
        return None
    rule = _RULES.get("fire_protection_areas", {}).get(geo.fire_district)
    if rule is None:
        return None
    return f"{rule.get('name')}：{rule.get('note')}"
