"""費用・期間の概算見積エンジン.

config/cost_estimates.yaml を読み込み、調査パターン（A〜D）と業態から
費用・期間レンジを返す.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .models import BusinessType, CostEstimate, InvestigationPattern, ProjectInput

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "cost_estimates.yaml"
)


def _load() -> Dict[str, Any]:
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


_CONFIG = _load()


def estimate(
    pattern: InvestigationPattern,
    project: ProjectInput,
) -> Optional[CostEstimate]:
    """費用・期間の見積."""
    if pattern == InvestigationPattern.UNKNOWN:
        return None
    pattern_key = pattern.value
    rule = _CONFIG.get("patterns", {}).get(pattern_key)
    if rule is None:
        return None

    inv = rule["investigation"]
    app = rule["application"]
    ren = rule["renovation"]
    tot = rule["total"]

    # 構造係数で改修コストを調整
    mult = _structure_multiplier(project.structure)
    ren_min = int(ren["cost_min"] * mult)
    ren_max = int(ren["cost_max"] * mult)

    return CostEstimate(
        investigation_cost_min=inv["cost_min"],
        investigation_cost_max=inv["cost_max"],
        application_cost_min=app["cost_min"],
        application_cost_max=app["cost_max"],
        renovation_cost_min=ren_min,
        renovation_cost_max=ren_max,
        total_months_min=tot["months_min"],
        total_months_max=tot["months_max"],
        confidence=tot["confidence"],
        investigation_tasks=inv.get("tasks", []),
        application_tasks=app.get("tasks", []),
        renovation_tasks=ren.get("tasks", []),
    )


def _structure_multiplier(structure: Optional[str]) -> float:
    """構造種別から改修コスト係数を取得."""
    if not structure:
        return 1.0
    mults = _CONFIG.get("structure_multipliers", {})
    if "SRC" in structure or "鉄骨鉄筋" in structure:
        return mults.get("src", {}).get("multiplier", 1.25)
    if "RC" in structure or "鉄筋コンクリート" in structure:
        return mults.get("rc", {}).get("multiplier", 1.15)
    if "鉄骨" in structure or "S造" in structure:
        return mults.get("s_steel", {}).get("multiplier", 1.10)
    if "木造" in structure or "W造" in structure:
        return mults.get("wooden", {}).get("multiplier", 1.0)
    return 1.0


def scale_notes(project: ProjectInput) -> Optional[str]:
    """延床面積に応じた追加コメント."""
    fa = project.floor_area_m2
    if fa is None:
        return None
    thresholds = _CONFIG.get("scale_thresholds", {})
    if fa < 200:
        return thresholds.get("under_200m2", {}).get("note")
    if fa < 500:
        return thresholds.get("m2_200_to_500", {}).get("note")
    if fa < 1000:
        return thresholds.get("m2_500_to_1000", {}).get("note")
    if fa < 3000:
        return thresholds.get("over_1000m2", {}).get("note")
    return thresholds.get("over_3000m2", {}).get("note")
