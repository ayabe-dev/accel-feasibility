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


# ---------------------------------------------------------------------------
# v2026.08.1 ADR/NOI 是正の回帰テスト
# ---------------------------------------------------------------------------


def test_gpi_is_potential_revenue():
    """GPIは満室潜在収入（ADR×室数×日数）で、EGIは稼働率を1回だけ掛ける."""
    nb = profitability.usali_noi(adr_yen=20000, occupancy=0.5, rooms=2, days=100,
                                 assessed_value_man=0, los=1.0,
                                 cleaning_cost_per_stay_man=0.0,
                                 cleaning_fee_per_stay_man=0.0)
    assert abs(nb["gpi"] - 20000 * 2 * 100 / 10000) < 1e-6, nb["gpi"]
    # 清掃収入0なら EGI = GPI × 稼働率（空室率の二重引きがない）
    assert abs(nb["egi"] - nb["gpi"] * 0.5) < 1e-6, (nb["egi"], nb["gpi"])
    assert abs(nb["revpar_yen"] - 20000 * 0.5) < 1e-6
    print("✅ GPI=満室潜在収入 / EGIで稼働率は1回だけ")


def test_cleaning_is_per_stay_not_per_night():
    """清掃回数は「組数」＝稼働室夜÷平均宿泊日数."""
    kw = dict(adr_yen=20000, occupancy=1.0, rooms=1, days=300,
              assessed_value_man=0, cleaning_fee_per_stay_man=0.0)
    one = profitability.usali_noi(los=1.0, **kw)
    three = profitability.usali_noi(los=3.0, **kw)
    assert abs(one["stays"] - 300) < 1e-6, one["stays"]
    assert abs(three["stays"] - 100) < 1e-6, three["stays"]
    # LOSが長いほど清掃回数が減るのでNOIは増える
    assert three["noi"] > one["noi"], (three["noi"], one["noi"])
    print("✅ 清掃は組数ベース（LOSで割る）")


def test_cleaning_fee_is_revenue():
    """ゲスト請求の清掃料金はEGIに乗る."""
    kw = dict(adr_yen=20000, occupancy=0.8, rooms=1, days=365,
              assessed_value_man=0, los=3.0, cleaning_cost_per_stay_man=0.1)
    no_fee = profitability.usali_noi(cleaning_fee_per_stay_man=0.0, **kw)
    fee = profitability.usali_noi(cleaning_fee_per_stay_man=0.1, **kw)
    assert fee["egi"] > no_fee["egi"], (fee["egi"], no_fee["egi"])
    assert abs(fee["cleaning_revenue"] - 0.1 * fee["stays"]) < 1e-6
    print("✅ 清掃料金が収入計上される")


def test_year_fraction_prorates_fixed_costs():
    """month単位の呼び出しで年額固定費が12分割される（12回足すと年額に戻る）."""
    full = profitability.usali_noi(adr_yen=20000, occupancy=0.7, rooms=3, days=365,
                                   assessed_value_man=5000, los=3.0)
    parts = [
        profitability.usali_noi(adr_yen=20000, occupancy=0.7, rooms=3,
                                days=365 * (d / 365), assessed_value_man=5000,
                                los=3.0, year_fraction=d / 365)
        for d in profitability._DAYS_IN_MONTH
    ]
    assert abs(sum(p["fixed"] for p in parts) - full["fixed"]) < 1e-6
    assert abs(sum(p["noi"] for p in parts) - full["noi"]) < 1e-6
    print("✅ year_fractionで固定費を按分（合算すると年額に一致）")


def test_seasonality_normalized():
    facs = profitability.seasonal_factors()
    assert len(facs) == 12
    assert abs(sum(facs) / 12 - 1.0) < 1e-9, sum(facs) / 12
    print("✅ 季節係数は年平均1.0に正規化")


def test_rampup_lowers_first_year():
    """初年度NOI < 安定稼働NOI（立ち上がりを反映）."""
    res = profitability.compute(_report(), overrides={})
    assert res["noi_year1_man"] < res["noi"]["mid"], (res["noi_year1_man"], res["noi"]["mid"])
    # 5年CFの1年目が初年度NOIを使っている
    row1 = res["projection"]["5y"]["rows"][0]
    assert abs(row1["noi"] - res["noi_year1_man"]) < 1.0, (row1["noi"], res["noi_year1_man"])
    # 2年目以降は安定稼働NOI
    row2 = res["projection"]["5y"]["rows"][1]
    assert abs(row2["noi"] - res["noi"]["mid"]) < 1.0, (row2["noi"], res["noi"]["mid"])
    assert len(res["monthly"]["stabilized"]) == 12
    print("✅ 初年度は立ち上がり反映・2年目以降は安定稼働")


def test_capacity_clamped_and_adr_diminishing():
    """定員は上限クランプされ、一棟ADRは定員に対して逓減する."""
    big = profitability.compute(_report(fa=1000.0), overrides={"revenue_unit": "whole"})
    cap_max = int(profitability._CFG["constants"]["capacity_max"])
    assert big["capacity_est"] <= cap_max, big["capacity_est"]
    # 定員2倍でADRは2倍未満（逓減）
    small = profitability.compute(_report(fa=80.0), overrides={"revenue_unit": "whole"})
    large = profitability.compute(_report(fa=160.0), overrides={"revenue_unit": "whole"})
    if large["capacity_est"] > small["capacity_est"]:
        ratio_cap = large["capacity_est"] / small["capacity_est"]
        ratio_adr = large["adr_yen"]["mid"] / small["adr_yen"]["mid"]
        assert ratio_adr < ratio_cap, (ratio_adr, ratio_cap)
    print("✅ 定員クランプ＋一棟ADRの定員逓減")


def test_comps_drive_adr_range():
    """近隣コンプを入れるとADR/稼働がコンプ由来になる."""
    comps = [
        {"name": "A", "adr_yen": 28000, "occupancy": 0.72},
        {"name": "B", "adr_yen": 35000, "occupancy": 0.80},
        {"name": "C", "adr_yen": 31000, "occupancy": 78},   # %表記も受ける
        {"name": "D", "adr_yen": 42000, "occupancy": 0.85},
    ]
    res = profitability.compute(_report(), overrides={"revenue_unit": "whole", "comps": comps})
    mc = res["market_comps"]
    assert mc is not None and mc["comp_count"] == 4, mc
    assert "コンプ" in res["revpar_source"], res["revpar_source"]
    # 中央値が mid
    assert abs(res["adr_yen"]["mid"] - 33000) < 1.0, res["adr_yen"]
    assert res["adr_yen"]["min"] < res["adr_yen"]["mid"] < res["adr_yen"]["max"]
    # 稼働率もコンプ由来（%入力が0.78に正規化されている）
    assert abs(res["assumptions"]["occupancy"]["mid"] - 0.79) < 1e-6, res["assumptions"]["occupancy"]
    print("✅ 近隣コンプがADR・稼働レンジを駆動")


def test_manual_adr_beats_comps():
    """手動ADRはコンプより優先される."""
    comps = [{"adr_yen": 28000, "occupancy": 0.7}, {"adr_yen": 30000, "occupancy": 0.75}]
    res = profitability.compute(_report(), overrides={"adr_yen": 50000, "comps": comps})
    assert abs(res["adr_yen"]["mid"] - 50000) < 1.0, res["adr_yen"]
    assert "手動入力" in res["revpar_source"], res["revpar_source"]
    print("✅ 手動ADR > コンプ の優先順位")


def test_empty_comps_falls_back():
    """コンプが空・不正でも例外にならずエリア相場にフォールバック."""
    for bad in (None, [], [{}], [{"adr_yen": 0}], [{"adr_yen": "abc"}]):
        res = profitability.compute(_report(), overrides={"comps": bad})
        assert res["adr_yen"]["mid"] > 0, (bad, res["adr_yen"])
    print("✅ 不正コンプでもフォールバック")


def test_irr_uses_first_year_noi():
    """出口IRRのキャッシュフローも1年目は初年度NOIを使う（CF表と整合）."""
    res = profitability.compute(_report(), overrides={"purchase_price_man": 9000, "exit_year": 2})
    a = res["assumptions"]
    ex = res["exit"]
    ads = profitability.loan_payment(a["loan_man"], a["loan_rate"], a["loan_term_years"])
    cf_year1 = res["noi_year1_man"] - ads
    cf_stable = res["financing"]["mid"]["pretax_cf"]
    expected = -a["equity_man"] + cf_year1 + cf_stable + ex["sale_net_man"]
    assert abs(ex["total_return_man"] - expected) < 1.5, (ex["total_return_man"], expected)
    # 初年度CFの方が小さい＝立ち上がりが効いている
    assert cf_year1 < cf_stable, (cf_year1, cf_stable)
    print("✅ 出口IRR/トータルリターンも初年度NOIを反映")


if __name__ == "__main__":
    test_basic_ranges()
    test_minpaku_180()
    test_zero_loan_rate()
    test_room_area_clamp()
    test_missing_floor_area()
    test_old_building_remaining_life()
    test_tsubo_consistency()
    test_tier_detection()
    test_gpi_is_potential_revenue()
    test_cleaning_is_per_stay_not_per_night()
    test_cleaning_fee_is_revenue()
    test_year_fraction_prorates_fixed_costs()
    test_seasonality_normalized()
    test_rampup_lowers_first_year()
    test_capacity_clamped_and_adr_diminishing()
    test_comps_drive_adr_range()
    test_manual_adr_beats_comps()
    test_empty_comps_falls_back()
    test_irr_uses_first_year_noi()
    print("\n🎉 全テストパス")
