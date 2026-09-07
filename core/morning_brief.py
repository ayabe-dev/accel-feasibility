"""朝会1枚（Morning Brief）生成.

毎日15分の朝会で「前日に出てきた物件を購入検討リストにまとめるか」を決めるための
A4 1枚。営業（高橋さん）がこれを見ながら60秒で話せる形にする。

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
        # 売出価格は収益性タブの override（手入力）でしか入らない。
        # 未入力だと price は収益価格で代替されるため、割安判定が成立しない。
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
    out: Dict[str, Any] = {
        "asking_price_man": asking,
        "market_value_man": v.get("market_value_man"),
        "gap_pct": gap,
        "price_verdict": v.get("price_verdict"),
        "basis": r.get("basis_note", ""),
        "fair_price_man": (b.get("fair_price_man") or {}).get("mid"),
        "target_noi_yield_pct": (b.get("target_noi_yield") or 0) * 100 or None,
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
        out["note"] = f"開業まで最長{months_max}ヶ月＝{r.get('max_months_to_open')}ヶ月超で保留"
    elif verdict_value in (r.get("accept_verdicts") or []):
        out["status"] = "ok"
        out["note"] = "許可ルートあり＝前に進める"
    return out


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
    out: Dict[str, Any] = {
        "basis": r.get("basis_note", ""),
        "noi_mid_man": noi_mid,
        "noi_year1_man": prof.get("noi_year1_man"),
        "price_man": price,
        "price_is_override": a.get("price_is_override"),
        "initial_works_man": works_mid,
        "initial_works_source": b.get("initial_works_source"),
        "total_investment_man": total_investment,
        "noi_yield_total_pct": noi_yield_total,
        "noi_yield_on_price_pct": (fin.get("noi_yield") or 0) * 100 if fin.get("noi_yield") else None,
        "dscr": dscr,
        "pretax_cf_man": fin.get("pretax_cf"),
        "payback_years": fin.get("payback_years"),
        "financing_verdict": prof.get("verdict"),
        "status": "unknown",
        "note": "",
    }

    if noi_yield_total is None:
        out["note"] = "売出価格が未入力のため利回り判定不能"
        return out

    if noi_yield_total < r.get("min_noi_yield_pct", 5.0):
        out["status"] = "reject"
        out["note"] = f"対総投資 {noi_yield_total:.1f}% ＝ 下限{r.get('min_noi_yield_pct')}%未満"
    elif dscr is not None and dscr < r.get("min_dscr_hold", 1.0):
        out["status"] = "reject"
        out["note"] = f"DSCR {dscr:.2f} ＝ 返済が回らない"
    elif noi_yield_total >= r.get("target_noi_yield_pct", 7.0) and (
        dscr is None or dscr >= r.get("min_dscr", 1.2)
    ):
        out["status"] = "ok"
        out["note"] = f"対総投資 {noi_yield_total:.1f}% ＝ 目標{r.get('target_noi_yield_pct')}%以上"
    else:
        out["status"] = "weak"
        out["note"] = (
            f"対総投資 {noi_yield_total:.1f}%（目標{r.get('target_noi_yield_pct')}%未達）"
            + (f"・DSCR {dscr:.2f}" if dscr is not None else "")
        )
    return out


# ---------------------------------------------------------------------------
# 総合スクリーニング
# ---------------------------------------------------------------------------


def screen(report: FeasibilityReport) -> Dict[str, Any]:
    """3論点を評価して「載せる / 保留 / 見送り」を返す.

    ここが朝会の結論。判定の根拠（reasons）を必ず添える。
    """
    price = _eval_price(report)
    lic = _eval_license(report)
    yld = _eval_yield(report)
    missing = missing_inputs(report)

    reasons: List[str] = []
    verdict = "list_up"

    # 1) 落とす条件（どれか1つで落ちる）
    if lic["status"] == "reject":
        verdict = "drop"
        reasons.append(f"用途変更：{lic['verdict_label']}（{lic['note']}）")
    elif price["status"] == "reject":
        verdict = "drop"
        reasons.append(f"価格：{price['note']}")
    elif yld["status"] == "reject":
        verdict = "drop"
        reasons.append(f"利回り：{yld['note']}")
    # 2) 保留条件
    elif missing:
        verdict = "hold"
        reasons.append(
            "入力不足：" + "・".join(m["label"] for m in missing) + " が空欄"
        )
    elif lic["status"] == "hold":
        verdict = "hold"
        reasons.append(f"用途変更：{lic['note']}")
    elif price["status"] == "expensive" or yld["status"] == "weak":
        verdict = "hold"
        if price["status"] == "expensive":
            reasons.append(f"価格：{price['note']}")
        if yld["status"] == "weak":
            reasons.append(f"利回り：{yld['note']}")
        if price.get("suggested_discount_man"):
            reasons.append(
                f"目標NOI利回りに乗せるには {price['suggested_discount_man']:,.0f}万円の指値が必要"
            )
    elif "unknown" in (price["status"], yld["status"], lic["status"]):
        verdict = "hold"
        reasons.append("判定不能の論点あり（下の表の『判定不能』を埋める）")
    else:
        reasons.append(f"価格：{price['note']}")
        reasons.append(f"用途変更：{lic['verdict_label']}・難易度{lic['difficulty_label']}")
        reasons.append(f"利回り：{yld['note']}")

    hot = price["status"] == "great" and yld["status"] == "ok" and lic["status"] == "ok"
    vspec = (RULES.get("verdicts") or {}).get(verdict, {})

    return {
        "verdict": verdict,
        "verdict_label": vspec.get("label", verdict),
        "action": vspec.get("action", ""),
        "is_hot": hot,
        "reasons": reasons,
        "price": price,
        "license": lic,
        "yield": yld,
        "missing_inputs": missing,
        "rules_version": RULES.get("version"),
        "rules_as_of": RULES.get("as_of"),
    }


# ---------------------------------------------------------------------------
# 表示ヘルパ
# ---------------------------------------------------------------------------


def _man(v: Optional[float], suffix: str = "万円") -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}{suffix}"


def _pct(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}%"


def _num(v: Optional[float], digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _rng_man(d: Optional[Dict[str, Any]]) -> str:
    if not d:
        return "—"
    return f"{d.get('min', 0):,.0f}〜{d.get('max', 0):,.0f}万円（mid {d.get('mid', 0):,.0f}）"


def _building_age(built_year: Optional[int]) -> Optional[int]:
    if not built_year:
        return None
    return date.today().year - built_year


# ---------------------------------------------------------------------------
# 1枚レポート（Markdown）
# ---------------------------------------------------------------------------


def generate_morning_brief_markdown(
    report: FeasibilityReport,
    meta: Optional[Dict[str, str]] = None,
) -> str:
    """朝会1枚をMarkdownで返す.

    meta で受ける任意項目:
      property_name  物件名（無ければ住所）
      source         出どころ（レインズ / 仲介紹介 / DM / 訪問）
      presenter      説明する人
      memo           「良いと思った理由」（依頼事項②）
      note           その他の補足
    """
    meta = meta or {}
    s = screen(report)
    inp = report.input
    geo = report.geo
    price, lic, yld = s["price"], s["license"], s["yield"]
    mtg = RULES.get("meeting", {})

    name = meta.get("property_name") or inp.address or "物件名未設定"
    age = _building_age(inp.built_year)
    today = datetime.now().strftime("%Y-%m-%d")

    L: List[str] = []
    L.append(f"# 朝会1枚｜{name}")
    L.append("")
    L.append(
        f"> **{today} 朝会**（{mtg.get('name', '15分')}）｜説明: {meta.get('presenter') or '—'}"
        f"｜出どころ: {meta.get('source') or '—'}｜"
        f"目安 {mtg.get('seconds_per_property', 60)}秒"
    )
    L.append("")

    # ── 1行サマリー（minpaku 物件説明フォーマットの型） ────────────────
    L.append("## 1行サマリー")
    L.append("")
    L.append(
        f"> **【{s['verdict_label']}】{name}"
        f"（{inp.structure or '構造不明'}{f'{inp.floors_above}階建' if inp.floors_above else ''}"
        f"{f'・築{age}年' if age is not None else ''}"
        f"{f'・延床{inp.floor_area_m2:,.0f}㎡' if inp.floor_area_m2 else ''}）"
        f｜"
    )
    L.append("")

    return "\n".join(L)
