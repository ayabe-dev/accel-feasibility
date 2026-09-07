"""Phase 5-B：旅館業法 構造設備基準のチェックエンジン.

旅館業法施行令1条と自治体条例の基準を組み合わせて評価。
入力：ProjectInput + 自治体キー（任意）
出力：LodgingBusinessCheckItem のリスト
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .models import BusinessType, CheckStatus, LodgingBusinessCheckItem, ProjectInput

_MUNI_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "municipality_rules.yaml"
)


def _load_municipality_rules() -> dict:
    if not _MUNI_CONFIG_PATH.exists():
        return {}
    with _MUNI_CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_MUNI = _load_municipality_rules()


def _muni(municipality_key: Optional[str]) -> dict:
    """自治体ルールを取得（無ければ空dict）."""
    if not municipality_key:
        return {}
    return _MUNI.get("municipalities", {}).get(municipality_key, {}) or {}


def _muni_name(municipality_key: Optional[str]) -> str:
    return _muni(municipality_key).get("name", municipality_key or "")


def _is_detailed(municipality_key: Optional[str]) -> bool:
    """実データで作り込み済みの自治体か（generic な当て込みではないか）."""
    return _muni(municipality_key).get("detail_level") == "full"


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

    muni = _muni(municipality_key)
    room_rule = muni.get("room_area") or {}
    extras: List[str] = []
    if room_rule.get("simple_lodging_total_min_m2"):
        extras.append(
            f"簡易宿所は客室延床{room_rule['simple_lodging_total_min_m2']}㎡以上"
            f"（宿泊者10人未満は{room_rule.get('simple_lodging_per_guest_m2', 3.3)}㎡×人数）"
        )
    if room_rule.get("measurement"):
        extras.append(f"面積算定は{room_rule['measurement']}")
    if room_rule.get("basement_prohibited"):
        extras.append("客室は原則地階に設けられない")
    if room_rule.get("window_required"):
        extras.append("窓のない客室は不可")

    return LodgingBusinessCheckItem(
        rule_id="room_area",
        item_name="客室面積",
        standard=f"1室{standard_m2}㎡以上（寝台のみ{bed_only_m2}㎡以上）",
        status=CheckStatus.NEEDS_REVIEW,
        note=f"{note}。各客室の有効面積を平面図で確認"
             + ("。" + "／".join(extras) if extras else ""),
    )


def _check_capacity(project: ProjectInput,
                    municipality_key: Optional[str]) -> Optional[LodgingBusinessCheckItem]:
    """定員の法令上限（有効面積○㎡につき1人）."""
    cap = _muni(municipality_key).get("capacity") or {}
    if not cap:
        return None
    if project.business_type == BusinessType.SIMPLE_LODGING:
        per_guest = cap.get("simple_lodging_m2_per_guest")
        label = "簡易宿所営業"
    else:
        per_guest = cap.get("hotel_ryokan_m2_per_guest")
        label = "旅館・ホテル営業"
    if not per_guest:
        return None

    note = f"{_muni_name(municipality_key)}：{cap.get('note', '')}"
    if project.floor_area_m2:
        # 有効面積は延床の一部。ここでは客室占有率0.62を仮置きした概算を示す
        eff = project.floor_area_m2 * 0.62
        legal_max = int(eff / per_guest)
        note += (
            f"／概算：延床{project.floor_area_m2:.0f}㎡×客室占有率0.62＝有効面積約{eff:.0f}㎡ "
            f"→ 法令上限 約{legal_max}名。ただし実務上は寝具・便所数・消防設備・"
            "近隣対応が先に頭打ちになるため、上限いっぱいの計画は通りにくい"
        )
    excl = cap.get("effective_area_excludes")
    if excl:
        note += f"／有効面積に含めないもの：{'・'.join(excl)}"

    return LodgingBusinessCheckItem(
        rule_id="capacity",
        item_name="宿泊定員の上限",
        standard=f"{label}＝有効面積{per_guest}㎡につき1人",
        status=CheckStatus.NEEDS_REVIEW,
        note=note,
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

    muni = _muni(municipality_key)
    fd = muni.get("front_desk") or {}
    if fd.get("unmanned_allowed") is False:
        note = f"⚠️ 完全無人運営は不可（従業員の常駐が必要）。{note}"
    reqs = fd.get("substitute_requirements") or []
    if reqs:
        note += "／代替設備の要件：" + "、".join(reqs)
    if fd.get("camera_requirements"):
        note += f"／カメラ要件：{fd['camera_requirements']}"
    if fd.get("remote_front_desk_max_distance_m"):
        note += (
            f"／フロント別置は{fd['remote_front_desk_max_distance_m']}m以内"
            f"（{fd.get('remote_front_desk_note', '')}）"
        )
    return LodgingBusinessCheckItem(
        rule_id="front_desk",
        item_name="玄関帳場（フロント）",
        standard=("玄関帳場必須。ICT代替可だが従業員常駐が必要（無人不可）"
                  if fd.get("unmanned_allowed") is False
                  else "面接可能な構造、または ICT代替設備＋緊急対応体制"),
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


def _check_toilet(municipality_key: Optional[str] = None) -> LodgingBusinessCheckItem:
    rule = _muni(municipality_key).get("toilet_count_by_capacity") or {}
    table = rule.get("table") or []
    note = "客室数・宿泊定員に応じた個数"
    standard = "客用便所を備え、共同便所の場合は男女別"
    if table:
        pairs = "／".join(
            f"{r['up_to']}人以下:{r['count']}" for r in table
        )
        standard = f"合計定員別の必要便器数（{pairs}）"
        note = f"{_muni_name(municipality_key)}：{rule.get('note', '')}"
        if rule.get("over_30_note"):
            note += f"／{rule['over_30_note']}"
    return LodgingBusinessCheckItem(
        rule_id="toilet",
        item_name="便所",
        standard=standard,
        status=CheckStatus.NEEDS_REVIEW,
        note=note,
    )


def _check_washbasin(municipality_key: Optional[str]) -> Optional[LodgingBusinessCheckItem]:
    rule = _muni(municipality_key).get("washbasin") or {}
    if not rule:
        return None
    per = rule.get("guests_per_tap")
    return LodgingBusinessCheckItem(
        rule_id="washbasin",
        item_name="洗面設備",
        standard=(f"共同洗面所は定員{per}人につき1給水栓" if per else "宿泊者需要を満たす規模"),
        status=CheckStatus.NEEDS_REVIEW,
        note=f"{_muni_name(municipality_key)}：{rule.get('note', '')}",
    )


def _check_application_fee(project: ProjectInput,
                           municipality_key: Optional[str]) -> Optional[LodgingBusinessCheckItem]:
    rule = _muni(municipality_key).get("application_fee_yen") or {}
    if not rule:
        return None
    key = {
        BusinessType.HOTEL_RYOKAN: "hotel_ryokan",
        BusinessType.SIMPLE_LODGING: "simple_lodging",
    }.get(project.business_type)
    fee = rule.get(key) if key else None
    standard = (f"申請手数料 {fee:,}円" if fee else rule.get("note", ""))
    return LodgingBusinessCheckItem(
        rule_id="application_fee",
        item_name="申請手数料",
        standard=standard,
        status=CheckStatus.COMPLIANT if fee else CheckStatus.NEEDS_REVIEW,
        note=f"{_muni_name(municipality_key)}：{rule.get('note', '')}",
    )


def _check_blocking_conditions(municipality_key: Optional[str]) -> List[LodgingBusinessCheckItem]:
    """事業化そのものが不可になり得る条件（自治体固有）."""
    notes = (_muni(municipality_key).get("building_code_notes") or {}).get("blocking") or []
    items: List[LodgingBusinessCheckItem] = []
    for b in notes:
        items.append(LodgingBusinessCheckItem(
            rule_id=f"blocking_{b.get('id', 'unknown')}",
            item_name=f"⛔ {b.get('title', '')}（要確認・該当すると事業化不可）",
            standard=b.get("title", ""),
            status=CheckStatus.NEEDS_REVIEW,
            note=f"{_muni_name(municipality_key)}：{b.get('detail', '')}",
        ))
    return items


def _check_zoning_notes(municipality_key: Optional[str]) -> Optional[LodgingBusinessCheckItem]:
    """自治体固有の用途地域事情（区に指定のない地域・文教地区など）."""
    z = _muni(municipality_key).get("zoning") or {}
    if not z:
        return None
    parts: List[str] = []
    if z.get("not_designated_in_ward"):
        parts.append(
            f"区内に指定のない用途地域：{'・'.join(z['not_designated_in_ward'])}"
            f"（{z.get('not_designated_note', '')}）"
        )
    for sp in z.get("special_district_prohibitions", []):
        parts.append(sp)
    for ex in z.get("extra_notes", []):
        parts.append(ex)
    if z.get("map_service"):
        parts.append(f"用途地域の確認：{z['map_service']}")
    return LodgingBusinessCheckItem(
        rule_id="zoning_local",
        item_name="用途地域（自治体固有事情）",
        standard="／".join(z.get("allowed", [])) or "—",
        status=CheckStatus.NEEDS_REVIEW,
        note=f"{_muni_name(municipality_key)}：" + "。".join(parts),
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
    items: List[LodgingBusinessCheckItem] = []
    # 事業化そのものが不可になり得る条件を先頭に置く（見落とし防止）
    items.extend(_check_blocking_conditions(municipality_key))
    zoning_local = _check_zoning_notes(municipality_key)
    if zoning_local:
        items.append(zoning_local)
    items.append(_check_room_count())
    items.append(_check_room_area(municipality_key))
    cap = _check_capacity(project, municipality_key)
    if cap:
        items.append(cap)
    items.append(_check_front_desk(municipality_key))
    items.append(_check_bath_facility())
    items.append(_check_toilet(municipality_key))
    wb = _check_washbasin(municipality_key)
    if wb:
        items.append(wb)
    items.append(_check_water_supply())
    items.append(_check_ventilation_lighting())
    items.append(_check_distance_to_school(municipality_key))
    fee = _check_application_fee(project, municipality_key)
    if fee:
        items.append(fee)
    return items
