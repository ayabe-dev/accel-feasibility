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


def cashflow_projection(noi_mid, ads, loan, loan_rate, term, exit_cap,
                        price, equity, years, cg_rate, noi_growth=0.0):
    """N年間の年次キャッシュフロー・累計CF・各年売却時の純利益を試算（税引前・mid）."""
    rows = []
    cumulative = 0.0
    for t in range(1, years + 1):
        noi_t = noi_mid * ((1 + noi_growth) ** (t - 1))
        cf = noi_t - ads
        cumulative += cf
        bal = loan_balance(loan, loan_rate, term, t)
        sale = (noi_t / exit_cap) if exit_cap and exit_cap > 0 else 0.0
        gain = sale - price
        sale_net = sale - bal - max(0.0, gain) * cg_rate
        # その年に売却した場合の累計純利益（運営CF累計 + 売却純手取り − 自己資金）
        net_if_sell = cumulative + sale_net - equity
        rows.append({
            "year": t,
            "noi": round(noi_t, 1),
            "annual_cf": round(cf, 1),
            "cumulative_cf": round(cumulative, 1),
            "loan_balance": round(bal, 1),
            "sale_net": round(sale_net, 1),
            "net_profit_if_sell": round(net_if_sell, 1),
        })
    return {
        "years": years,
        "rows": rows,
        "cumulative_cf": round(cumulative, 1),
        "net_profit_if_sell_end": rows[-1]["net_profit_if_sell"] if rows else 0.0,
        "annual_cf": round(rows[0]["annual_cf"], 1) if rows else 0.0,
    }


def lender_candidates(stru_key, age, dscr_mid, noi_yield_mid, bt):
    """物件プロファイルから候補金融機関タイプと通りやすさ目安を提示（一般論・要確認）."""
    is_solid = stru_key in ("RC", "SRC")
    young = age <= 25
    high_dscr = (dscr_mid or 0) >= 1.3
    def fit(cond_high, cond_mid):
        return "通りやすい" if cond_high else ("可能性あり" if cond_mid else "やや厳しい")
    cands = [
        {
            "type": "メガバンク・都市銀行",
            "fit": fit(is_solid and young and high_dscr, is_solid and high_dscr),
            "rate_hint": "低金利（参考1%前後〜）",
            "term_hint": "残存耐用年数内",
            "rationale": "低金利だが築年・構造・属性の審査が厳しい。RC築浅・高DSCR向き。",
            "caveat": "属性（年収・自己資金・事業実績）依存が大きい。",
        },
        {
            "type": "地方銀行・信用金庫",
            "fit": fit(is_solid, True),
            "rate_hint": "中（参考1〜3%）",
            "term_hint": "耐用年数±",
            "rationale": "エリア・関係性重視。収益物件に比較的対応。アパートローン等。",
            "caveat": "エリア・取引実績で可否が変動。",
        },
        {
            "type": "ノンバンク（オリックス銀行 等）",
            "fit": "通りやすい",
            "rate_hint": "中〜やや高（参考2〜4%）",
            "term_hint": "築古でも年数が出やすい",
            "rationale": "投資用に積極的・条件が柔軟。区分/一棟の収益物件向き。",
            "caveat": "金利は都銀より高め。商品要項は時期で変動。",
        },
        {
            "type": "ノンバンク（セゾンファンデックス 等）",
            "fit": "通りやすい",
            "rate_hint": "高め（参考3.65%前後〜・要確認）",
            "term_hint": "築古・変則物件にも柔軟",
            "rationale": "収益性（利回り）重視。築古・再生・バリューアップ前提に強い。高金利なら多くの物件で融資が付きやすい。",
            "caveat": "金利が高いぶん、その金利でCFが回るか（DSCR・返済比率）を必ず確認。",
        },
    ]
    # 高利回り・築古はノンバンク優位を補足
    note = ("利回りが高い／築古でバリューアップ前提の物件は、属性重視の都銀より"
            "収益性で見るノンバンク系が通りやすい傾向。"
            "ただし金利・LTV・年数は各社・時期で変動するため、確定条件は最新の商品要項・個別審査で要確認。")
    return {"candidates": cands, "note": note}


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

    cr = tier["cap_rate"]
    oc = dict(tier["occupancy"])
    occ_in = o.get("occupancy_input")
    if occ_in:
        oc = {"min": max(0.0, occ_in - 0.10), "mid": occ_in, "max": min(1.0, occ_in + 0.10)}

    # 最大定員の目安（専有面積 ÷ 1人あたり面積）
    cap_per_guest = _CFG["constants"].get("capacity_m2_per_guest", 8.0)
    capacity_est = max(1, int(floor_area / cap_per_guest))

    # 課金モデル：whole=一棟貸し（建物まるごとの1泊単価）／ per_room=客室ごと
    revenue_unit = o.get("revenue_unit") or _CFG.get("default_revenue_unit", "whole")
    adr_in = o.get("adr_yen")          # 手動ADR（whole=一棟/泊、per_room=1室/泊）
    revpar_in = o.get("revpar_override")
    rooms_used = 1 if revenue_unit == "whole" else max(1, rooms)

    # ADR（min/mid/max）を決定 → RevPAR = ADR × 稼働
    if revpar_in:
        # RevPAR直接指定
        rp = {"min": revpar_in * 0.85, "mid": revpar_in, "max": revpar_in * 1.15}
        revpar_source = "RevPAR手動入力"
    elif revenue_unit == "whole":
        if adr_in:
            adr_base = {"min": adr_in * 0.85, "mid": adr_in, "max": adr_in * 1.15}
            revpar_source = "Airbnb手動入力(一棟ADR)"
        else:
            apg = tier["adr_per_guest_yen"]
            adr_base = {k: capacity_est * apg[k] for k in ("min", "mid", "max")}
            revpar_source = f"エリア相場(定員{capacity_est}名×1人単価)"
        rp = {k: adr_base[k] * oc[k] for k in ("min", "mid", "max")}
    else:  # per_room
        if adr_in:
            adr_base = {"min": adr_in * 0.85, "mid": adr_in, "max": adr_in * 1.15}
            rp = {k: adr_base[k] * oc[k] for k in ("min", "mid", "max")}
            revpar_source = "Airbnb手動入力(1室ADR)"
        else:
            rp = dict(tier["revpar_yen"])  # tierのRevPARは1室前提
            revpar_source = "エリア相場(客室ごと)"

    cap_mid_override = o.get("cap_rate_mid")
    cap = {"min": cr["max"], "mid": cap_mid_override or cr["mid"], "max": cr["min"]}
    scen = {
        "min": dict(revpar=rp["min"], occ=oc["min"], cap=cr["max"]),
        "mid": dict(revpar=rp["mid"], occ=oc["mid"], cap=(cap_mid_override or cr["mid"])),
        "max": dict(revpar=rp["max"], occ=oc["max"], cap=cr["min"]),
    }

    noi_breakdown = {}
    noi = {}
    income_value = {}
    for k, s in scen.items():
        nb = usali_noi(s["revpar"], s["occ"], rooms_used, days, assessed)
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

    # 相場（適正価格）vs 販売価格 の割安/割高判定（収益価格[A]と原価法[B]の両方を使用）
    a_min, a_mid, a_max = income_value["min"], income_value["mid"], income_value["max"]
    if cost_value is not None:
        est_low = min(a_min, cost_value)
        est_high = max(a_max, cost_value)
        est_mid = (a_mid + cost_value) / 2.0
    else:
        est_low, est_mid, est_high = a_min, a_mid, a_max
    asking = o.get("purchase_price_man")
    valuation = {
        "market_value_man": {"min": round(est_low, 1), "mid": round(est_mid, 1), "max": round(est_high, 1)},
        "income_value_mid_man": round(a_mid, 1),
        "cost_value_man": round(cost_value, 1) if cost_value is not None else None,
        "asking_price_man": round(asking, 1) if asking else None,
        "price_verdict": None,
        "gap_pct": None,
        "basis": "収益価格[A]と原価法[B]の両方（土地未入力時はAのみ）",
    }
    if asking:
        gap = asking / est_mid - 1 if est_mid > 0 else None
        if asking < est_low:
            verdict = "割安（相場下限より安い）"
        elif asking > est_high:
            verdict = "割高（相場上限より高い）"
        else:
            verdict = "適正レンジ内"
        valuation["price_verdict"] = verdict
        valuation["gap_pct"] = round(gap * 100, 1) if gap is not None else None
        if land_area:
            valuation["asking_land_per_tsubo_man"] = round(asking / m2_to_tsubo(land_area), 1)
            valuation["tier_land_per_tsubo_man"] = land_tier

    # ---- NOI目標から逆算：払っていい適正価格（プロの5ステップ思考） ----
    target_yield = o.get("target_noi_yield", _CFG.get("works_defaults", {}).get("target_noi_yield", 0.15))
    # 初期費用（旅館化リノベ＋消防設備＋用途変更/許可）＝面積スケール概算
    wd = _CFG.get("works_defaults", {})
    reno_per_tsubo = wd.get("reno_per_tsubo_man", 45)
    fire_permit = wd.get("fire_permit_fixed_man", 300)
    rr = wd.get("range_ratio", 0.35)
    works_mid = reno_per_tsubo * m2_to_tsubo(floor_area) + fire_permit
    works = {"min": works_mid * (1 - rr), "mid": works_mid, "max": works_mid * (1 + rr)}
    works_src = f"面積スケール概算(リノベ{reno_per_tsubo}万/坪×{m2_to_tsubo(floor_area):.0f}坪＋消防許可{fire_permit}万)"
    works_over = o.get("initial_works_man")
    if works_over:
        works = {"min": works_over, "mid": works_over, "max": works_over}
        works_src = "手入力"
    acq_rate2 = o.get("acquisition_cost_rate", fin["acquisition_cost_rate"])

    def _budget_cap(noi_v):
        return (noi_v / target_yield) if target_yield > 0 else 0.0

    def _fair(noi_v, works_v):
        cap = _budget_cap(noi_v)
        # 適正物件価格 = (総投資上限 − 初期工事費) ÷ (1 + 取得諸経費率)
        return max(0.0, (cap - works_v) / (1 + acq_rate2))

    noi_r = {"min": noi["min"], "mid": noi["mid"], "max": noi["max"]}
    fair = {
        "min": _fair(noi_r["min"], works["max"]),   # 保守：低NOI×高工事
        "mid": _fair(noi_r["mid"], works["mid"]),
        "max": _fair(noi_r["max"], works["min"]),   # 強気：高NOI×低工事
    }
    asking2 = o.get("purchase_price_man")
    back_verdict = None
    discount = None
    if asking2:
        if asking2 <= fair["mid"]:
            back_verdict = "割安（目標NOIを満たす）" if asking2 <= fair["min"] else "ほぼ適正（mid以下）"
        else:
            back_verdict = "割高（このままでは目標NOI未達）"
            discount = round(asking2 - fair["mid"], 1)  # 必要な指値額（mid基準）
    backward = {
        "target_noi_yield": target_yield,
        "gpi_mid_man": round(noi_breakdown["mid"]["gpi"], 1),
        "noi_mid_man": round(noi["mid"], 1),
        "noi_quick50_mid_man": round(noi_breakdown["mid"]["egi"] * 0.5, 1),
        "budget_cap_man": {k: round(_budget_cap(noi_r[k]), 1) for k in ("min", "mid", "max")},
        "initial_works_man": {k: round(works[k], 1) for k in ("min", "mid", "max")},
        "initial_works_source": works_src,
        "acq_cost_rate": acq_rate2,
        "fair_price_man": {k: round(fair[k], 1) for k in ("min", "mid", "max")},
        "asking_price_man": round(asking2, 1) if asking2 else None,
        "verdict": back_verdict,
        "suggested_discount_man": discount,
    }

    # 複数年キャッシュフロー（5年・10年）
    noi_mid_val = noi_breakdown["mid"]["noi"]
    proj5 = cashflow_projection(noi_mid_val, ads, loan, loan_rate, term,
                                scen["mid"]["cap"], price, equity, 5, cg_rate)
    proj10 = cashflow_projection(noi_mid_val, ads, loan, loan_rate, term,
                                 scen["mid"]["cap"], price, equity, 10, cg_rate)
    # 銀行マッチング
    age_for_bank = age
    lenders = lender_candidates(stru["_key"], age_for_bank,
                                financing["mid"]["dscr"], financing["mid"]["noi_yield"], bt)

    def rng(d):  # min<=mid<=max を保証
        vals = sorted([d["min"], d["mid"], d["max"]])
        return {"min": round(vals[0], 1), "mid": round(d["mid"], 1), "max": round(vals[2], 1)}

    return {
        "is_estimate": True,
        "disclaimer": "本結果は前提明示型の試算（estimate）であり、鑑定評価・融資審査の代替ではありません。",
        "config_version": _CFG.get("version"),
        "as_of": _CFG.get("as_of"),
        "area_tier": tier["label"],
        "revpar_source": revpar_source,
        "valuation": valuation,
        "backward": backward,
        "structure": stru["_key"],
        "business_type": bt.value,
        "operating_days_used": days,
        "rooms": rooms,
        "rooms_used_for_revenue": rooms_used,
        "revenue_unit": revenue_unit,
        "capacity_est": capacity_est,
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
        "projection": {"5y": proj5, "10y": proj10},
        "lenders": lenders,
    }
