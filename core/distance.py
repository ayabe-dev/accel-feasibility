"""距離規制（学校等100m）のチェック.

MVP段階では POI 検索 API を統合せず、ユーザーが手動で「近隣に学校等があるか」を
チェックする想定。将来的に 不動産情報ライブラリ / OpenStreetMap POI を統合する。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .models import DistanceCheckResult

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "distance_rules.yaml"
)
_MUNI_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "municipality_rules.yaml"
)


def _load_rules():
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_muni():
    if not _MUNI_PATH.exists():
        return {}
    with _MUNI_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_RULES = _load_rules()
_MUNI = _load_muni()


def check_distance_regulation(
    has_nearby_facility: Optional[bool],
    nearby_facilities: Optional[List[str]] = None,
    municipality_key: Optional[str] = None,
) -> DistanceCheckResult:
    """距離規制チェック.

    Args:
        has_nearby_facility: 100m以内に対象施設があるか（ユーザー回答）
        nearby_facilities: 具体的な施設名（任意）
        municipality_key: 自治体キー（例: 'kyoto_city'）
    """
    default = _RULES.get("default", {})
    distance_m = default.get("distance_meters", 100)

    municipality = None
    if municipality_key:
        # 旧 distance_rules.yaml と 新 municipality_rules.yaml の両対応
        municipality = _RULES.get("municipalities", {}).get(municipality_key)
        if not municipality:
            muni_new = _MUNI.get("municipalities", {}).get(municipality_key, {})
            if muni_new:
                dist_rule = muni_new.get("distance_to_school", {})
                municipality = {
                    "name": muni_new.get("name", municipality_key),
                    "distance_meters": dist_rule.get("distance_m", distance_m),
                    "note": dist_rule.get("note", ""),
                }
        if municipality:
            distance_m = municipality.get("distance_meters", distance_m)

    if has_nearby_facility is None:
        return DistanceCheckResult(
            has_issue=False,
            note=(
                f"半径{distance_m}m以内の学校・児童福祉施設等の有無が未確認です。"
                f"周辺POIを地図で確認してください。"
            ),
        )

    if has_nearby_facility:
        action = default.get(
            "action_required",
            "都道府県知事への意見聴取が必要。設置可否は事前協議で確定。",
        )
        municipality_note = (
            f" / 自治体ルール：{municipality.get('note', '')}"
            if municipality
            else ""
        )
        return DistanceCheckResult(
            has_issue=True,
            nearby_facilities=nearby_facilities or [],
            action_required=action,
            note=(
                f"半径{distance_m}m以内に学校等あり → "
                f"{action}{municipality_note}"
            ),
        )

    return DistanceCheckResult(
        has_issue=False,
        note=f"半径{distance_m}m以内に対象施設なし（ユーザー回答ベース）",
    )
