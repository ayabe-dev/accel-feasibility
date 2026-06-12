"""Phase 5-A：消防法 チェックエンジン.

消防法施行令別表第一 (5)項イ（旅館・ホテル）の主要設備要件をルール化。
入力：ProjectInput
出力：FireSafetyCheckItem のリスト
"""
from __future__ import annotations

from typing import List, Optional

from .models import FireSafetyCheckItem, ProjectInput


def _occupancy(project: ProjectInput) -> int:
    """収容人員の概算. 客室1室3人想定. 不明なら床面積/30㎡."""
    fa = project.floor_area_m2 or 0
    # 単純概算：客室数（床面積/30㎡）×3人
    return int((fa / 30) * 3)


def _is_fireproof_struct(structure: Optional[str]) -> bool:
    if not structure:
        return False
    return any(s in structure for s in ["RC", "SRC", "鉄筋コンクリート", "鉄骨鉄筋"])


# ----------------------------------------------------------------------
# 各設備のチェック
# ----------------------------------------------------------------------


def _check_extinguisher(project: ProjectInput) -> FireSafetyCheckItem:
    fa = project.floor_area_m2 or 0
    required = fa >= 150
    return FireSafetyCheckItem(
        rule_id="extinguisher",
        equipment_name="消火器",
        article="消防法施行令10条",
        required=required or True,  # 旅館は実質全件必要
        threshold_note="延床150㎡以上で義務（旅館は実質ほぼ全件）",
        current_status=f"延床{fa:,.0f}㎡",
        action="未設置なら設置、各階適正配置を確認",
    )


def _check_auto_fire_alarm(project: ProjectInput) -> FireSafetyCheckItem:
    fa = project.floor_area_m2 or 0
    sub_300 = fa < 300
    return FireSafetyCheckItem(
        rule_id="auto_fire_alarm",
        equipment_name="自動火災報知設備",
        article="消防法施行令21条 (5)項イ",
        required=True,
        threshold_note=(
            "旅館・ホテルは原則全件必要。300㎡未満なら特定小規模施設用（無線式）可、"
            "300㎡以上は有線式"
        ),
        current_status=f"延床{fa:,.0f}㎡（{'300㎡未満' if sub_300 else '300㎡以上'}）",
        action="既設なし→新設要。" + ("特定小規模施設用が選択可" if sub_300 else "有線式の設置要"),
    )


def _check_emergency_light(project: ProjectInput) -> FireSafetyCheckItem:
    return FireSafetyCheckItem(
        rule_id="emergency_light",
        equipment_name="誘導灯・誘導標識",
        article="消防法施行令26条",
        required=True,
        threshold_note="旅館・ホテルは全件設置義務",
        current_status="既設の有無を現地確認",
        action="廊下・階段・出入口の誘導灯を設置・点検",
    )


def _check_fire_notification(project: ProjectInput) -> FireSafetyCheckItem:
    fa = project.floor_area_m2 or 0
    required = fa >= 500
    return FireSafetyCheckItem(
        rule_id="fire_notification",
        equipment_name="消防機関へ通報する火災報知設備",
        article="消防法施行令23条",
        required=required,
        threshold_note="延床500㎡以上で設置義務",
        current_status=f"延床{fa:,.0f}㎡",
        action="該当なら設置、自動通報装置の動作確認",
    )


def _check_sprinkler(project: ProjectInput) -> FireSafetyCheckItem:
    fa = project.floor_area_m2 or 0
    floors_above = project.floors_above or 0
    required = (floors_above >= 11) or (fa >= 3000)
    note_reason = []
    if floors_above >= 11:
        note_reason.append(f"{floors_above}階建（11階以上）")
    if fa >= 3000:
        note_reason.append(f"延床{fa:,.0f}㎡（3,000㎡以上）")
    return FireSafetyCheckItem(
        rule_id="sprinkler",
        equipment_name="スプリンクラー設備",
        article="消防法施行令12条",
        required=required,
        threshold_note=(
            "11階以上、または延床3,000㎡以上、または地階・無窓階で1,000㎡以上 等"
        ),
        current_status=f"地上{floors_above}階 / 延床{fa:,.0f}㎡",
        action=(
            "設置義務あり：" + " / ".join(note_reason)
            if required
            else "本則の規模未満。ただし無窓階・地階の規定は別途確認"
        ),
    )


def _check_indoor_hydrant(project: ProjectInput) -> FireSafetyCheckItem:
    fa = project.floor_area_m2 or 0
    structure = project.structure
    is_fp = _is_fireproof_struct(structure)
    # 耐火構造で内装難燃材料 → 700㎡が2倍に緩和（1,400㎡）等の規定あり
    effective_threshold = 1400 if is_fp else 700
    required = fa >= effective_threshold
    return FireSafetyCheckItem(
        rule_id="indoor_hydrant",
        equipment_name="屋内消火栓設備",
        article="消防法施行令11条",
        required=required,
        threshold_note=(
            "延床700㎡以上で原則必要。耐火構造で内装難燃材料以上なら2倍緩和（1,400㎡）"
        ),
        current_status=f"延床{fa:,.0f}㎡ / 構造{structure or '不明'}",
        action=(
            f"設置義務あり（実効閾値{effective_threshold}㎡超）"
            if required
            else "未満。ただし建物用途・無窓階の規定で例外あり"
        ),
    )


def _check_fire_manager(project: ProjectInput) -> FireSafetyCheckItem:
    fa = project.floor_area_m2 or 0
    occ = _occupancy(project)
    if occ >= 30:
        kind = "甲種防火管理者" if fa >= 300 else "乙種防火管理者"
        return FireSafetyCheckItem(
            rule_id="fire_manager",
            equipment_name="防火管理者・消防計画",
            article="消防法8条",
            required=True,
            threshold_note=(
                "(5)項イは特定防火対象物。収容人員30人以上で防火管理者選任義務。"
                "延床300㎡以上は甲種"
            ),
            current_status=f"想定収容人員 約{occ}人 / 延床{fa:,.0f}㎡",
            action=f"{kind}を選任、消防計画を所轄消防に届出",
        )
    return FireSafetyCheckItem(
        rule_id="fire_manager",
        equipment_name="防火管理者・消防計画",
        article="消防法8条",
        required=False,
        threshold_note="収容人員30人未満なら選任義務はないが、自主管理は推奨",
        current_status=f"想定収容人員 約{occ}人",
        action="任意で防火管理体制を整備",
    )


def _check_fire_compliance(project: ProjectInput) -> FireSafetyCheckItem:
    return FireSafetyCheckItem(
        rule_id="fire_compliance",
        equipment_name="消防法令適合通知書",
        article="旅館業法施行規則（消防への申請）",
        required=True,
        threshold_note="旅館業許可の前提として必須。所轄消防への申請＋現地検査",
        current_status="未取得",
        action="工事完了後、所轄消防に申請し現地検査を受ける",
    )


# ----------------------------------------------------------------------
# 公開関数
# ----------------------------------------------------------------------


def run_fire_safety_checks(project: ProjectInput) -> List[FireSafetyCheckItem]:
    return [
        _check_extinguisher(project),
        _check_auto_fire_alarm(project),
        _check_emergency_light(project),
        _check_fire_notification(project),
        _check_sprinkler(project),
        _check_indoor_hydrant(project),
        _check_fire_manager(project),
        _check_fire_compliance(project),
    ]
