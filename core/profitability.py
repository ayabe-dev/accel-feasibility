"""収益性分析エンジン（前提明示型の試算 / estimate）.

USALI階層NOI、Inwood有期還元/簡易DCF、CF・DSCR・返済比率(分母EGI)、簡易IRR、
tornado、レンジ(min/mid/max)を算出する。鑑定評価・融資審査の代替ではない。

compute(report, overrides) -> dict を公開。app.py から呼び出す。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import BusinessType, FeasibilityReport

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "revenue_estimates.yaml"


def _load() -> Dict[str, Any]:
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


_CFG = _load()
TSUBO = _CFG["constants"]["tsubo_m2"]  # 3.30578


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def m2_to_tsubo(x: float) -> float:
    return x / TSUBO


def detect_area_tier(address: str) -> Dict[str, Any]:
    """住所キーワードからエリアティアを判定."""
    addr = address or ""
    tiers = _CFG["area_tiers"]
    is_tokyo = ("東京" in addr) or ("東京都" in addr)
    # 優先順位：都心 > 京都(東京以外) > 23区 > その他
    for kw in tiers["tokyo_central"]["keywords"]:
        if kw in addr:
            return {**tiers["tokyo_central"], "_key": "tokyo_central"}
    if not is_tokyo:  # 「東京都」が「京都」に誤マッチするのを防ぐ
        for kw in tiers["kyoto_tourist"]["keywords"]:
            if kw in addr:
                return {**tiers["kyoto_tourist"], "_key": "kyoto_tourist"}
    # 23区判定（「区」を含み東京/都内らしい）
    if "区" in addr and is_tokyo:
        return {**tiers["tokyo_23"], "_key": "tokyo_23"}
    if "区" in addr:
        return {**tiers["tokyo_23"], "_key": "tokyo_23"}
    return {**tiers["other"], "_key": "other"}


def detect_structure(structure: Optional[str]) -> Dict[str, Any]:
    s = structure or ""
    st = _CFG["structure"]
    if "SRC" in s or "鉄骨鉄筋" in s:
        return {**st["SRC"], "_key": "SRC"}
    if "RC" in s or "鉄筋" in s:
        return {**st["RC"], "_key": "RC"}
    if "鉄骨" in s or "S造" in s:
        return {**st["S"], "_key": "S"}
    if "木" in s or "W造" in s:
        return {**st["WOOD"], "_key": "WOOD"}
    return {**st["RC"], "_key": "RC"}  # 不明はRC想定


def operating_days_for(bt: BusinessType, requested: Optional[int]) -> int:
    cap = _CFG["operating_days_cap"].get(bt.value, 365)
    req = requested if requested else 365
    return int(min(req, cap))


def loan_payment(principal_man: float, annual_rate: float, years: int) -> float:
    """年間元利返済額（万円）。金利0は元金均等で安全に算出（ゼロ除算回避）."""
    if years is None or years <= 0 or principal_man <= 0:
        return 0.0
    n = years * 12
    r = annual_rate / 12.0
    if r <= 0:
        monthly = principal_man / n
    else:
        monthly = principal_man * r * (1 + r) ** n / ((1 + r) ** n - 1)
    return monthly * 12.0


def loan_balance(principal_man: float, annual_rate: float, years: int, after_years: int) -> float:
    """after_years 経過後のローン残高（万円・元利均等）."""
    if principal_man <= 0 or years is None or years <= 0:
        return 0.0
    if after_years >= years:
        return 0.0
    n = years * 12
    k = after_years * 12
    r = annual_rate / 12.0
    if r <= 0:
        return principal_man * (1 - k / n)
    bal = principal_man * ((1 + r) ** n - (1 + r) ** k) / ((1 + r) ** n - 1)
    return max(0.0, bal)


def irr_bisection(cashflows: List[float]) -> Optional[float]:
    """簡易IRR（二分法）。numpy非依存。"""
    if not cashflows or all(c >= 0 for c in cashflows) or all(c <= 0 for c in cashflows):
        return None

    def npv(rate: float) -> float:
        return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))

    lo, hi = -0.9, 1.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-6:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def inwood_value(noi_man: float, cap_rate: float, years: int) -> float:
    """Inwood有期還元（万円）。cap<=0や年数<=0はガード."""
    if noi_man <= 0:
        return 0.0
    if cap_rate <= 0 or years is None or years <= 0:
        return 0.0
    factor = (1 - (1 + cap_rate) ** (-years)) / cap_rate
    return noi_man * factor


# ---------------------------------------------------------------------------
# USALI NOI（1シナリオ）
# ---------------------------------------------------------------------------

def usali_noi(revpar_yen: float, occupancy: float, rooms: int, days: int,
              assessed_value_man: float) -> Dict[str, float]:
    """USALI階層でNOIを算出（万円）."""
    u = _CFG["usali"]
    vac = _CFG["constants"]["vacancy_allowance"]
    gpi_yen = revpar_yen * rooms * days
    gpi = gpi_yen / 10000.0                      # 万円
    egi = gpi * (1 - vac)
    stays = rooms * days * _clamp(occupancy, 0, 1)
    # 変動費
    variable = (
        egi * u["ota_commission_rate"]
        + u["cleaning_cost_per_stay_man"] * stays
        + egi * u["linen_rate"]
        + egi * u["utility_variable_rate"]
    )
    gop_pre = egi - variable
    # 固定費
    fixed = (
        u["labor_cost_per_room_man_year"] * rooms
        + egi * u["mgmt_fee_rate"]
        + egi * u["insurance_rate_of_revenue"]
        + assessed_value_man * u["property_tax_rate_of_value"]
        + u["utility_base_per_room_man_year"] * rooms
    )
    gop = gop_pre - fixed
    ffe = egi * u["ffe_reserve_rate"]
    noi = gop - ffe
    return {
        "gpi": gpi, "egi": egi, "variable": variable, "gop_pre": gop_pre,
        "fixed": fixed, "gop": gop, "ffe": ffe, "noi": noi, "rooms": rooms,
    }


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def compute(report: FeasibilityReport, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    o = overrides or {}
    warnings: List[str] = []
    inp = report.input
    geo = report.geo

    tier = detect_area_tier(inp.address)
    stru = detect_structure(inp.structure)

    # 延床
    floor_area = o.get("floor_area_m2") or inp.floor_area_m2
    if not floor_area or floor_area <= 0:
        floor_area = 300.0
        warnings.append("延床面積が未取得のため 300㎡ と仮置きしました（要入力）。")

    # 築年→残存耐用年数
    built = o.get("built_year") or inp.built_year
    age = 0
    if built:
        age = max(0, 2026 - int(built))
    useful = stru["useful_life_years"]
    remaining = useful - age
    if remaining <= 0:
        remaining = 1
        warnings.append("法定耐用年数を超過。建物価値ゼロ・残存1年として収益還元します（要精査）。")

    # 客室数
    room_area = o.get("room_area_m2") or _CFG["constants"]["default_room_area_m2"]
    # 法令下限クランプ
    min_room = 9.0 if inp.business_type == BusinessType.HOTEL_RYOKAN else 7.0
    if room_area < min_room:
        warnings.append(f"1室面積{room_area}㎡は法令下限{min_room}㎡未満のため下限に丸めました。")
        room_area = min_room
    eff_ratio = _CFG["constants"]["effective_room_ratio"]
    effective_floor = floor_area * eff_ratio
    rooms = o.get("room_count") or int(effective_floor // room_area)
    if rooms < 1:
        rooms = 0
        warnings.append("客室数が0室。延床または1室面積を確認してください。")

    bt = inp.business_type
    days = operating_days_for(bt, o.get("operating_days"))
    if bt == BusinessType.MINPAKU:
        warnings.append("民泊（住宅宿泊事業）は年間営業180日上限でクランプしています。")

    # 原価法ベースの査定額（固都税・原価法価格用、循環回避）
    build_recost = stru["build_cost_per_tsubo_man"] * m2_to_tsubo(floor_area)
    depreciation = build_recost * _clamp(age / useful, 0, 1)
    building_value = build_recost - depreciation
    land_area = o.get("land_area_m2")
    land_tier = tier["land_price_per_tsubo_man"]
    land_value_mid = (land_tier["mid"] * m2_to_tsubo(land_area)) if land_area else 0.0
    cost_value = (building_value + land_value_mid) if land_area else None
    assessed = building_value + land_value_mid  # 固都税ベース（土地未入力なら建物のみ）

    rp = tier["revpar_yen"]
    oc = tier["occupancy"]
    cr = tier["cap_rate"]
    cap_mid_override = o.get("cap_rate_mid")
    cap = {"min": cr["max"], "mid": cap_mid_override or cr["mid"], "max": cr["min"]}
    # value: min=保守(低revpar/高cap), max=強気
    scen = {
        "min": dict(revpar=rp["min"], occ=oc["min"], cap=cr["max"]),
        "mid": dict(revpar=rp["mid"], occ=oc["mid"], cap=(cap_mid_override or cr["mid"])),
        "max": dict(revpar=rp["max"], occ=oc["max"], cap=cr["min"]),
    }

    noi_breakdown = {}
    noi = {}
    income_value = {}
    for k, s in scen.items():
        nb = usali_noi(s["revpar"], s["occ"], rooms, days, assessed)
        noi_breakdown[k] = nb
        noi[k] = nb["noi"]
        income_value[k] = inwood_value(nb["noi"], s["cap"], remaining)

    # 想定取得価格：override or 収益価格(mid)
    fin = _CFG["finance"]
    price = o.get("purchase_price_man") or income_value["mid"]
    if price <= 0:
        price = max(income_value.values()) or 1.0
    acq_rate = o.get("acquisition_cost_rate", fin["acquisition_cost_rate"])
    total_investment = price * (1 + acq_rate)
    ltv = o.get("ltv", fin["ltv"])
    loan = o.get("loan_amount_man") or price * ltv
    equity = max(1.0, total_investment - loan)
    loan_rate = o.get("loan_rate", fin["loan_rate"])
    term = o.get("loan_term_years", fin["loan_term_years"])
    ads = loan_payment(loan, loan_rate, term)

    def fin_metrics(k):
        nb = noi_breakdown[k]
        egi = nb["egi"]; n = nb["noi"]; gpi = nb["gpi"]
        dscr = (n / ads) if ads > 0 else None
        rr = (ads / egi) if egi > 0 else None
        cf = n - ads
        payback = (equity / cf) if cf > 0 else None
        return {
            "dscr": dscr, "repayment_ratio": rr, "pretax_cf": cf,
            "payback_years": payback,
            "gross_yield": (gpi / price) if price > 0 else None,
            "noi_yield": (n / price) if price > 0 else None,
        }

    financing = {k: fin_metrics(k) for k in scen}

    # 良物件判定（mid・税引前）
    m = financing["mid"]
    if m["dscr"] is None or m["pretax_cf"] is None:
        verdict = "判定不能"
    elif m["dscr"] >= 1.2 and (m["repayment_ratio"] or 1) <= 0.5 and m["pretax_cf"] > 0:
        verdict = "GOOD（回りやすい）"
    elif m["dscr"] >= 1.0 and m["pretax_cf"] > 0:
        verdict = "条件付き"
    else:
        verdict = "NG（現状CF赤字/DSCR<1）"

    # IRR/出口（mid）
    exit_year = int(o.get("exit_year", 10))
    cg_rate = o.get("capital_gains_tax_rate", 0.0)
    exit_cap = o.get("exit_cap_rate", scen["mid"]["cap"])
    noi_mid = noi_breakdown["mid"]["noi"]
    cf_mid = financing["mid"]["pretax_cf"]
    sale_price = (noi_mid / exit_cap) if exit_cap > 0 else 0.0
    bal = loan_balance(loan, loan_rate, term, exit_year)
    gain = sale_price - price
    sale_net = sale_price - bal - max(0.0, gain) * cg_rate
    cfs = [-equity] + [cf_mid] * (exit_year - 1) + [cf_mid + sale_net]
    irr = irr_bisection(cfs)
    total_return = sum(cfs)

    # tornado（mid基準、year1 税引前CFへの影響）
    base_cf = cf_mid
    tornado = []
    # cap rate（価格→ローン経由でCFに効く、ここでは収益価格への影響を見る）
    tornado.append({"driver": "cap rate", "low_label": "強気(低)", "high_label": "保守(高)",
                    "low": income_value["max"], "high": income_value["min"], "base": income_value["mid"],
                    "metric": "収益価格(万円)"})
    # 稼働率/RevPAR → NOI
    tornado.append({"driver": "稼働率/RevPAR", "low_label": "弱気", "high_label": "強気",
                    "low": noi["min"], "high": noi["max"], "base": noi["mid"],
                    "metric": "NOI(万円)"})
    # 運営費（FF&E+OTA ±5pt 相当）→ NOI(mid)を±で近似
    egi_mid = noi_breakdown["mid"]["egi"]
    tornado.append({"driver": "運営費(±5pt)", "low_label": "低", "high_label": "高",
                    "low": noi["mid"] + egi_mid * 0.05, "high": noi["mid"] - egi_mid * 0.05,
                    "base": noi["mid"], "metric": "NOI(万円)"})
    # 金利 ±1pt → 年間CF
    ads_low = loan_payment(loan, max(0, loan_rate - 0.01), term)
    ads_high = loan_payment(loan, loan_rate + 0.01, term)
    tornado.append({"driver": "金利(±1pt)", "low_label": "低金利", "high_label": "高金利",
                    "low": noi_mid - ads_low, "high": noi_mid - ads_high, "base": base_cf,
                    "metric": "年間CF(万円)"})

    # 理論最大床（参考・要鑑定）
    theoretical = None
    if land_area and geo.floor_area_ratio_pct:
        eff_far = geo.floor_area_ratio_pct / 100.0
        max_floor = land_area * eff_far
        consumption = floor_area / land_area
        theoretical = {
            "land_area_m2": land_area,
            "designated_far_pct": geo.floor_area_ratio_pct,
            "current_consumption_pct": round(consumption * 100, 1),
            "theoretical_max_floor_m2": round(max_floor, 1),
            "headroom_x": round(eff_far / consumption, 2) if consumption > 0 else None,
            "note": "参考値・要鑑定。斜線/日影/高度地区/天空率/前面道路制限・地階の扱いは未考慮。",
        }

    def rng(d):  # min<=mid<=max を保証
        vals = sorted([d["min"], d["mid"], d["max"]])
        return {"min": round(vals[0], 1), "mid": round(d["mid"], 1), "max": round(vals[2], 1)}

    return {
        "is_estimate": True,
        "disclaimer": "本結果は前提明示型の試算（estimate）であり、鑑定評価・融資審査の代替ではありません。",
        "config_version": _CFG.get("version"),
        "as_of": _CFG.get("as_of"),
        "area_tier": tier["label"],
        "structure": stru["_key"],
        "business_type": bt.value,
        "operating_days_used": days,
        "rooms": rooms,
        "room_area_m2": room_area,
        "floor_area_m2": floor_area,
        "remaining_useful_life_years": remaining,
        "assumptions": {
            "revpar_yen": rp, "occupancy": oc, "cap_rate": cap,
            "price_assumption_man": round(price, 1),
            "price_is_override": bool(o.get("purchase_price_man")),
            "ltv": ltv, "loan_man": round(loan, 1), "equity_man": round(equity, 1),
            "loan_rate": loan_rate, "loan_term_years": term,
            "land_area_m2": land_area,
        },
        "noi": rng(noi),
        "income_value_man": rng(income_value),
        "cost_value_man": round(cost_value, 1) if cost_value is not None else None,
        "noi_breakdown_mid": {k: round(v, 1) for k, v in noi_breakdown["mid"].items()},
        "financing": financing,
        "verdict": verdict,
        "exit": {
            "exit_year": exit_year, "exit_cap_rate": exit_cap,
            "sale_price_man": round(sale_price, 1), "loan_balance_man": round(bal, 1),
            "sale_net_man": round(sale_net, 1),
            "simple_irr": round(irr, 4) if irr is not None else None,
            "total_return_man": round(total_return, 1),
        },
        "tornado": tornado,
        "theoretical_max_floor": theoretical,
        "warnings": warnings,
    }
