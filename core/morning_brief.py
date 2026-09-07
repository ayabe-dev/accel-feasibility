"""朝会1枚（Morning Brief）生成.

毎日15分の朝会で「前日に出てきた物件を購入検討リストにまとめるか」を決めるためのA4 1枚。
営業担当がこれを見ながら60秒で話せる形にする。

詳細レポート（report_generator）との違い:
  - 論点を3つに固定する … ①相場よりどのぐらい安いか ②用途変更の可否と難易度 ③利回り
  - 結論を先に出す … 🟢載せる / 🟡保留 / 🔴見送り を機械判定する
  - 読み上げ台本を付ける … 事実確認に時間を使わせない
  - 判定に必要な入力の欠落を明示する … 「売出価格が空だから論点1は答えられない」と言わせる

判定閾値の正本は `config/screening_rules.yaml`（コードに数字を埋めない）。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import FeasibilityReport, InvestigationPattern

_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "screening_rules.yaml"


def _load_rules() -> Dict[str, Any]:
    with open(_RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


RULES: Dict[str, Any] = _load_rules()


# ---------------------------------------------------------------------------
# 入力の欠落チェック
# ---------------------------------------------------------------------------


def _input_present(report: FeasibilityReport, key: str) -> bool:
    """required_inputs の1項目が埋まっているか."""
    inp = report.input
    prof = getattr(report, "profitability", None) or {}
    assumptions = prof.get("assumptions") or {}
    valuation = prof.get("valuation") or {}

    return {
        "address": bool(inp.address),
        "business_type": bool(inp.business_type),
        "floor_area_m2": bool(inp.floor_area_m2),
        "floors_above": inp.floors_above is not None,
        "built_year": inp.built_year is not None,
        "structure": bool(inp.structure),
        "has_inspection": inp.has_inspection_certificate is not None,
        # 売出価格は収益性タブの手入力でしか入らない。未入力だと price は収益価格で
        # 代替されるため、割安判定（論点1）と利回り（論点3）が成立しない。
        "purchase_price": bool(valuation.get("asking_price_man")),
        "land_area": bool(assumptions.get("land_area_m2")),
        "guest_rooms": bool(inp.guest_room_count),
    }.get(key, True)


def missing_inputs(report: FeasibilityReport) -> List[Dict[str, str]]:
    """朝会で答えるために足りない入力を返す."""
    out: List[Dict[str, str]] = []
    for spec in RULES.get("required_inputs", []):
        if not _input_present(report, spec.get("key", "")):
            out.append(spec)
    return out


# ---------------------------------------------------------------------------
# 論点ごとの評価
# ---------------------------------------------------------------------------


def _eval_price(report: FeasibilityReport) -> Dict[str, Any]:
    """論点1：取得額が相場（＝想定適正価格レンジ）よりどのぐらい安いか."""
    r = RULES.get("price", {})
    prof = getattr(report, "profitability", None) or {}
    v = prof.get("valuation") or {}
    b = prof.get("backward") or {}

    gap = v.get("gap_pct")  # 適正レンジmid比（%）。マイナス=安い
    asking = v.get("asking_price_man")
    target = b.get("target_noi_yield")
    out: Dict[str, Any] = {
        "asking_price_man": asking,
        "market_value_man": v.get("market_value_man"),
        "income_value_mid_man": v.get("income_value_mid_man"),
        "cost_value_man": v.get("cost_value_man"),
        "gap_pct": gap,
        "price_verdict": v.get("price_verdict"),
        "land_per_tsubo_man": v.get("asking_land_per_tsubo_man"),
        "basis": r.get("basis_note", ""),
        "fair_price_man": (b.get("fair_price_man") or {}).get("mid"),
        "target_noi_yield_pct": target * 100 if target else None,
        "suggested_discount_man": b.get("suggested_discount_man"),
        "status": "unknown",
        "note": "",
    }

    if asking is None or gap is None:
        out["note"] = "売出価格が未入力のため判定不能（②収益化タブの「販売価格」に入れる）"
        return out

    if gap <= r.get("great_discount_pct", -20.0):
        out["status"] = "great"
        out["note"] = f"適正レンジmid比 {gap:+.1f}% ＝ 激アツ水準"
    elif gap <= r.get("ok_discount_pct", 0.0):
        out["status"] = "ok"
        out["note"] = f"適正レンジmid比 {gap:+.1f}% ＝ mid以下（合格）"
    elif gap <= r.get("reject_premium_pct", 15.0):
        out["status"] = "expensive"
        out["note"] = f"適正レンジmid比 {gap:+.1f}% ＝ mid超（指値が前提）"
    else:
        out["status"] = "reject"
        out["note"] = f"適正レンジmid比 {gap:+.1f}% ＝ 割高（見送り水準）"
    return out


def _eval_land(report: FeasibilityReport) -> Dict[str, Any]:
    """論点1-b：土地値で見るといくらか（築古はこちらが主軸）.

    築古は「建物はタダ、土地をいくらで買うか」に収斂する。法定耐用年数を超えていれば
    原価法[B]の建物価値は0で、実質は土地値との比較になる。
    """
    r = RULES.get("land", {})
    prof = getattr(report, "profitability", None) or {}
    v = prof.get("valuation") or {}
    land = prof.get("land") or {}

    # 判定は丸め前の値で行う（境界2.00倍が四捨五入で許容側に落ちるのを防ぐ）
    asking = v.get("asking_price_man")
    land_mid = (land.get("land_value_man") or {}).get("mid")
    ratio = (asking / land_mid) if (asking and land_mid) else None
    age = land.get("building_age_years")
    prefer_age = r.get("prefer_land_basis_if_age_over", 30)
    out: Dict[str, Any] = {
        "basis": r.get("basis_note", ""),
        "land_area_m2": land.get("land_area_m2"),
        "land_tsubo": land.get("land_tsubo"),
        "unit_price_per_tsubo_man": land.get("unit_price_per_tsubo_man"),
        "unit_price_source": land.get("unit_price_source"),
        "unit_price_is_override": land.get("unit_price_is_override"),
        "unit_price_is_official": land.get("unit_price_is_official"),
        "official_points": land.get("official_points") or [],
        "land_value_man": land.get("land_value_man"),
        "building_value_man": land.get("building_value_man"),
        "building_recost_man": land.get("building_recost_man"),
        "depreciation_pct": land.get("depreciation_pct"),
        "building_is_worthless": land.get("building_is_worthless"),
        "building_age_years": age,
        "asking_land_per_tsubo_man": v.get("asking_land_per_tsubo_man"),
        "price_to_land_ratio": round(ratio, 2) if ratio is not None else None,
        "excess_over_land_man": v.get("excess_over_land_man"),
        # 築古なら価格の合否は土地値比で決める
        "is_primary": bool(age is not None and age > prefer_age),
        "prefer_age": prefer_age,
        "status": "unknown",
        "note": "",
    }

    if ratio is None:
        out["note"] = (
            "土地面積または売出価格が未入力のため土地値と比べられない"
            "（築古なら**ここが一番効く**ので必ず埋める）"
        )
        return out

    if ratio <= r.get("solid_ratio", 1.0):
        out["status"] = "solid"
        out["note"] = f"売出は土地値の{ratio:.2f}倍＝**土地値以下**。下値が固い"
    elif ratio <= r.get("ok_ratio", 1.3):
        out["status"] = "ok"
        out["note"] = f"売出は土地値の{ratio:.2f}倍＝築古の実勢として許容範囲"
    elif ratio <= r.get("reject_ratio", 2.0):
        out["status"] = "expensive"
        out["note"] = f"売出は土地値の{ratio:.2f}倍＝上物に払い過ぎ（指値が前提）"
    else:
        out["status"] = "reject"
        out["note"] = f"売出は土地値の{ratio:.2f}倍＝土地値では説明できない"
    return out


def _eval_license(report: FeasibilityReport) -> Dict[str, Any]:
    """論点2：用途変更・旅館業許可の可否と難易度."""
    r = RULES.get("license", {})
    j = getattr(report, "license_judgment", None)
    pattern = getattr(report, "pattern", InvestigationPattern.UNKNOWN)
    pattern_key = getattr(pattern, "value", str(pattern))
    diff = (r.get("difficulty_by_pattern") or {}).get(pattern_key, {})
    ce = report.cost_estimate

    verdict_value = getattr(getattr(j, "verdict", None), "value", "unknown")
    blockers = [
        f"{g.title}：{g.finding}" for g in (getattr(j, "blocking_gates", None) or [])
    ]

    months_max = ce.total_months_max if ce else None
    out: Dict[str, Any] = {
        "verdict": verdict_value,
        "verdict_label": getattr(j, "verdict_label", "—"),
        "headline": getattr(j, "headline", ""),
        "permit_authority": getattr(j, "permit_authority", ""),
        "municipality_name": getattr(j, "municipality_name", ""),
        "confidence": getattr(j, "confidence", "—"),
        "data_completeness": getattr(j, "data_completeness", None),
        "blockers": blockers,
        "next_actions": list(getattr(j, "next_actions", []) or []),
        "pattern": pattern_key,
        "pattern_reason": getattr(report, "pattern_reason", ""),
        "difficulty_label": diff.get("label", "—"),
        "difficulty_score": diff.get("score"),
        "months": (ce.total_months_min, months_max) if ce else None,
        "cost_man": (ce.total_cost_min, ce.total_cost_max) if ce else None,
        "status": "unknown",
        "note": "",
    }

    if verdict_value in (r.get("reject_verdicts") or []):
        out["status"] = "reject"
        out["note"] = "許可が下りない／実質困難＝ここで落とす"
        return out

    slow = months_max is not None and months_max > r.get("max_months_to_open", 18)
    if verdict_value in (r.get("hold_verdicts") or []):
        out["status"] = "hold"
        out["note"] = "行政協議・情報待ち＝保留（待ち先と期限を言う）"
    elif pattern_key in (r.get("hold_if_pattern_in") or []):
        out["status"] = "hold"
        out["note"] = "検査済証なし＝法適合調査フル。価格が十分安いかで拾う"
    elif slow:
        out["status"] = "hold"
        out["note"] = (
            f"開業まで最長{months_max}ヶ月＝上限{r.get('max_months_to_open')}ヶ月超で保留"
        )
    elif verdict_value in (r.get("accept_verdicts") or []):
        out["status"] = "ok"
        out["note"] = "許可ルートあり＝前に進める"
    return out


def fair_price_at_target(
    noi_man: Optional[float],
    works_man: Optional[float],
    acq_rate: Optional[float],
    target_yield_pct: Optional[float],
) -> Optional[float]:
    """朝会の目標NOI利回りで「払っていい価格」を逆算する.

    総投資上限 = NOI ÷ 目標利回り
    適正価格   = (総投資上限 − 初期工事費) ÷ (1 + 取得諸費用率)

    ※ profitability.backward と同じ式。ただしあちらは収益化タブの目標利回り
      （既定15%）を使うため、朝会基準（screening_rules.yaml）では別に計算する。
    """
    if not noi_man or not target_yield_pct:
        return None
    cap = noi_man / (target_yield_pct / 100.0)
    return max(0.0, (cap - (works_man or 0.0)) / (1 + (acq_rate or 0.0)))


def _eval_yield(report: FeasibilityReport) -> Dict[str, Any]:
    """論点3：利回り（NOI ÷ 総投資）とDSCR."""
    r = RULES.get("yield", {})
    prof = getattr(report, "profitability", None) or {}
    fin = (prof.get("financing") or {}).get("mid") or {}
    b = prof.get("backward") or {}
    a = prof.get("assumptions") or {}
    noi_mid = (prof.get("noi") or {}).get("mid")

    # 総投資 = 取得価格 ×(1+取得諸費用率) ＋ 初期工事費（リノベ＋消防許可）
    price = a.get("price_assumption_man")
    acq_rate = b.get("acq_cost_rate")
    works_mid = (b.get("initial_works_man") or {}).get("mid")
    total_investment = None
    noi_yield_total = None
    if price and acq_rate is not None and works_mid is not None:
        total_investment = price * (1 + acq_rate) + works_mid
        if noi_mid and total_investment > 0:
            noi_yield_total = noi_mid / total_investment * 100

    dscr = fin.get("dscr")
    noi_yield_price = fin.get("noi_yield")
    out: Dict[str, Any] = {
        "basis": r.get("basis_note", ""),
        "noi_mid_man": noi_mid,
        "noi_year1_man": prof.get("noi_year1_man"),
        "price_man": price,
        "price_is_override": a.get("price_is_override"),
        "acq_cost_rate": acq_rate,
        "initial_works_man": works_mid,
        "initial_works_source": b.get("initial_works_source"),
        "total_investment_man": total_investment,
        "noi_yield_total_pct": noi_yield_total,
        "noi_yield_on_price_pct": noi_yield_price * 100 if noi_yield_price else None,
        "dscr": dscr,
        "pretax_cf_man": fin.get("pretax_cf"),
        "payback_years": fin.get("payback_years"),
        "financing_verdict": prof.get("verdict"),
        "adr_yen": (prof.get("adr_yen") or {}).get("mid"),
        "occupancy": ((a.get("occupancy") or {}).get("mid")),
        "rooms_used": prof.get("rooms_used_for_revenue"),
        "rooms_input": report.input.guest_room_count,
        "rooms_calc": prof.get("rooms"),
        "revenue_unit": prof.get("revenue_unit"),
        "status": "unknown",
        "note": "",
    }

    if noi_yield_total is None:
        out["note"] = "売出価格が未入力のため利回り判定不能"
        return out

    if noi_yield_total < r.get("min_noi_yield_pct", 5.0):
        out["status"] = "reject"
        out["note"] = (
            f"対総投資 {noi_yield_total:.1f}% ＝ 下限{r.get('min_noi_yield_pct')}%未満"
        )
    elif dscr is not None and dscr < r.get("min_dscr_hold", 1.0):
        out["status"] = "reject"
        out["note"] = f"DSCR {dscr:.2f} ＝ 返済が回らない"
    elif noi_yield_total >= r.get("target_noi_yield_pct", 7.0) and (
        dscr is None or dscr >= r.get("min_dscr", 1.2)
    ):
        out["status"] = "ok"
        out["note"] = (
            f"対総投資 {noi_yield_total:.1f}% ＝ 目標{r.get('target_noi_yield_pct')}%以上"
        )
    else:
        out["status"] = "weak"
        note = f"対総投資 {noi_yield_total:.1f}%（目標{r.get('target_noi_yield_pct')}%未達）"
        if dscr is not None:
            note += f"・DSCR {dscr:.2f}"
        out["note"] = note
    return out


# ---------------------------------------------------------------------------
# 総合スクリーニング
# ---------------------------------------------------------------------------


def screen(report: FeasibilityReport) -> Dict[str, Any]:
    """3論点を評価して「載せる / 保留 / 見送り」を返す.

    ここが朝会の結論。判定の根拠（reasons）を必ず添える。
    """
    price = _eval_price(report)
    land = _eval_land(report)
    lic = _eval_license(report)
    yld = _eval_yield(report)
    missing = missing_inputs(report)

    # 築古（既定：築30年超）は、価格の合否を「土地値比」で決める。
    # 収益還元は民泊前提のADRに引っ張られるが、土地値は動かないため。
    price_basis = "land" if (land["is_primary"] and land["status"] != "unknown") else "range"
    price_gate = land if price_basis == "land" else price

    # 「朝会の目標利回りなら、いくらまで払えるか」を逆算する。
    # 収益化タブの目標利回り（既定15%）とは別に、screening_rules の目標で出す。
    meeting_target = (RULES.get("yield") or {}).get("target_noi_yield_pct")
    price["meeting_target_yield_pct"] = meeting_target
    price["fair_price_at_meeting_target_man"] = fair_price_at_target(
        yld.get("noi_mid_man"),
        yld.get("initial_works_man"),
        yld.get("acq_cost_rate"),
        meeting_target,
    )
    fair_meeting = price["fair_price_at_meeting_target_man"]
    asking_now = price.get("asking_price_man")
    price["discount_needed_at_meeting_target_man"] = (
        max(0.0, asking_now - fair_meeting)
        if (fair_meeting is not None and asking_now)
        else None
    )

    reasons: List[str] = []
    verdict = "list_up"

    # 1) 落とす（どれか1つで落ちる）
    if lic["status"] == "reject":
        verdict = "drop"
        reasons.append(f"用途変更：{lic['verdict_label']}（{lic['note']}）")
    elif price_gate["status"] == "reject":
        verdict = "drop"
        reasons.append(f"価格：{price_gate['note']}")
    elif yld["status"] == "reject":
        verdict = "drop"
        reasons.append(f"利回り：{yld['note']}")
    # 2) 保留
    elif missing:
        verdict = "hold"
        reasons.append("入力不足：" + "・".join(m["label"] for m in missing) + " が空欄")
    elif lic["status"] == "hold":
        verdict = "hold"
        reasons.append(f"用途変更：{lic['note']}")
    elif price_gate["status"] == "expensive" or yld["status"] == "weak":
        verdict = "hold"
        if price_gate["status"] == "expensive":
            reasons.append(f"価格：{price_gate['note']}")
        if yld["status"] == "weak":
            reasons.append(f"利回り：{yld['note']}")
        if price.get("suggested_discount_man"):
            reasons.append(
                f"目標NOI利回りに乗せるには {price['suggested_discount_man']:,.0f}万円の指値が必要"
            )
    elif "unknown" in (price_gate["status"], yld["status"], lic["status"]):
        verdict = "hold"
        reasons.append("判定不能の論点あり（下の表の『判定不能』を埋める）")
    # 3) 載せる
    else:
        reasons.append(f"価格：{price_gate['note']}")
        reasons.append(
            f"用途変更：{lic['verdict_label']}・難易度{lic['difficulty_label']}"
        )
        reasons.append(f"利回り：{yld['note']}")

    hot = (
        price_gate["status"] in ("great", "solid")
        and yld["status"] == "ok"
        and lic["status"] == "ok"
    )
    vspec = (RULES.get("verdicts") or {}).get(verdict, {})

    return {
        "verdict": verdict,
        "verdict_label": vspec.get("label", verdict),
        "action": vspec.get("action", ""),
        "is_hot": hot,
        "reasons": reasons,
        "price": price,
        "land": land,
        "price_basis": price_basis,   # "land" = 築古なので土地値比で判定した
        "license": lic,
        "yield": yld,
        "missing_inputs": missing,
        "rules_version": RULES.get("version"),
        "rules_as_of": RULES.get("as_of"),
    }


# ---------------------------------------------------------------------------
# 表示ヘルパ
# ---------------------------------------------------------------------------


def _man(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:,.0f}万円"


def _pct(v: Optional[float], digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}%"


def _num(v: Optional[float], digits: int = 2, suffix: str = "") -> str:
    return "—" if v is None else f"{v:.{digits}f}{suffix}"


def _signed_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _rng_man(d: Optional[Dict[str, Any]]) -> str:
    if not d:
        return "—"
    return (
        f"{d.get('min', 0):,.0f}〜{d.get('max', 0):,.0f}万円"
        f"（mid {d.get('mid', 0):,.0f}）"
    )


def _pair_man(t: Optional[tuple]) -> str:
    if not t:
        return "—"
    return f"{t[0]:,.0f}〜{t[1]:,.0f}万円"


def _pair_months(t: Optional[tuple]) -> str:
    if not t:
        return "—"
    return f"{t[0]}〜{t[1]}ヶ月"


def _building_age(built_year: Optional[int]) -> Optional[int]:
    if not built_year:
        return None
    return date.today().year - built_year


def _property_line(report: FeasibilityReport) -> str:
    """「RC造5階建・築36年・延床271㎡」の1行."""
    inp = report.input
    parts: List[str] = []
    if inp.structure:
        parts.append(str(inp.structure))
    if inp.floors_above:
        below = f"地下{inp.floors_below}階" if inp.floors_below else ""
        parts.append(f"{inp.floors_above}階建{below}")
    age = _building_age(inp.built_year)
    if age is not None:
        parts.append(f"築{age}年")
    if inp.floor_area_m2:
        parts.append(f"延床{inp.floor_area_m2:,.0f}㎡")
    return "・".join(parts) or "スペック未入力"


# ---------------------------------------------------------------------------
# 1枚レポート（Markdown）
# ---------------------------------------------------------------------------


def one_line_summary(report: FeasibilityReport, meta: Optional[Dict[str, str]] = None) -> str:
    """Notion物件ページ・朝会アジェンダの冒頭に置く1行.

    型は minpaku/projects/minpaku-teirei/templates/物件説明フォーマット_1枚.md に合わせる。
    """
    meta = meta or {}
    s = screen(report)
    name = meta.get("property_name") or report.input.address or "物件名未設定"
    price, lic, yld = s["price"], s["license"], s["yield"]
    land = s["land"]
    # 築古は土地値比を先に言う（そこが判定根拠だから）
    if s["price_basis"] == "land":
        price_part = (
            f"｜売出{_man(price['asking_price_man'])} → **土地値の"
            f"{_num(land['price_to_land_ratio'])}倍**"
            f"（適正レンジ比 {_signed_pct(price['gap_pct'])}）"
        )
    else:
        price_part = (
            f"｜売出{_man(price['asking_price_man'])} → 適正レンジ比 "
            f"{_signed_pct(price['gap_pct'])}"
        )
    return (
        f"【{s['verdict_label']}】{name}（{_property_line(report)}）"
        + price_part
        + f"｜用途変更 {lic['verdict_label']}・{lic['difficulty_label']}・{_pair_months(lic['months'])}"
        + f"｜対総投資 {_pct(yld['noi_yield_total_pct'])}"
    )


def talk_track(report: FeasibilityReport, meta: Optional[Dict[str, str]] = None) -> List[str]:
    """60秒で読み上げる台本（4行）."""
    meta = meta or {}
    s = screen(report)
    price, lic, yld = s["price"], s["license"], s["yield"]
    name = meta.get("property_name") or report.input.address or "この物件"
    src = meta.get("source")

    l1 = f"「{name}、{_property_line(report)}。出どころは{src or '（未記入）'}です。"
    land = s["land"]
    if land["price_to_land_ratio"] is not None:
        l2 = (
            f"値段は{_man(price['asking_price_man'])}。"
            f"土地{_num(land['land_tsubo'], 0, '坪')}で土地値が{_man((land.get('land_value_man') or {}).get('mid'))}なので、"
            f"土地値の{_num(land['price_to_land_ratio'])}倍"
            f"（上物ゼロ換算で坪{_man(land['asking_land_per_tsubo_man'])}）。"
            f"{land['note'].replace('**', '')}。"
        )
    else:
        l2 = (
            f"値段は{_man(price['asking_price_man'])}。うちの想定適正価格は"
            f"{_rng_man(price['market_value_man'])}なので、{_signed_pct(price['gap_pct'])}。"
            f"{price['note']}。"
        )
    l3 = (
        f"用途変更は{lic['verdict_label']}。調査パターン{lic['pattern']}＝難易度{lic['difficulty_label']}で、"
        f"開業まで{_pair_months(lic['months'])}・費用{_pair_man(lic['cost_man'])}の見込み。"
    )
    if lic["blockers"]:
        l3 += f"ネックは{lic['blockers'][0].split('：')[0]}。"
    l4 = (
        f"回りは NOI {_man(yld['noi_mid_man'])}／年、総投資{_man(yld['total_investment_man'])}に対して"
        f"{_pct(yld['noi_yield_total_pct'])}、DSCR {_num(yld['dscr'])}。"
        f"→ 提案は【{s['verdict_label']}】です。」"
    )
    return [l1, l2, l3, l4]


def generate_morning_brief_markdown(
    report: FeasibilityReport,
    meta: Optional[Dict[str, str]] = None,
) -> str:
    """朝会1枚をMarkdownで返す.

    meta で受ける任意項目:
      property_name  物件名（無ければ住所）
      source         出どころ（レインズ / 仲介紹介 / DM / 訪問）
      presenter      説明する人
      memo           「良いと思った理由」（朝会の依頼事項②）
      note           その他の補足
    """
    meta = meta or {}
    s = screen(report)
    inp = report.input
    geo = report.geo
    price, lic, yld = s["price"], s["license"], s["yield"]
    mtg = RULES.get("meeting", {})

    name = meta.get("property_name") or inp.address or "物件名未設定"
    today = datetime.now().strftime("%Y-%m-%d")
    prof = getattr(report, "profitability", None) or {}

    L: List[str] = []

    # ── ヘッダ ────────────────────────────────────────────
    L.append(f"# 朝会1枚｜{name}")
    L.append("")
    L.append(
        f"**{today}**｜説明: {meta.get('presenter') or '—'}"
        f"｜出どころ: {meta.get('source') or '—'}"
        f"｜持ち時間 {mtg.get('seconds_per_property', 60)}秒"
        f"｜この会で決めること: {mtg.get('purpose', '')}"
    )
    L.append("")

    # ── 結論 ──────────────────────────────────────────────
    L.append(f"## 🎯 結論：{s['verdict_label']}" + ("　🔥激アツ" if s["is_hot"] else ""))
    L.append("")
    for r in s["reasons"]:
        L.append(f"- {r}")
    if s["action"]:
        L.append("")
        L.append(f"**次アクション**：{s['action']}")
    L.append("")
    L.append("### 1行サマリー（そのまま読む）")
    L.append("")
    L.append(f"> {one_line_summary(report, meta)}")
    L.append("")

    # ── 論点1 ─────────────────────────────────────────────
    L.append("## 論点1：取得額は相場よりどのぐらい安いのか")
    L.append("")
    L.append(
        "**見方は2つある**。1-a＝収益還元＋原価法の適正価格レンジ、1-b＝土地値。"
        f"この物件は築{_building_age(inp.built_year) if inp.built_year else '?'}年なので、"
        + ("**1-b（土地値）で合否を判定**している。" if s["price_basis"] == "land"
           else "1-a（適正価格レンジ）で合否を判定している。")
    )
    L.append("")
    L.append("| 項目 | 値 |")
    L.append("|---|---|")
    L.append(f"| 売出価格（先方希望額） | **{_man(price['asking_price_man'])}** |")
    L.append(f"| 想定適正価格レンジ | {_rng_man(price['market_value_man'])} |")
    L.append(f"| ├ 収益価格[A]（NOI還元） | {_man(price['income_value_mid_man'])} |")
    L.append(f"| └ 原価法[B]（土地値＋建物） | {_man(price['cost_value_man'])} |")
    L.append(
        f"| **レンジmid比（朝会の判定基準）** | **{_signed_pct(price['gap_pct'])}**"
        f" → {price['note'] or '—'} |"
    )
    L.append(f"| （参考）レンジ内外の判定 | {price['price_verdict'] or '判定不能'} |")
    if price.get("land_per_tsubo_man"):
        L.append(f"| 参考・土地坪単価（価格ベース） | {_man(price['land_per_tsubo_man'])}/坪 |")
    L.append(
        f"| **朝会基準（NOI利回り{_pct(price.get('meeting_target_yield_pct'), 0)}）で払える上限** | "
        f"**{_man(price.get('fair_price_at_meeting_target_man'))}** |"
    )
    L.append(
        f"| → その上限に乗せるのに必要な指値 | "
        f"{_man(price.get('discount_needed_at_meeting_target_man'))} |"
    )
    L.append(
        f"| （参考）収益化タブの目標{_pct(price['target_noi_yield_pct'], 0)}での適正価格 | "
        f"{_man(price['fair_price_man'])} |"
    )
    L.append("")
    L.append(f"> ⚠️ **ここでいう「相場」の定義**：{price['basis']}")
    L.append(
        "> レンジは収益価格[A]と原価法[B]の外側を取るため広い。"
        "**レンジ内外は参考**で、見るのは mid 比と「払える上限」。"
    )
    L.append("")

    # ── 論点1-b：土地値（築古はここが主軸） ─────────────────
    land = s["land"]
    primary = " 🔑**この物件はここで判定**" if s["price_basis"] == "land" else ""
    L.append(f"### 論点1-b：土地値で見るといくらか{primary}")
    L.append("")
    L.append("| 項目 | 値 |")
    L.append("|---|---|")
    L.append(
        f"| 土地面積 | {_num(land['land_area_m2'], 1, '㎡')}"
        f"（{_num(land['land_tsubo'], 1, '坪')}） |"
    )
    unit = land.get("unit_price_per_tsubo_man") or {}
    L.append(
        f"| 土地坪単価 | {_man(unit.get('mid'))}/坪"
        f"（{_man(unit.get('min'))}〜{_man(unit.get('max'))}）"
        f"<br>出所：{land.get('unit_price_source') or '—'} |"
    )
    lv = land.get("land_value_man") or {}
    lv_disp = (
        _man(lv.get("mid"))
        if (lv and lv.get("min") == lv.get("max"))
        else _rng_man(land.get("land_value_man"))
    )
    L.append(f"| **土地値** | **{lv_disp}** |")
    L.append(
        f"| 建物の残存価値 | {_man(land['building_value_man'])}"
        f"（再調達{_man(land['building_recost_man'])}・築{land['building_age_years']}年で"
        f"{_pct(land['depreciation_pct'], 0)}償却"
        f"{'＝**建物ゼロ評価**' if land['building_is_worthless'] else ''}） |"
    )
    L.append(f"| **売出価格 ÷ 土地値** | **{_num(land['price_to_land_ratio'])}倍** |")
    excess = land.get("excess_over_land_man")
    excess_note = (
        "＝上物・のれんへの支払い" if (excess or 0) > 0 else "マイナス＝土地値より安く買える"
    )
    L.append(f"| 土地値との差 | {_man(excess)}（{excess_note}） |")
    L.append(
        f"| 上物ゼロ評価での土地坪単価 | {_man(land['asking_land_per_tsubo_man'])}/坪"
        "　←**実勢の坪単価と直接比べる数字** |"
    )
    L.append("")
    L.append(f"**判定**：{land['note'] or '—'}")
    L.append("")
    if land.get("unit_price_is_override"):
        L.append(f"> 土地坪単価は実勢の手入力値。{land['basis']}")
    elif land.get("unit_price_is_official"):
        L.append("> 土地坪単価は**地価公示・地価調査の近傍標準地**から取得（下表が採用点）。")
        L.append("")
        L.append("| 標準地 | 距離 | 用途区分 | 円/㎡ | 万円/坪 |")
        L.append("|---|---:|---|---:|---:|")
        for pt in land["official_points"][:3]:
            L.append(
                f"| {pt.get('standard_lot') or '—'}"
                f"（{pt.get('address') or '—'}） | {pt.get('distance_m', '—')}m"
                f" | {pt.get('use_category') or '—'}"
                f" | {pt.get('price_per_m2', 0):,.0f}"
                f" | {pt.get('per_tsubo_man', 0):,.1f} |"
            )
        L.append("")
        L.append(
            "> ⚠️ 標準地は「その地点」の価格。**間口・接道・形状・角地かで実勢は上下する**ので、"
            "仲介の感触や成約事例と突合すること。"
        )
    else:
        L.append(
            "> ⚠️ 土地坪単価は**エリア既定の代表値**（同じ23区内なら一律）。"
            "地価公示も取れていない（住所から緯度経度が出ない／APIキー未設定）。"
            "実勢が分かるなら②収益化タブの「土地坪単価」に入れ直す。"
        )
    L.append("")

    # ── 論点2 ─────────────────────────────────────────────
    L.append("## 論点2：用途変更は可能か／難易度")
    L.append("")
    L.append("| 項目 | 値 |")
    L.append("|---|---|")
    L.append(f"| **判定** | **{lic['verdict_label']}** |")
    L.append(f"| 用途地域 | {geo.zoning_name or '—'} |")
    L.append(f"| 自治体・許可権者 | {lic['municipality_name'] or '—'} / {lic['permit_authority'] or '—'} |")
    L.append(f"| 調査パターン | {lic['pattern']}（{lic['difficulty_label']}） |")
    L.append(f"| 期間（調査＋申請＋工事） | {_pair_months(lic['months'])} |")
    L.append(f"| 費用 | {_pair_man(lic['cost_man'])} |")
    L.append(
        f"| 判定の確度 | 信頼度 {lic['confidence']}／情報充足率 "
        f"{_pct((lic['data_completeness'] or 0) * 100, 0)} |"
    )
    L.append("")
    if lic["headline"]:
        L.append(f"{lic['headline']}")
        L.append("")
    if lic["blockers"]:
        L.append("**越えるべきネック**")
        for b in lic["blockers"][:5]:
            L.append(f"- {b}")
        L.append("")
    if lic["next_actions"]:
        L.append("**確定に必要な照会（誰に聞くか）**")
        for a in lic["next_actions"][:5]:
            L.append(f"- {a}")
        L.append("")

    # ── 論点3 ─────────────────────────────────────────────
    L.append("## 論点3：利回りは良いか")
    L.append("")
    L.append("| 項目 | 値 |")
    L.append("|---|---|")
    L.append(
        f"| 前提（ADR×稼働×室数） | {_num(yld['adr_yen'], 0, '円')} × "
        f"{_pct((yld['occupancy'] or 0) * 100, 0)} × {yld['rooms_used'] or '—'}室"
        f"（{'一棟貸し' if yld.get('revenue_unit') == 'whole' else '客室ごと'}） |"
    )
    L.append(f"| NOI（安定稼働・mid） | **{_man(yld['noi_mid_man'])}/年** |")
    L.append(f"| NOI（初年度・立ち上がり反映） | {_man(yld['noi_year1_man'])}/年 |")
    L.append(f"| 取得価格 | {_man(yld['price_man'])} |")
    L.append(
        f"| ＋取得諸費用 | {_pct((yld['acq_cost_rate'] or 0) * 100, 1)}"
        f"（{_man((yld['price_man'] or 0) * (yld['acq_cost_rate'] or 0))}） |"
    )
    L.append(
        f"| ＋初期工事費（リノベ＋消防許可） | {_man(yld['initial_works_man'])}"
        f"／{yld['initial_works_source'] or '—'} |"
    )
    L.append(f"| **＝総投資額** | **{_man(yld['total_investment_man'])}** |")
    L.append(f"| **NOI利回り（対総投資）** | **{_pct(yld['noi_yield_total_pct'])}** |")
    L.append(f"| 参考・NOI利回り（対取得価格） | {_pct(yld['noi_yield_on_price_pct'])} |")
    L.append(f"| DSCR / 税引前CF | {_num(yld['dscr'])} / {_man(yld['pretax_cf_man'])}/年 |")
    L.append(f"| 自己資金回収年数 | {_num(yld['payback_years'], 1, '年')} |")
    L.append(f"| 融資の回り方 | {yld['financing_verdict'] or '—'} |")
    L.append("")
    L.append(f"> 利回りの定義：{yld['basis']}")
    L.append("")

    # ── 良いと思った理由（依頼事項②） ─────────────────────
    L.append("## 📌 良いと思った理由（持ってきた人が書く）")
    L.append("")
    if meta.get("memo"):
        L.append(meta["memo"])
    else:
        L.append("- （空欄）ここを埋めずに朝会に出すと、数字だけの会になる")
        L.append("- 例：同じ通りの◯◯が◯万で成約／オーナーが売り急いでいる／隣が民泊で回っている")
    L.append("")

    # ── 台本 ──────────────────────────────────────────────
    L.append(f"## ⏱ {mtg.get('seconds_per_property', 60)}秒の読み上げ台本")
    L.append("")
    for i, line in enumerate(talk_track(report, meta), 1):
        L.append(f"{i}. {line}")
    L.append("")

    # ── 言い方の注意 ───────────────────────────────────────
    L.append("## ⚠️ 言い方の注意（混ぜると事故になる）")
    L.append("")
    L.append("- この判定は**机上の一次スクリーニング**。許可の可否を決めるのは保健所・特定行政庁・消防。")
    L.append("  「行政に確認済み」と「まだ机上」を混ぜて話さない。")
    if s["missing_inputs"]:
        L.append("- **入力が空のため答えられない論点がある**：")
        for m in s["missing_inputs"]:
            L.append(f"  - {m['label']}（{m.get('why', '')}）")
    rooms_in, rooms_used = yld.get("rooms_input"), yld.get("rooms_used")
    if rooms_in and rooms_used and rooms_in != rooms_used:
        L.append(
            f"- **室数が入力と違う**：入力{rooms_in}室に対し、試算は面積から算出した"
            f"{rooms_used}室を使っている（`core/profitability.py` は延床×有効率÷1室面積で室数を出す）。"
            "室数を効かせるには②収益化タブで1室面積を実測値にする。"
        )
    if prof.get("warnings"):
        for w in list(prof["warnings"])[:3]:
            L.append(f"- {w}")
    if report.missing_documents:
        L.append(f"- 不足書類：{'・'.join(report.missing_documents[:5])}")
    L.append("")

    # ── フッタ ────────────────────────────────────────────
    L.append("---")
    L.append("")
    L.append(
        f"判定基準：`config/screening_rules.yaml` v{s['rules_version']}（{s['rules_as_of']} 時点）"
        f"／収益前提：`config/revenue_estimates.yaml` v{prof.get('config_version', '—')}"
        f"（{prof.get('as_of', '—')} 時点）"
    )
    L.append("")
    L.append(
        f"生成：用途変更フィジビリティ判定システム／{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        "　※基準の全文は「判定ルールブック」を参照"
    )
    return "\n".join(L)


# ---------------------------------------------------------------------------
# HTML / PDF
# ---------------------------------------------------------------------------


def generate_morning_brief_html(
    report: FeasibilityReport,
    meta: Optional[Dict[str, str]] = None,
) -> str:
    """朝会1枚のHTML（印刷してそのまま持ち込める体裁）."""
    from .md_document import markdown_to_html_document

    name = (meta or {}).get("property_name") or report.input.address or "物件"
    return markdown_to_html_document(
        title=f"朝会1枚 — {name}",
        markdown_text=generate_morning_brief_markdown(report, meta),
    )


def generate_morning_brief_pdf(
    report: FeasibilityReport,
    meta: Optional[Dict[str, str]] = None,
) -> Optional[bytes]:
    """朝会1枚のPDF。weasyprint 未インストール時は None."""
    from .md_document import html_to_pdf

    return html_to_pdf(generate_morning_brief_html(report, meta))
