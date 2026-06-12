"""住所→自治体キーの自動判定ヘルパー."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "municipality_rules.yaml"
)


def _load() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_DATA = _load()


def detect_municipality(address: Optional[str]) -> Optional[str]:
    """住所から自治体キーを推定. matched_address_prefixes の前方一致で判定."""
    if not address:
        return None
    munis = _DATA.get("municipalities", {})
    for key, rule in munis.items():
        prefixes = rule.get("matched_address_prefixes", [])
        for prefix in prefixes:
            if address.startswith(prefix):
                return key
    return None


def get_municipality_name(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return _DATA.get("municipalities", {}).get(key, {}).get("name")
