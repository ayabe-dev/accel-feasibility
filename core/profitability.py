"""収益性分析エンジン（前提明示型の試算 / estimate）.

USALI階層NOI、Inwood有期還元/簡易DCF、CF・DSCR・返済比率(分母EGI)、簡易IRR、
tornado、レンジ(min/mid/max)を算出する。鑑定評価・融資審査の代替ではない。

compute(report, overrides) -> dict を公開。app.py から呼び出す。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from . import market_data, municipality
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
                        price, equity, years, cg_rate, noi_growth=0.0,
                        noi_year1=None):
    """N年間の年次キャッシュフロー・累計CF・各年売却時の純利益を試算（税引前・mid）.

    `noi_year1` を渡すと1年目だけ立ち上がり（ramp-up）後のNOIを使う。
    売却価格の還元には常に安定稼働NOIを使う（初年度の立ち上がりで資産価値を
    割り引くのは不適切なため）。
    """
    rows = []
    cumulative = 0.0
    for t in range(1, years + 1):
        stabilized_t = noi_mid * ((1 + noi_growth) ** (t - 1))
        noi_t = noi_year1 if (t == 1 and noi_year1 is not None) else stabilized_t
        cf = noi_t - ads
        cumulative += cf
        bal = loan_balance(loan, loan_rate, term, t)
        sale = (stabilized_t / exit_cap) if exit_cap and exit_cap > 0 else 0.0
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

def usali_noi(adr_yen: float, occupancy: float, rooms: int, days: float,
              assessed_value_man: float, los: Optional[float] = None,
              year_fraction: float = 1.0,
              cleaning_cost_per_stay_man: Optional[float] = None,
              cleaning_fee_per_stay_man: Optional[float] = None) -> Dict[str, float]:
    """USALI階層でNOIを算出（万円）.

    v2026.08.1 での是正点:
      1. GPI の定義を「満室時の潜在収入（ADR×室数×営業日数）」に統一し、
         EGI = GPI × 稼働率 とした。旧実装は GPI に RevPAR（＝ADR×稼働）を
         使ったうえで空室率3%を重ねており、空室を二重に引いていた。
      2. 清掃回数を「稼働室夜」ではなく「組数（稼働室夜÷平均宿泊日数）」にした。
         旧実装は1泊ごとに清掃費が発生する計算で、LOS=3泊なら清掃費が約3倍。
      3. ゲストに請求する清掃料金を収入として計上（旧実装は費用のみで収入なし）。
      4. `year_fraction` で年額の固定費（人件費・基本光熱費・固都税）を按分し、
         月次や部分期間の計算に使えるようにした。
    """
    u = _CFG["usali"]
    c = _CFG["constants"]
    occ = _clamp(occupancy, 0.0, 1.0)
    los_v = max(1.0, float(los if los else c.get("avg_length_of_stay_nights", 3.0)))
    yf = max(0.0, float(year_fraction))
    clean_cost = (u["cleaning_cost_per_stay_man"] if cleaning_cost_per_stay_man is None
                  else float(cleaning_cost_per_stay_man))
    clean_fee = (u.get("cleaning_fee_per_stay_man", 0.0) if cleaning_fee_per_stay_man is None
                 else float(cleaning_fee_per_stay_man))

    # --- 収入 ---
    # GPI（満室時潜在収入）＝ ADR × 室数 × 営業日数
    gpi = adr_yen * rooms * days / 10000.0
    # 客室収入 ＝ GPI × 稼働率（＝ RevPAR × 室数 × 営業日数）
    room_revenue = gpi * occ
    occupied_room_nights = rooms * days * occ
    stays = (occupied_room_nights / los_v) if los_v > 0 else 0.0
    cleaning_revenue = clean_fee * stays
    egi = room_revenue + cleaning_revenue

    # --- 変動費 ---
    variable = (
        egi * u["ota_commission_rate"]                    # OTA手数料はゲスト支払総額に対して
        + clean_cost * stays                              # 清掃原価は「組数」に対して
        + egi * u["linen_rate"]
        + egi * u["utility_variable_rate"]
    )
    gop_pre = egi - variable

    # --- 固定費（年額項目は year_fraction で按分） ---
    fixed = (
        u["labor_cost_per_room_man_year"] * rooms * yf
        + egi * u["mgmt_fee_rate"]
        + egi * u["insurance_rate_of_revenue"]
        + assessed_value_man * u["property_tax_rate_of_value"] * yf
        + u["utility_base_per_room_man_year"] * rooms * yf
    )
    gop = gop_pre - fixed
    ffe = egi * u["ffe_reserve_rate"]
    noi = gop - ffe
    return {
        "gpi": gpi, "egi": egi, "variable": variable, "gop_pre": gop_pre,
        "fixed": fixed, "gop": gop, "ffe": ffe, "noi": noi, "rooms": rooms,
        # 内訳の可視化用
        "adr_yen": adr_yen, "revpar_yen": adr_yen * occ, "occupancy": occ,
        "room_revenue": room_revenue, "cleaning_revenue": cleaning_revenue,
        "stays": stays, "occupied_room_nights": occupied_room_nights,
        "avg_length_of_stay": los_v, "days": days,
        "cleaning_cost_per_stay_man": clean_cost,
        "cleaning_fee_per_stay_man": clean_fee,
    }


# ---------------------------------------------------------------------------
# 季節性・立ち上がり（月次）
# ---------------------------------------------------------------------------

_DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MONTH_LABELS = ["1月", "2月", "3月", "4月", "5月", "6月",
                 "7月", "8月", "9月", "10月", "11月", "12月"]


def seasonal_factors() -> List[float]:
    """月別稼働係数を年平均1.0に正規化して返す."""
    prof = _CFG.get("seasonal_profile") or []
    if len(prof) != 12:
        return [1.0] * 12
    vals = [float(p) for p in prof]
    mean = sum(vals) / 12.0
    if mean <= 0:
        return [1.0] * 12
    return [v / mean for v in vals]


def rampup_factors() -> List[float]:
    """初年度の立ち上がり係数（12ヶ月）。start_ratio から 1.0 へ線形回復."""
    r = _CFG.get("rampup") or {}
    if not r.get("enabled", False):
        return [1.0] * 12
    months = int(_clamp(float(r.get("months", 6)), 1, 12))
    start = _clamp(float(r.get("start_ratio", 0.4)), 0.0, 1.0)
    out: List[float] = []
    for i in range(12):
        if i >= months:
            out.append(1.0)
        else:
            out.append(start + (1.0 - start) * (i / months))
    return out


def monthly_profile(adr_yen: float, occupancy: float, rooms: int, days_year: float,
                    assessed_value_man: float, los: Optional[float] = None,
                    ramp: Optional[List[float]] = None,
                    cleaning_cost_per_stay_man: Optional[float] = None,
                    cleaning_fee_per_stay_man: Optional[float] = None) -> List[Dict[str, float]]:
    """月次の稼働・売上・NOIを算出（季節性・任意で立ち上がりを反映）."""
    facs = seasonal_factors()
    total_days = float(sum(_DAYS_IN_MONTH))
    rows: List[Dict[str, float]] = []
    for i in range(12):
        frac = _DAYS_IN_MONTH[i] / total_days
        days_m = days_year * frac
        occ_m = _clamp(occupancy * facs[i] * (ramp[i] if ramp else 1.0), 0.0, 1.0)
        nb = usali_noi(adr_yen, occ_m, rooms, days_m, assessed_value_man,
                       los=los, year_fraction=frac,
                       cleaning_cost_per_stay_man=cleaning_cost_per_stay_man,
                       cleaning_fee_per_stay_man=cleaning_fee_per_stay_man)
        rows.append({
            "month": _MONTH_LABELS[i],
            "seasonal_factor": round(facs[i], 3),
            "ramp_factor": round(ramp[i], 3) if ramp else 1.0,
            "occupancy": round(occ_m, 4),
            "revenue": round(nb["egi"], 1),
            "noi": round(nb["noi"], 1),
        })
    return rows


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

    # 最大定員の目安（専有面積 ÷ 1人あたり面積）。実務上の上限でクランプする。
    cap_per_guest = _CFG["constants"].get("capacity_m2_per_guest", 8.0)
    cap_max = int(_CFG["constants"].get("capacity_max", 16))
    capacity_raw = max(1, int(floor_area / cap_per_guest))
    capacity_practical = min(capacity_raw, cap_max)

    # 自治体条例の定員上限（有効面積○㎡につき1人）。実データがある自治体のみ適用。
    muni_key = municipality.detect_municipality(inp.address)
    muni_rule = municipality.get_municipality_rule(muni_key)
    muni_cap = muni_rule.get("capacity") or {}
    capacity_legal_max = None
    if muni_cap:
        if bt == BusinessType.SIMPLE_LODGING:
            per_guest_legal = muni_cap.get("simple_lodging_m2_per_guest")
        else:
            per_guest_legal = muni_cap.get("hotel_ryokan_m2_per_guest")
        if per_guest_legal:
            effective_area = floor_area * eff_ratio
            capacity_legal_max = max(1, int(effective_area / float(per_guest_legal)))

    # 収益計算に使う定員は「実務目安」と「法令上限」の小さい方（法令超過を防ぐ）
    capacity_est = capacity_practical
    capacity_basis = f"面積÷{cap_per_guest}㎡/人（実務目安）"
    if capacity_legal_max is not None and capacity_legal_max < capacity_practical:
        capacity_est = capacity_legal_max
        capacity_basis = (
            f"{muni_rule.get('name', muni_key)}条例の法令上限"
            f"（有効面積÷{per_guest_legal}㎡/人）"
        )
        warnings.append(
            f"{muni_rule.get('name', muni_key)}条例の定員上限{capacity_legal_max}名が"
            f"実務目安{capacity_practical}名より小さいため、法令上限を採用しました。"
        )
    elif capacity_raw > cap_max:
        warnings.append(
            f"面積からの定員目安{capacity_raw}名を実務上限{cap_max}名にクランプしました"
            "（消防・旅館業の実務上、面積に比例して定員を増やし続けることはできません）。"
        )
    if capacity_legal_max is not None and capacity_legal_max > capacity_est:
        warnings.append(
            f"参考：{muni_rule.get('name', muni_key)}条例上の定員上限は約{capacity_legal_max}名"
            f"（有効面積{floor_area * eff_ratio:.0f}㎡÷{per_guest_legal}㎡/人）ですが、"
            f"収益試算は実務目安の{capacity_est}名で計算しています"
            "（寝具・便所数・消防設備が先に頭打ちになるため）。"
        )

    # 課金モデル：whole=一棟貸し（建物まるごとの1泊単価）／ per_room=客室ごと
    revenue_unit = o.get("revenue_unit") or _CFG.get("default_revenue_unit", "whole")
    adr_in = o.get("adr_yen")          # 手動ADR（whole=一棟/泊、per_room=1室/泊）
    revpar_in = o.get("revpar_override")
    rooms_used = 1 if revenue_unit == "whole" else max(1, rooms)
    los = o.get("avg_length_of_stay") or _CFG["constants"].get("avg_length_of_stay_nights", 3.0)

    # 清掃単価（1組あたり）：課金モデル別の既定 → overridesで上書き可
    _cbu = (_CFG["usali"].get("cleaning_by_unit") or {}).get(revenue_unit, {})
    clean_cost_man = o.get("cleaning_cost_man")
    if clean_cost_man is None:
        clean_cost_man = _cbu.get("cost_per_stay_man", _CFG["usali"]["cleaning_cost_per_stay_man"])
    clean_fee_man = o.get("cleaning_fee_man")
    if clean_fee_man is None:
        clean_fee_man = _cbu.get("fee_per_stay_man", _CFG["usali"].get("cleaning_fee_per_stay_man", 0.0))

    # 近隣コンプ（手入力）→ 相場サマリ。将来はここをAirDNA等のアダプタに差し替える。
    comps_summary = market_data.summarize_comps(o.get("comps"))
    if comps_summary and comps_summary.get("occupancy") and not occ_in:
        co = comps_summary["occupancy"]
        oc = {k: _clamp(co[k], 0.0, 1.0) for k in ("min", "mid", "max")}

    # 一棟貸しADRの定員逓減指数（1.0＝定員に線形。既定0.85＝逓減）
    cap_exp = float(_CFG["constants"].get("capacity_adr_exponent", 0.85))

    def _tier_adr():
        """エリア相場からADRレンジ（min/mid/max）と出典ラベルを作る."""
        if revenue_unit == "whole":
            apg = tier["adr_per_guest_yen"]
            scale = capacity_est ** cap_exp
            return ({k: apg[k] * scale for k in ("min", "mid", "max")},
                    f"エリア相場（1人単価 × 定員{capacity_est}名^{cap_exp}＝逓減考慮）")
        # per_room：tierのRevPARは1室前提なので、稼働で割り戻してADRにする
        rpv = tier["revpar_yen"]
        return ({k: (rpv[k] / oc[k] if oc[k] > 0 else rpv[k]) for k in ("min", "mid", "max")},
                "エリア相場（客室ごと・RevPAR ÷ 稼働率）")

    # ADR（min/mid/max）を決定 → RevPAR = ADR × 稼働
    # 優先順位：RevPAR直接指定 ＞ 手動ADR ＞ 近隣コンプ ＞ エリア相場
    if revpar_in:
        rp = {"min": revpar_in * 0.85, "mid": revpar_in, "max": revpar_in * 1.15}
        adr_base = {k: (rp[k] / oc[k] if oc[k] > 0 else rp[k]) for k in ("min", "mid", "max")}
        revpar_source = "RevPAR手動入力"
    elif adr_in:
        adr_base = {"min": adr_in * 0.85, "mid": adr_in, "max": adr_in * 1.15}
        _ul = "一棟ADR" if revenue_unit == "whole" else "1室ADR"
        revpar_source = f"手動入力（{_ul}・±15%をレンジとして仮置き）"
        rp = {k: adr_base[k] * oc[k] for k in ("min", "mid", "max")}
    elif comps_summary and comps_summary.get("adr_yen"):
        ca = comps_summary["adr_yen"]
        adr_base = {k: ca[k] for k in ("min", "mid", "max")}
        revpar_source = f"近隣コンプ{comps_summary['comp_count']}件（中央値=mid・四分位=レンジ）"
        rp = {k: adr_base[k] * oc[k] for k in ("min", "mid", "max")}
    else:
        adr_base, revpar_source = _tier_adr()
        rp = {k: adr_base[k] * oc[k] for k in ("min", "mid", "max")}

    cap_mid_override = o.get("cap_rate_mid")
    cap = {"min": cr["max"], "mid": cap_mid_override or cr["mid"], "max": cr["min"]}
    scen = {
        "min": dict(adr=adr_base["min"], revpar=rp["min"], occ=oc["min"], cap=cr["max"]),
        "mid": dict(adr=adr_base["mid"], revpar=rp["mid"], occ=oc["mid"], cap=(cap_mid_override or cr["mid"])),
        "max": dict(adr=adr_base["max"], revpar=rp["max"], occ=oc["max"], cap=cr["min"]),
    }

    noi_breakdown = {}
    noi = {}
    income_value = {}
    for k, s in scen.items():
        nb = usali_noi(s["adr"], s["occ"], rooms_used, days, assessed, los=los,
                       cleaning_cost_per_stay_man=clean_cost_man,
                       cleaning_fee_per_stay_man=clean_fee_man)
        noi_breakdown[k] = nb
        noi[k] = nb["noi"]
        income_value[k] = inwood_value(nb["noi"], s["cap"], remaining)

    # 月次（季節性）と初年度（立ち上がり）— midシナリオで算出
    _ramp = rampup_factors()
    _clean_kw = dict(cleaning_cost_per_stay_man=clean_cost_man,
                     cleaning_fee_per_stay_man=clean_fee_man)
    monthly_stable = monthly_profile(scen["mid"]["adr"], scen["mid"]["occ"], rooms_used,
                                     days, assessed, los=los, **_clean_kw)
    monthly_year1 = monthly_profile(scen["mid"]["adr"], scen["mid"]["occ"], rooms_used,
                                    days, assessed, los=los, ramp=_ramp, **_clean_kw)
    noi_year1 = sum(r["noi"] for r in monthly_year1)
    if any(f < 1.0 for f in _ramp):
        _rc = _CFG.get("rampup", {})
        warnings.append(
            f"初年度は開業立ち上がりを反映しています（{_rc.get('months', 6)}ヶ月かけて"
            f"稼働{_rc.get('start_ratio', 0.4) * 100:.0f}%→100%へ回復）。"
            f"初年度NOI {noi_year1:,.0f}万円 ／ 安定稼働NOI {noi['mid']:,.0f}万円。"
        )

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
    # 1年目は開業立ち上がり後のCFを使う（複数年CF表と整合させる）
    cf_year1 = noi_year1 - ads
    if exit_year <= 1:
        cfs = [-equity, cf_year1 + sale_net]
    else:
        cfs = [-equity, cf_year1] + [cf_mid] * (exit_year - 2) + [cf_mid + sale_net]
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
    # ⚠️ 上の「良物件判定」の verdict を潰さないよう別名にする
    # （潰すと戻り値の "verdict" が価格の割安判定に化け、DSCR/CFの判定が消える）
    price_verdict = None
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
            price_verdict = "割安（相場下限より安い）"
        elif asking > est_high:
            price_verdict = "割高（相場上限より高い）"
        else:
            price_verdict = "適正レンジ内"
        valuation["price_verdict"] = price_verdict
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
                                scen["mid"]["cap"], price, equity, 5, cg_rate,
                                noi_year1=noi_year1)
    proj10 = cashflow_projection(noi_mid_val, ads, loan, loan_rate, term,
                                 scen["mid"]["cap"], price, equity, 10, cg_rate,
                                 noi_year1=noi_year1)
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
        "capacity_practical": capacity_practical,
        "capacity_legal_max": capacity_legal_max,
        "capacity_basis": capacity_basis,
        "municipality_key": muni_key,
        "municipality_name": muni_rule.get("name"),
        "municipality_detail_level": muni_rule.get("detail_level", "generic"),
        "room_area_m2": room_area,
        "floor_area_m2": floor_area,
        "remaining_useful_life_years": remaining,
        "adr_yen": {k: round(adr_base[k], 0) for k in ("min", "mid", "max")},
        "avg_length_of_stay": los,
        "market_comps": comps_summary,
        "noi_year1_man": round(noi_year1, 1),
        "monthly": {"stabilized": monthly_stable, "year1": monthly_year1},
        "assumptions": {
            "adr_yen": {k: round(adr_base[k], 0) for k in ("min", "mid", "max")},
            "avg_length_of_stay": los,
            "capacity_adr_exponent": cap_exp,
            "cleaning_cost_per_stay_man": clean_cost_man,
            "cleaning_fee_per_stay_man": clean_fee_man,
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
