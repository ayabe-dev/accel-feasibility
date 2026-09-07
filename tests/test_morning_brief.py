"""朝会1枚（morning_brief）と判定ルールブック（rulebook）のテスト.

ネットワークに依存しないよう、用途地域は manual_geo で与える。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import gis_client
from core import judgment, morning_brief, profitability, rulebook
from core.models import BusinessType, ProjectInput


def _report(**overrides):
    """商業地域・検査済証あり・RC の標準ケース."""
    kwargs = dict(
        address="東京都新宿区高田馬場2-1-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=271.0,
        floors_above=5,
        floors_below=0,
        structure="RC造",
        built_year=2015,
        has_inspection_certificate=True,
        guest_room_count=8,
        conversion_area_m2=271.0,
    )
    kwargs.update(overrides)
    project = ProjectInput(**kwargs)
    geo = gis_client.from_manual_input(
        address=kwargs["address"],
        zoning_code="commercial",
        zoning_name="商業地域",
        fire_district="no_district",
        coverage_ratio_pct=80.0,
        floor_area_ratio_pct=400.0,
    )
    return judgment.run(project=project, docs=[], manual_geo=geo, has_nearby_facility=False)


def _with_profit(report, **ov):
    base = {"land_area_m2": 120.0, "revenue_unit": "per_room"}
    base.update(ov)
    report.profitability = profitability.compute(report, overrides=base)
    return report


# ---------------------------------------------------------------------------
# スクリーニング判定
# ---------------------------------------------------------------------------


def test_missing_price_is_hold():
    """売出価格が空なら「載せる」にはならない（論点1・3が答えられない）."""
    r = _with_profit(_report())
    s = morning_brief.screen(r)
    assert s["verdict"] == "hold", s
    labels = [m["label"] for m in s["missing_inputs"]]
    assert "売出価格" in labels, labels
    assert s["price"]["status"] == "unknown"
    print("✅ 売出価格なし → 保留＋欠落項目を明示")


def test_overpriced_is_dropped():
    """適正レンジmid比が閾値を超えたら見送り."""
    r = _with_profit(_report(), purchase_price_man=100000)
    s = morning_brief.screen(r)
    assert s["verdict"] == "drop", s["reasons"]
    assert s["price"]["status"] == "reject"
    print("✅ 割高 → 見送り")


def test_cheap_property_is_listed_up():
    """十分に安ければ「載せる」になり、激アツフラグが立つ."""
    r = _report()
    _with_profit(r)
    fair = morning_brief.fair_price_at_target(
        r.profitability["noi"]["mid"],
        (r.profitability["backward"]["initial_works_man"] or {}).get("mid"),
        r.profitability["backward"]["acq_cost_rate"],
        morning_brief.RULES["yield"]["target_noi_yield_pct"],
    )
    assert fair and fair > 0
    # 目標利回りを満たす価格の7割で出す（レンジmidも十分下回る水準）
    _with_profit(r, purchase_price_man=round(fair * 0.7))
    s = morning_brief.screen(r)
    assert s["verdict"] == "list_up", (s["verdict"], s["reasons"])
    assert s["yield"]["noi_yield_total_pct"] >= morning_brief.RULES["yield"]["target_noi_yield_pct"]
    print(f"✅ 安い物件 → 載せる（対総投資 {s['yield']['noi_yield_total_pct']:.1f}%）")


def test_thresholds_come_from_yaml():
    """閾値はコードではなくYAMLが正本."""
    assert morning_brief.RULES["version"]
    assert morning_brief.RULES["yield"]["target_noi_yield_pct"] == 7.0
    assert morning_brief.RULES["price"]["reject_premium_pct"] == 15.0
    print("✅ 閾値は screening_rules.yaml 由来")


def test_fair_price_formula():
    """払える上限 = (NOI÷目標利回り − 工事費) ÷ (1+諸費用率)."""
    got = morning_brief.fair_price_at_target(700.0, 1000.0, 0.07, 7.0)
    assert abs(got - ((700 / 0.07) - 1000) / 1.07) < 0.01, got
    # NOIが小さければ0でクランプされ、マイナス価格を出さない
    assert morning_brief.fair_price_at_target(10.0, 5000.0, 0.07, 7.0) == 0.0
    print("✅ 逆算式とゼロクランプ")


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


def test_brief_has_three_topics_and_no_none():
    r = _with_profit(_report(), purchase_price_man=12000)
    md = morning_brief.generate_morning_brief_markdown(
        r, meta={"property_name": "テスト物件", "source": "レインズ", "presenter": "高橋", "memo": "- 売り急ぎ"}
    )
    for heading in (
        "## 論点1：取得額は相場よりどのぐらい安いのか",
        "## 論点2：用途変更は可能か／難易度",
        "## 論点3：利回りは良いか",
        "## 📌 良いと思った理由",
        "秒の読み上げ台本",
    ):
        assert heading in md, heading
    assert "None" not in md, "未整形のNoneが露出している"
    assert "- 売り急ぎ" in md
    print("✅ 3論点＋理由＋台本が揃い、Noneが露出しない")


def test_one_line_summary_and_talk_track():
    r = _with_profit(_report(), purchase_price_man=12000)
    line = morning_brief.one_line_summary(r, {"property_name": "テスト物件"})
    assert "テスト物件" in line and "対総投資" in line
    track = morning_brief.talk_track(r, {"property_name": "テスト物件"})
    assert len(track) == 4 and all(track)
    print("✅ 1行サマリー・台本4行")


def test_brief_html_renders():
    r = _with_profit(_report(), purchase_price_man=12000)
    html = morning_brief.generate_morning_brief_html(r)
    assert html.startswith("<!DOCTYPE html>")
    assert "朝会1枚" in html
    from core import md_document

    try:
        import markdown  # noqa: F401

        # markdown があれば表として組まれる
        assert "<table>" in html, "tables拡張が効いていない"
        print("✅ HTML化（表つき）")
    except ImportError:
        # 無い環境ではフォールバック（<pre>）。内容は失わない
        assert "<pre>" in html
        assert md_document.markdown_to_html_document("t", "| a |")
        print("✅ HTML化（markdown未インストール環境のフォールバック）")


# ---------------------------------------------------------------------------
# ルールブック
# ---------------------------------------------------------------------------


def test_rulebook_sections():
    md = rulebook.generate_rulebook_markdown()
    for heading in (
        "## 1. 基準ファイル一覧",
        "## 3. 旅館業許可ゲート",
        "## 4. 一次情報レジストリ",
        "## 5. 用途地域 × 業態",
        "## 9. 調査パターンA〜Dと概算費用・期間",
        "## 10. 収益試算の前提",
        "## 11. スコアの重み",
        "## 12. 朝会スクリーニング",
        "## 13. コード側に埋まっている基準",
    ):
        assert heading in md, heading
    # 実データが載っていること
    assert "第一種低層住居専用地域" in md
    assert "screening_rules.yaml" in md
    assert "def _aggregate" in md, "総合判定の集約ルール（コード原文）が入っていない"
    assert "def classify_pattern" in md, "パターン判定ロジックが入っていない"
    print(f"✅ ルールブック全{len(md):,}文字・主要セクションあり")


def test_rulebook_gate_catalog_is_bounded():
    """各ゲートが自分の関数の範囲だけを拾っている（次のdefで切れている）."""
    cat = rulebook.gate_catalog()
    assert len(cat) == 12, [g["func"] for g in cat]
    ids = [g["gate_ids"][0] for g in cat]
    assert ids[0] == "A1_zoning" and ids[-1] == "D1_applicant", ids
    d1 = cat[-1]
    assert d1["cites"] == ["ryokan_law_3_2_disq"], d1["cites"]
    assert d1["categories"] == ["申請者"], d1["categories"]
    print("✅ ゲート12件を関数単位で正しく抽出")


# ---------------------------------------------------------------------------
# 回帰：良物件判定が価格判定に上書きされない
# ---------------------------------------------------------------------------


def test_financing_verdict_not_clobbered_by_price_verdict():
    r = _with_profit(_report(), purchase_price_man=12000)
    res = r.profitability
    assert res["verdict"] in (
        "GOOD（回りやすい）",
        "条件付き",
        "NG（現状CF赤字/DSCR<1）",
        "判定不能",
    ), res["verdict"]
    assert res["valuation"]["price_verdict"] in (
        "割安（相場下限より安い）",
        "割高（相場上限より高い）",
        "適正レンジ内",
    ), res["valuation"]["price_verdict"]
    print("✅ 良物件判定と価格判定が別々に保たれる")


if __name__ == "__main__":
    test_missing_price_is_hold()
    test_overpriced_is_dropped()
    test_cheap_property_is_listed_up()
    test_thresholds_come_from_yaml()
    test_fair_price_formula()
    test_brief_has_three_topics_and_no_none()
    test_one_line_summary_and_talk_track()
    test_brief_html_renders()
    test_rulebook_sections()
    test_rulebook_gate_catalog_is_bounded()
    test_financing_verdict_not_clobbered_by_price_verdict()
    print("\n🎉 全テストパス")
