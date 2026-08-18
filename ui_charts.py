"""収益シミュレーションのグラフ部品.

依存追加なしで動くよう、Streamlit が同梱している altair / pandas のみを使う。
各関数は altair.Chart を返す（Streamlit への描画は呼び出し側で st.altair_chart）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import altair as alt
import pandas as pd

# 配色（判定色と衝突しないトーン）
C_POS = "#2E7D32"      # 良い/プラス
C_NEG = "#C62828"      # 悪い/マイナス
C_MAIN = "#1565C0"     # 主線
C_SUB = "#90A4AE"      # 補助
C_ACCENT = "#EF6C00"   # 強調
C_BAND = "#B0BEC5"     # レンジ帯

_H = 260  # 標準の高さ


def _base(df: pd.DataFrame, height: int = _H) -> alt.Chart:
    return alt.Chart(df).properties(width="container", height=height)


# ---------------------------------------------------------------------------
# 複数年キャッシュフロー
# ---------------------------------------------------------------------------


def cumulative_cf_chart(proj: Dict[str, Any]) -> alt.LayerChart:
    """累計CF・ローン残債・その年に売却した場合の純利益を1枚で."""
    rows = proj.get("rows") or []
    recs: List[Dict[str, Any]] = []
    for r in rows:
        recs.append({"年": r["year"], "指標": "累計CF", "万円": r["cumulative_cf"]})
        recs.append({"年": r["year"], "指標": "ローン残債", "万円": r["loan_balance"]})
        recs.append({"年": r["year"], "指標": "売却時の純利益", "万円": r["net_profit_if_sell"]})
    df = pd.DataFrame(recs)

    line = _base(df).mark_line(point=True, strokeWidth=2.5).encode(
        x=alt.X("年:O", title="保有年数"),
        y=alt.Y("万円:Q", title="万円"),
        color=alt.Color(
            "指標:N",
            title=None,
            scale=alt.Scale(
                domain=["累計CF", "ローン残債", "売却時の純利益"],
                range=[C_MAIN, C_SUB, C_ACCENT],
            ),
            legend=alt.Legend(orient="top"),
        ),
        tooltip=[alt.Tooltip("年:O"), alt.Tooltip("指標:N"),
                 alt.Tooltip("万円:Q", format=",.0f")],
    )
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        strokeDash=[4, 4], color="#616161"
    ).encode(y="y:Q")
    return (line + zero).resolve_scale(y="shared")


def annual_cf_chart(proj: Dict[str, Any]) -> alt.Chart:
    """年間CFの棒グラフ（赤字年が一目で分かる）."""
    rows = proj.get("rows") or []
    df = pd.DataFrame([{"年": r["year"], "年間CF": r["annual_cf"], "NOI": r["noi"]} for r in rows])
    df["符号"] = df["年間CF"].apply(lambda v: "プラス" if v >= 0 else "マイナス")
    return _base(df, 220).mark_bar().encode(
        x=alt.X("年:O", title="保有年数"),
        y=alt.Y("年間CF:Q", title="年間CF（万円・税引前）"),
        color=alt.Color("符号:N", title=None,
                        scale=alt.Scale(domain=["プラス", "マイナス"], range=[C_POS, C_NEG]),
                        legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("年:O"), alt.Tooltip("NOI:Q", format=",.0f"),
                 alt.Tooltip("年間CF:Q", format=",.0f")],
    )


# ---------------------------------------------------------------------------
# 感度（tornado）
# ---------------------------------------------------------------------------


def tornado_chart(tornado: List[Dict[str, Any]]) -> Optional[alt.Chart]:
    """各ドライバーが基準値からどれだけ動かすかを横棒で表示.

    指標（単位）がドライバーごとに違うため、基準値からの**変化率(%)**に揃える。
    """
    recs: List[Dict[str, Any]] = []
    for t in tornado:
        base = t.get("base")
        if not base:
            continue
        for side, key in (("下振れ", "low"), ("上振れ", "high")):
            v = t.get(key)
            if v is None:
                continue
            recs.append({
                "ドライバー": f"{t['driver']}（{t['metric']}）",
                "方向": side,
                "変化率": (v / base - 1) * 100,
                "値": v,
                "基準": base,
                "ラベル": t.get("low_label" if key == "low" else "high_label", side),
            })
    if not recs:
        return None
    df = pd.DataFrame(recs)
    # 影響の大きい順に並べる
    order = (df.assign(a=df["変化率"].abs()).groupby("ドライバー")["a"].max()
             .sort_values(ascending=False).index.tolist())
    return _base(df, 40 * len(order) + 70).mark_bar().encode(
        y=alt.Y("ドライバー:N", title=None, sort=order),
        x=alt.X("変化率:Q", title="基準値からの変化率（%）"),
        color=alt.Color("方向:N", title=None,
                        scale=alt.Scale(domain=["下振れ", "上振れ"], range=[C_NEG, C_POS]),
                        legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("ドライバー:N"), alt.Tooltip("ラベル:N", title="シナリオ"),
                 alt.Tooltip("値:Q", format=",.0f"), alt.Tooltip("基準:Q", format=",.0f"),
                 alt.Tooltip("変化率:Q", format="+.1f")],
    )


# ---------------------------------------------------------------------------
# 月次（季節性・立ち上がり）
# ---------------------------------------------------------------------------


def monthly_chart(stabilized: List[Dict[str, Any]],
                  year1: Optional[List[Dict[str, Any]]] = None) -> alt.LayerChart:
    """月別の売上（棒）と稼働率（線）。初年度を重ねて立ち上がりを可視化."""
    recs: List[Dict[str, Any]] = []
    for r in stabilized:
        recs.append({"月": r["month"], "区分": "安定稼働", "売上": r["revenue"],
                     "稼働率": r["occupancy"] * 100})
    if year1:
        for r in year1:
            recs.append({"月": r["month"], "区分": "初年度", "売上": r["revenue"],
                         "稼働率": r["occupancy"] * 100})
    df = pd.DataFrame(recs)
    months = [r["month"] for r in stabilized]

    bars = _base(df).mark_bar(opacity=0.85).encode(
        x=alt.X("月:N", title=None, sort=months),
        y=alt.Y("売上:Q", title="月間売上 EGI（万円）"),
        xOffset=alt.XOffset("区分:N"),
        color=alt.Color("区分:N", title=None,
                        scale=alt.Scale(domain=["安定稼働", "初年度"], range=[C_MAIN, C_ACCENT]),
                        legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("月:N"), alt.Tooltip("区分:N"),
                 alt.Tooltip("売上:Q", format=",.0f"),
                 alt.Tooltip("稼働率:Q", format=".1f", title="稼働率(%)")],
    )
    line = _base(df).mark_line(point=True, strokeWidth=2).encode(
        x=alt.X("月:N", sort=months),
        y=alt.Y("稼働率:Q", title="稼働率（%）", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("区分:N", legend=None,
                        scale=alt.Scale(domain=["安定稼働", "初年度"], range=[C_MAIN, C_ACCENT])),
        strokeDash=alt.value([4, 3]),
        tooltip=[alt.Tooltip("月:N"), alt.Tooltip("区分:N"),
                 alt.Tooltip("稼働率:Q", format=".1f")],
    )
    return alt.layer(bars, line).resolve_scale(y="independent")


# ---------------------------------------------------------------------------
# 価格ポジション（相場 vs 売出）
# ---------------------------------------------------------------------------


def price_position_chart(valuation: Dict[str, Any],
                         fair_price: Optional[Dict[str, Any]] = None) -> Optional[alt.LayerChart]:
    """相場レンジ帯・適正価格mid・売出価格・目標NOI逆算価格を1本の軸に並べる."""
    mv = (valuation or {}).get("market_value_man") or {}
    if not mv or not mv.get("mid"):
        return None
    band = pd.DataFrame([{"下限": mv["min"], "上限": mv["max"], "y": "価格"}])
    band_c = _base(band, 150).mark_bar(height=46, color=C_BAND, opacity=0.55).encode(
        x=alt.X("下限:Q", title="万円", scale=alt.Scale(zero=False)),
        x2="上限:Q",
        y=alt.Y("y:N", title=None, axis=None),
        tooltip=[alt.Tooltip("下限:Q", format=",.0f", title="相場レンジ下限"),
                 alt.Tooltip("上限:Q", format=",.0f", title="相場レンジ上限")],
    )

    marks: List[Dict[str, Any]] = [
        {"種別": "想定適正価格(mid)", "万円": mv["mid"], "y": "価格"},
    ]
    if valuation.get("asking_price_man"):
        marks.append({"種別": "売出価格", "万円": valuation["asking_price_man"], "y": "価格"})
    if fair_price and fair_price.get("mid"):
        marks.append({"種別": "目標NOIから逆算(mid)", "万円": fair_price["mid"], "y": "価格"})
    df = pd.DataFrame(marks)

    rules = _base(df, 150).mark_rule(strokeWidth=3).encode(
        x=alt.X("万円:Q"),
        y=alt.Y("y:N", axis=None),
        color=alt.Color("種別:N", title=None,
                        scale=alt.Scale(
                            domain=["想定適正価格(mid)", "売出価格", "目標NOIから逆算(mid)"],
                            range=[C_MAIN, C_NEG, C_POS]),
                        legend=alt.Legend(orient="bottom", columns=1)),
        tooltip=[alt.Tooltip("種別:N"), alt.Tooltip("万円:Q", format=",.0f")],
    )
    labels = _base(df, 150).mark_text(dy=-26, fontSize=11).encode(
        x=alt.X("万円:Q"), y=alt.Y("y:N", axis=None),
        text=alt.Text("万円:Q", format=",.0f"),
        color=alt.Color("種別:N", legend=None,
                        scale=alt.Scale(
                            domain=["想定適正価格(mid)", "売出価格", "目標NOIから逆算(mid)"],
                            range=[C_MAIN, C_NEG, C_POS])),
    )
    return alt.layer(band_c, rules, labels)


# ---------------------------------------------------------------------------
# NOIの段階（売上 → NOI）
# ---------------------------------------------------------------------------


def noi_waterfall_chart(nb: Dict[str, Any]) -> alt.Chart:
    """GPI→EGI→GOP→NOI の落ち方を棒で表示（控除項目は赤）."""
    recs = [
        {"段階": "① GPI 満室潜在", "万円": nb.get("gpi", 0), "種別": "収入"},
        {"段階": "② EGI 実効総収入", "万円": nb.get("egi", 0), "種別": "収入"},
        {"段階": "③ −変動費", "万円": -abs(nb.get("variable", 0)), "種別": "控除"},
        {"段階": "④ −固定費", "万円": -abs(nb.get("fixed", 0)), "種別": "控除"},
        {"段階": "⑤ −FF&E積立", "万円": -abs(nb.get("ffe", 0)), "種別": "控除"},
        {"段階": "⑥ = NOI", "万円": nb.get("noi", 0), "種別": "結果"},
    ]
    df = pd.DataFrame(recs)
    order = [r["段階"] for r in recs]
    return _base(df, 300).mark_bar().encode(
        y=alt.Y("段階:N", title=None, sort=order),
        x=alt.X("万円:Q", title="万円/年"),
        color=alt.Color("種別:N", title=None,
                        scale=alt.Scale(domain=["収入", "控除", "結果"],
                                        range=[C_MAIN, C_NEG, C_POS]),
                        legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("段階:N"), alt.Tooltip("万円:Q", format=",.0f")],
    )


# ---------------------------------------------------------------------------
# シナリオ比較
# ---------------------------------------------------------------------------


def scenario_chart(res: Dict[str, Any]) -> alt.Chart:
    """弱気/標準/強気の NOI・収益価格・年間CF を並べる."""
    labels = {"min": "弱気", "mid": "標準", "max": "強気"}
    noi = res.get("noi") or {}
    iv = res.get("income_value_man") or {}
    fin = res.get("financing") or {}
    recs: List[Dict[str, Any]] = []
    for k, lab in labels.items():
        recs.append({"シナリオ": lab, "指標": "NOI", "万円": noi.get(k, 0)})
        recs.append({"シナリオ": lab, "指標": "収益価格", "万円": iv.get(k, 0)})
        cf = (fin.get(k) or {}).get("pretax_cf")
        recs.append({"シナリオ": lab, "指標": "年間CF", "万円": cf if cf is not None else 0})
    df = pd.DataFrame(recs)
    return _base(df, 240).mark_bar().encode(
        x=alt.X("シナリオ:N", title=None, sort=["弱気", "標準", "強気"]),
        y=alt.Y("万円:Q", title="万円"),
        color=alt.Color("シナリオ:N", legend=None,
                        scale=alt.Scale(domain=["弱気", "標準", "強気"],
                                        range=[C_NEG, C_MAIN, C_POS])),
        column=alt.Column("指標:N", title=None,
                          sort=["NOI", "収益価格", "年間CF"],
                          header=alt.Header(labelFontSize=12)),
        tooltip=[alt.Tooltip("シナリオ:N"), alt.Tooltip("指標:N"),
                 alt.Tooltip("万円:Q", format=",.0f")],
    ).properties(width=140)


def dscr_gauge_chart(fin: Dict[str, Any]) -> alt.LayerChart:
    """DSCRをシナリオ別に、1.0/1.2の基準線つきで表示."""
    labels = {"min": "弱気", "mid": "標準", "max": "強気"}
    recs = []
    for k, lab in labels.items():
        d = (fin.get(k) or {}).get("dscr")
        if d is not None:
            if d >= 1.2:
                verdict = "安全（1.2以上）"
            elif d >= 1.0:
                verdict = "ぎりぎり（1.0〜1.2）"
            else:
                verdict = "返済不足（1.0未満）"
            recs.append({"シナリオ": lab, "DSCR": d, "判定": verdict})
    df = pd.DataFrame(recs or [{"シナリオ": "—", "DSCR": 0, "判定": "返済不足（1.0未満）"}])
    bars = _base(df, 200).mark_bar(size=42).encode(
        x=alt.X("シナリオ:N", title=None, sort=["弱気", "標準", "強気"]),
        y=alt.Y("DSCR:Q", title="DSCR（NOI ÷ 年間返済）"),
        color=alt.Color("判定:N", title=None,
                        scale=alt.Scale(
                            domain=["安全（1.2以上）", "ぎりぎり（1.0〜1.2）", "返済不足（1.0未満）"],
                            range=[C_POS, C_ACCENT, C_NEG]),
                        legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("シナリオ:N"), alt.Tooltip("DSCR:Q", format=".2f"),
                 alt.Tooltip("判定:N")],
    )
    lines = alt.Chart(pd.DataFrame({"基準": [1.0, 1.2]})).mark_rule(
        strokeDash=[5, 3], color="#616161"
    ).encode(y="基準:Q")
    return alt.layer(bars, lines)


# ---------------------------------------------------------------------------
# ADR → NOI の導出テーブル（グラフではないが見せ方の中核）
# ---------------------------------------------------------------------------


def adr_derivation_rows(res: Dict[str, Any]) -> List[Dict[str, str]]:
    """ADRからNOIまでの導出を1行ずつ、式つきで返す（st.table用）."""
    nb = res.get("noi_breakdown_mid") or {}
    a = res.get("assumptions") or {}
    adr = (res.get("adr_yen") or {}).get("mid", 0)
    occ = (a.get("occupancy") or {}).get("mid", 0)
    rooms = res.get("rooms_used_for_revenue", 1)
    days = res.get("operating_days_used", 365)
    los = res.get("avg_length_of_stay", 3.0)
    stays = nb.get("stays", 0)

    def yen(v: float) -> str:
        return f"{v:,.0f} 円"

    def man(v: float) -> str:
        return f"{v:,.0f} 万円"

    return [
        {"項目": "① ADR（1泊単価）", "値": yen(adr), "根拠・式": res.get("revpar_source", "—")},
        {"項目": "② 稼働率", "値": f"{occ * 100:.1f} %", "根拠・式": "コンプ/手入力/エリア相場"},
        {"項目": "③ RevPAR", "値": yen(adr * occ), "根拠・式": "① × ②"},
        {"項目": "④ 収益計算室数", "値": f"{rooms} 室",
         "根拠・式": "一棟貸し=1／客室ごと=客室数"},
        {"項目": "⑤ 営業日数", "値": f"{days} 日",
         "根拠・式": "民泊は180日上限でクランプ"},
        {"項目": "⑥ GPI（満室潜在収入）", "値": man(nb.get("gpi", 0)), "根拠・式": "① × ④ × ⑤"},
        {"項目": "⑦ 客室収入", "値": man(nb.get("room_revenue", 0)), "根拠・式": "⑥ × ②（＝③×④×⑤）"},
        {"項目": "⑧ 平均宿泊日数(LOS)", "値": f"{los:.1f} 泊", "根拠・式": "清掃回数の分母"},
        {"項目": "⑨ 清掃組数", "値": f"{stays:,.0f} 組/年",
         "根拠・式": "稼働室夜 ÷ ⑧（1泊ごとではない）"},
        {"項目": "⑩ 清掃料金収入", "値": man(nb.get("cleaning_revenue", 0)),
         "根拠・式": f"1組 {a.get('cleaning_fee_per_stay_man', 0) * 10000:,.0f}円 × ⑨"},
        {"項目": "⑪ EGI（実効総収入）", "値": man(nb.get("egi", 0)), "根拠・式": "⑦ ＋ ⑩"},
        {"項目": "⑫ 変動費", "値": man(-abs(nb.get("variable", 0))),
         "根拠・式": "OTA手数料＋清掃原価＋リネン＋変動光熱"},
        {"項目": "⑬ 固定費", "値": man(-abs(nb.get("fixed", 0))),
         "根拠・式": "人件費＋管理料＋保険＋固都税＋基本光熱"},
        {"項目": "⑭ FF&E積立", "値": man(-abs(nb.get("ffe", 0))), "根拠・式": "⑪ × 積立率"},
        {"項目": "⑮ NOI（安定稼働）", "値": man(nb.get("noi", 0)), "根拠・式": "⑪ − ⑫ − ⑬ − ⑭"},
        {"項目": "⑯ NOI（初年度）", "値": man(res.get("noi_year1_man", 0)),
         "根拠・式": "季節性＋開業立ち上がりを月次で反映"},
    ]
