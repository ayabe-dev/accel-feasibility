"""Phase 5-B：旅館業法 構造設備基準のチェックエンジン.

旅館業法施行令1条と自治体条例の基準を組み合わせて評価。
入力：ProjectInput + 自治体キー（任意）
出力：LodgingBusinessCheckItem のリスト
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .models import CheckStatus, LodgingBusinessCheckItem, ProjectInput

_MUNI_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "municipality_rules.yaml"
)


def _load_municipality_rules() -> dict:
    if not _MUNI_CONFIG_PATH.exists():
        return {}
    with _MUNI_CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_MUNI = _load_municipality_rules()


# ----------------------------------------------------------------------
# 各項目チェック
# ----------------------------------------------------------------------


def _check_room_count() -> LodgingBusinessCheckItem:
    return LodgingBusinessCheckItem(
        rule_id="room_count",
        item_name="客室数",
        standard="1室以上（2018年法改正で旧基準の5室以上は撤廃）",
        status=CheckStatus.COMPLIANT,
        note="客室1室から旅館業営業可能",
    )


def _check_room_area(municipality_key: Optional[str]) -> LodgingBusinessCheckItem:
    standard_m2 = 7.0
    bed_only_m2 = 9.0
    note = "客室1室7㎡以上（寝台のみは9㎡以上）"

    if municipality_key:
        muni = _MUNI.get("municipalities", {}).get(municipality_key, {})
        room_rule = muni.get("room_area")
        if room_rule:
            standard_m2 = room_rule.get("min_m2", standard_m2)
            bed_only_m2 = room_rule.get("bed_only_min_m2", bed_only_m2)
            note = f"{muni.get('name', municipality_key)}：{room_rule.get('note', note)}"

    return LodgingBusinessCheckItem(
        rule_id="room_area",
        item_name="客室面積",
        standard=f"1室{standard_m2}㎡以上（寝台のみ{bed_only_m2}㎡以上）",
        status=CheckStatus.NEEDS_REVIEW,
        note=f"{note}。各客室の有効面積を平面図で確認",
    )


def _check_front_desk(municipality_key: Optional[str]) -> LodgingBusinessCheckItem:
    note = (
        "玄関帳場（フロント）または ICT代替設備＋緊急対応体制（10分以内の駆け付け等）"
    )
    if municipality_key:
        muni = _MUNI.get("municipalities", {}).get(municipality_key, {})
        fd_rule = muni.get("front_desk")
        if fd_rule:
            note = f"{muni.get('name', municipality_key)}：{fd_rule.get('note', note)}"

    return LodgingBusinessCheckItem(
        rule_id="front_desk",
        item_name="玄関帳場（フロント）",
        standard="面接可能な構造、または ICT代替設備＋緊急対応体制",
        status=CheckStatus.NEEDS_REVIEW,
        note=note,
    )


def _check_bath_facility() -> LodgingBusinessCheckItem:
    return LodgingBusinessCheckItem(
        rule_id="bath",
        item_name="入浴設備",
        standard="適当な規模の入浴設備（近隣の公衆浴場活用も可）",
        status=CheckStatus.NEEDS_REVIEW,
        note="客室内バスまたは共同浴室。給湯設備の容量も確認",
    )


def _check_toilet() -> LodgingBusinessCheckItem:
    return LodgingBusinessCheckItem(
        rule_id="toilet",
        item_name="便所",
        standard="客用便所を備え、共同便所の場合は男女別",
        status=CheckStatus.NEEDS_REVIEW,
        note="客室数・宿泊定員に応じた個数",
    )


def _check_water_supply() -> LodgingBusinessCheckItem:
    return LodgingBusinessCheckItem(
        rule_id="water",
        item_name="給排水設備",
        standard="水質基準適合の給水、衛生的な排水",
        status=CheckStatus.NEEDS_REVIEW,
        note="井戸水の場合は水質検査結果が必要",
    )


def _check_ventilation_lighting() -> LodgingBusinessCheckItem:
    return LodgingBusinessCheckItem(
        rule_id="ventilation_lighting",
        item_name="換気・採光・照明・防湿",
        standard="衛生上必要な構造設備",
        status=CheckStatus.NEEDS_REVIEW,
        note="自然換気・採光、防露・防カビ対策",
    )


def _check_distance_to_school(municipality_key: Optional[str]) -> LodgingBusinessCheckItem:
    distance_m = 100
    extra = ""
    if municipality_key:
        muni = _MUNI.get("municipalities", {}).get(municipality_key, {})
        dist_rule = muni.get("distance_to_school")
        if dist_rule:
            distance_m = dist_rule.get("distance_m", distance_m)
            extra = f"（{muni.get('name', municipality_key)}：{dist_rule.get('note', '')}）"
    return LodgingBusinessCheckItem(
        rule_id="distance_to_school",
        item_name="学校等からの距離",
        standard=f"半径{distance_m}m以内の学校・児童福祉施設等の有無を確認 {extra}",
        status=CheckStatus.NEEDS_REVIEW,
        note="該当ありなら都道府県知事への意見聴取が必要（旅館業法3条3項・4項）",
    )


# ----------------------------------------------------------------------
# 公開関数
# ----------------------------------------------------------------------


def run_lodging_business_checks(
    project: ProjectInput,
    municipality_key: Optional[str] = None,
) -> List[LodgingBusinessCheckItem]:
    return [
        _check_room_count(),
        _check_room_area(municipality_key),
        _check_front_desk(municipality_key),
        _check_bath_facility(),
        _check_toilet(),
        _check_water_supply(),
        _check_ventilation_lighting(),
        _check_distance_to_school(municipality_key),
    ]
