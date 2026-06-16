"""収益性エンジンのエッジケース・回帰テスト."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import judgment, profitability
from core.models import BusinessType, ProjectInput


def _report(addr="東京都新宿区大久保2-1-1", bt=BusinessType.HOTEL_RYOKAN,
            fa=271.0, structure="RC造", built=1990):
    p = ProjectInput(address=addr, business_type=bt, floor_area_m2=fa,
                     structure=structure, built_year=built)
    return judgment.run(project=p, docs=[])


def test_basic_ranges():
    res = profitability.compute(_report(), overrides={"land_area_m2": 271.0})
    for key in ("noi", "income_value_man"):
        d = res[key]
        assert d["min"] <= d["mid"] <= d["max"], f"{key} range broken: {d}"
    assert res["cost_value_man"] is not None
    assert res["rooms"] >= 1
    print("✅ basic ranges & cost value")


def test_minpaku_180():
    res = profitability.compute(_report(bt=BusinessType.MINPAKU))
    assert res["operating_days_used"] == 180, res["operating_days_used"]
    print("✅ minpaku 180-day clamp")


def test_zero_loan_rate():
    res = profitability.compute(_report(), overrides={"loan_rate": 0.0, "ltv": 0.7})
    # 金利0でも DSCR/CF が算出され例外が出ない
    assert res["financing"]["mid"]["dscr"] is not None
    print("✅ loan_rate=0 no div-by-zero")


def test_room_area_clamp():
    res = profitability.compute(_report(), overrides={"room_area_m2": 3.0})
    assert res["room_area_m2"] >= 7.0, res["room_area_m2"]
    assert any("法令下限" in w for w in res["warnings"])
    print("✅ room area clamped to legal minimum")


def test_missing_floor_area():
    p = ProjectInput(address="東京都新宿区大久保2-1-1", business_type=BusinessType.HOTEL_RYOKAN)
    r = judgment.run(project=p, docs=[])
    res = profitability.compute(r)
    assert res["floor_area_m2"] == 300.0
    assert any("延床面積" in w for w in res["warnings"])
    print("✅ missing floor area fallback + warning")


def test_old_building_remaining_life():
    # 築古（耐用超過）→残存1年・建物価値0・警告
    res = profitability.compute(_report(built=1960))
    assert res["remaining_useful_life_years"] == 1
    assert any("耐用年数" in w for w in res["warnings"])
    print("✅ over-aged building handled")


def test_tsubo_consistency():
    x = 100.0
    assert abs(profitability.m2_to_tsubo(x) * profitability.TSUBO - x) < 1e-9
    print("✅ tsubo conversion consistent")


def test_tier_detection():
    d = profitability.detect_area_tier
    assert d("東京都新宿区大久保2-1-1")["_key"] == "tokyo_central"
    assert d("京都府京都市東山区祇園町")["_key"] == "kyoto_tourist"
    assert d("東京都世田谷区成城6-5-34")["_key"] == "tokyo_23"
    assert d("北海道富良野市1-1")["_key"] == "other"
    print("✅ area tier detection (東京都≠京都)")


if __name__ == "__main__":
    test_basic_ranges()
    test_minpaku_180()
    test_zero_loan_rate()
    test_room_area_clamp()
    test_missing_floor_area()
    test_old_building_remaining_life()
    test_tsubo_consistency()
    test_tier_detection()
    print("\n🎉 全テストパス")
