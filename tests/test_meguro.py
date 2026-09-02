"""目黒区ルール（区の手引き・条例）の回帰テスト.

出典:
  - 目黒区「旅館業の手引き」令和6年1月改訂
  - 目黒区「既存住宅等を利用し、旅館・ホテルへの用途変更を検討している皆様へ」令和7年8月8日
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import judgment, municipality, profitability
from core.models import BusinessType, JudgmentLevel, ProjectInput
from core.report_generator import generate_markdown_report
from core.report_html import generate_html_report

MEGURO = "東京都目黒区中目黒1-1-1"


def _run(address=MEGURO, bt=BusinessType.HOTEL_RYOKAN, fa=271.0,
         structure="木造", built=1985, floors=3):
    p = ProjectInput(address=address, business_type=bt, floor_area_m2=fa,
                     structure=structure, built_year=built, floors_above=floors)
    return judgment.run(project=p, docs=[])


# ---------------------------------------------------------------------------
# 自治体判定
# ---------------------------------------------------------------------------


def test_detects_meguro_as_detailed():
    key = municipality.detect_municipality(MEGURO)
    assert key == "meguro_ku", key
    assert municipality.get_municipality_name(key) == "目黒区"
    assert municipality.is_detailed(key) is True
    # 他区は当て込み扱い（誤って「実データ反映済み」と表示しないこと）
    assert municipality.is_detailed(municipality.detect_municipality("東京都新宿区西新宿2-8-1")) is False
    print("✅ 目黒区は実データ反映済み（他区は当て込み）と判別される")


def test_meguro_rule_contents():
    r = municipality.get_municipality_rule("meguro_ku")
    # 定員基準（条4-*-6）
    assert r["capacity"]["hotel_ryokan_m2_per_guest"] == 3.0
    assert r["capacity"]["simple_lodging_m2_per_guest"] == 1.5
    # 簡易宿所の客室延床（令1-2-1）
    assert r["room_area"]["simple_lodging_total_min_m2"] == 33.0
    # 完全無人は不可
    assert r["front_desk"]["unmanned_allowed"] is False
    # 手数料
    assert r["application_fee_yen"]["hotel_ryokan"] == 30600
    assert r["application_fee_yen"]["simple_lodging"] == 16500
    # 便所数テーブル（規11-*-1）
    table = {row["up_to"]: row["count"] for row in r["toilet_count_by_capacity"]["table"]}
    assert table == {5: 2, 10: 3, 15: 4, 20: 5, 25: 6, 30: 7}, table
    # 区に指定のない用途地域
    assert "準住居地域" in r["zoning"]["not_designated_in_ward"]
    # 記録照会の起点（昭和34年＝1959／建築計画概要書は昭和46年＝1971）
    assert r["record_availability"]["confirmation_certificate_since_year"] == 1959
    assert r["record_availability"]["building_outline_since_year"] == 1971
    print("✅ 目黒区の条例数値が資料どおり登録されている")


# ---------------------------------------------------------------------------
# 旅館業法チェック
# ---------------------------------------------------------------------------


def test_lodging_checks_include_meguro_specifics():
    r = _run()
    blob = "\n".join(f"{c.item_name}｜{c.standard}｜{c.note}" for c in r.lodging_business_checks)
    for needle in [
        "路地状敷地",                    # 事業化不可条件
        "文教地区",                      # 事業化不可条件
        "完全無人運営は不可",             # 玄関帳場
        "有効面積3.0㎡につき1人",         # 定員
        "5人以下:2",                     # 便所テーブル
        "定員5人につき1給水栓",           # 洗面
        "30,600円",                      # 手数料
        "内法",                          # 面積算定
        "窓のない客室は不可",
        "意見照会",                      # 距離規制（100m）
    ]:
        assert needle in blob, f"旅館業法チェックに '{needle}' がない"
    print("✅ 旅館業法チェックに目黒区固有基準が反映されている")


def test_generic_municipality_has_no_meguro_items():
    """当て込み自治体に目黒区固有の項目が漏れ出さない."""
    r = _run(address="東京都新宿区西新宿2-8-1")
    blob = "\n".join(f"{c.item_name}｜{c.note}" for c in r.lodging_business_checks)
    assert "路地状敷地" not in blob
    assert "文教地区" not in blob
    assert "30,600円" not in blob
    print("✅ 目黒区固有項目が他区に漏れない")


# ---------------------------------------------------------------------------
# 建築基準法（区公表資料）
# ---------------------------------------------------------------------------


def test_building_checks_include_meguro_notes():
    r = _run()
    muni_items = [c for c in r.building_code_checks if c.rule_id.startswith("muni_")]
    assert len(muni_items) >= 12, len(muni_items)
    blob = "\n".join(f"{c.rule_name}｜{c.requirement}｜{c.recommended_action}" for c in muni_items)
    for needle in [
        "路地状敷地",
        "接道",
        "耐火建築物",
        "竪穴区画",
        "異種用途区画",
        "非常用照明",
        "容積緩和",              # 共同住宅→旅館で容積緩和が外れる
        "東京都建築安全条例",
        "既存不適格",            # 既存遡及（法87条2〜4項）
        "200㎡",                 # 200㎡以下でも建基法適合は必要
        "1959",                  # 記録照会の起点
    ]:
        assert needle in blob, f"建基法チェックに '{needle}' がない"
    # 事業化不可条件は impact=high で出す
    blocking = [c for c in muni_items if c.rule_id.startswith("muni_block_")]
    assert blocking and all(c.impact == "high" for c in blocking)
    print(f"✅ 建基法チェックに目黒区の注意点{len(muni_items)}件が反映されている")


# ---------------------------------------------------------------------------
# TODO（窓口の電話番号つき）
# ---------------------------------------------------------------------------


def test_todos_include_meguro_contacts():
    r = _run()
    blob = "\n".join(f"{t.title}｜{t.description}" for t in r.todos)
    assert "03-5722-9637" in blob, "建築課の電話番号がTODOにない"
    assert "03-5722-9502" in blob, "保健所の電話番号がTODOにない"
    assert "sonicweb-asp.jp/meguro" in blob, "めぐろ地図情報サービスがTODOにない"
    # 事業化不可条件が最優先で先頭に来ている
    assert "最優先" in r.todos[0].title, r.todos[0].title
    assert r.todos[0].priority == "high"
    print("✅ TODOに目黒区の窓口・地図サービス・最優先確認が入っている")


# ---------------------------------------------------------------------------
# 用途地域判定（目黒区の実情）
# ---------------------------------------------------------------------------


def test_zoning_outcomes_for_meguro_demo_addresses():
    cases = [
        ("東京都目黒区下目黒1-1-1", 271.0, JudgmentLevel.GO),          # 商業地域
        ("東京都目黒区自由が丘1-25-9", 271.0, JudgmentLevel.GO),        # 商業地域
        ("東京都目黒区鷹番3-2-1", 271.0, JudgmentLevel.GO),            # 近隣商業
        ("東京都目黒区目黒本町3-1-1", 271.0, JudgmentLevel.GO),         # 二種住居
        ("東京都目黒区中目黒1-1-1", 271.0, JudgmentLevel.GO),          # 一種住居（3000㎡以下）
        ("東京都目黒区中目黒1-1-1", 3500.0, JudgmentLevel.NO_GO),      # 一種住居（3000㎡超）
        ("東京都目黒区青葉台2-1-1", 120.0, JudgmentLevel.NO_GO),       # 一種低層＝不可
        ("東京都目黒区駒場4-6-1", 120.0, JudgmentLevel.NO_GO),         # 一種中高層＝不可
    ]
    for addr, fa, expected in cases:
        r = _run(address=addr, fa=fa)
        assert r.zoning.level == expected, (addr, fa, r.zoning.level, r.zoning.reason)
    print("✅ 目黒区の用途地域判定が手引きどおり（一種住居は3000㎡が分岐）")


# ---------------------------------------------------------------------------
# 業態（簡易宿所・民泊）— KeyError クラッシュの回帰テスト
# ---------------------------------------------------------------------------


def test_simple_lodging_does_not_crash():
    """簡易宿所は建基法上「旅館・ホテル」用途として判定できる（旧実装はKeyErrorで落ちた）."""
    r = _run(bt=BusinessType.SIMPLE_LODGING, fa=90.0)
    assert r.zoning.level == JudgmentLevel.GO, r.zoning.reason
    blob = "\n".join(c.standard for c in r.lodging_business_checks)
    assert "有効面積1.5㎡につき1人" in blob, blob
    print("✅ 簡易宿所で判定が通る（定員1.5㎡/人が適用）")


def test_minpaku_is_not_restricted_by_zoning():
    """民泊は建基法上「住宅」なので用途地域の制限を受けない（＝GO）.

    旧実装は KeyError で落ち、その後「本判定の対象外」として CONDITIONAL を
    返していた。しかし住宅宿泊事業は用途地域にかかわらず実施できるのが原則で、
    可否を決めるのは住宅宿泊事業法18条の条例なので、そう判定するのが正しい。
    """
    r = _run(bt=BusinessType.MINPAKU, fa=90.0)
    assert r.zoning.level == JudgmentLevel.GO, r.zoning.reason
    assert "住宅宿泊事業" in r.zoning.reason, r.zoning.reason
    assert "18条" in r.zoning.reason, r.zoning.reason
    print("✅ 民泊は用途地域で制限されない（条例が可否を決めると明示）")


def test_minpaku_allowed_in_low_rise_residential():
    """旅館業がNGの第一種低層住居専用地域でも、民泊は用途地域では落ちない."""
    hotel = _run(address="東京都目黒区八雲1-1-1", bt=BusinessType.HOTEL_RYOKAN, fa=90.0)
    minpaku = _run(address="東京都目黒区八雲1-1-1", bt=BusinessType.MINPAKU, fa=90.0)
    # 八雲は一種低層とは限らないので、用途地域が一種低層のときだけ意味のある比較になる
    if hotel.geo.zoning_code == "first_low_residential":
        assert hotel.zoning.level == JudgmentLevel.NO_GO
        assert minpaku.zoning.level == JudgmentLevel.GO
        print("✅ 一種低層で 旅館業=NO_GO / 民泊=GO に分岐する")
    else:
        print("⏭️ 一種低層のサンプル住所が取れないためスキップ")


# ---------------------------------------------------------------------------
# 定員（条例の法令上限）と収益計算への反映
# ---------------------------------------------------------------------------


def test_capacity_uses_ordinance_and_never_exceeds_it():
    r = _run(fa=271.0)
    res = profitability.compute(r, overrides={})
    assert res["municipality_name"] == "目黒区"
    assert res["municipality_detail_level"] == "full"
    assert res["capacity_legal_max"] is not None
    # 収益計算に使う定員は法令上限を超えない
    assert res["capacity_est"] <= res["capacity_legal_max"], (
        res["capacity_est"], res["capacity_legal_max"])
    # 実務目安と法令上限の小さい方
    assert res["capacity_est"] == min(res["capacity_practical"], res["capacity_legal_max"])
    # 簡易宿所（1.5㎡/人）は旅館ホテル（3.0㎡/人）より法令上限が大きい
    res_s = profitability.compute(_run(bt=BusinessType.SIMPLE_LODGING, fa=271.0), overrides={})
    assert res_s["capacity_legal_max"] > res["capacity_legal_max"], (
        res_s["capacity_legal_max"], res["capacity_legal_max"])
    print("✅ 定員は目黒区条例の法令上限を超えず、業態で上限が変わる")


def test_generic_municipality_has_no_legal_cap():
    """当て込み自治体では法令上限を勝手に作らない（数字の捏造防止）."""
    res = profitability.compute(_run(address="東京都新宿区西新宿2-8-1"), overrides={})
    assert res["capacity_legal_max"] is None, res["capacity_legal_max"]
    assert res["municipality_detail_level"] == "generic"
    print("✅ 条例データのない自治体では法令上限を作らない")


# ---------------------------------------------------------------------------
# レポート出力
# ---------------------------------------------------------------------------


def test_reports_contain_meguro_content():
    r = _run()
    r.profitability = profitability.compute(r, overrides={"land_area_m2": 180.0,
                                                         "purchase_price_man": 9000})
    md = generate_markdown_report(r)
    html = generate_html_report(r)
    for needle in ["路地状敷地", "文教地区", "完全無人運営は不可", "03-5722-9502",
                   "30,600", "既存不適格", "sonicweb-asp.jp/meguro"]:
        assert needle in md, f"Markdownに '{needle}' がない"
        assert needle in html, f"HTMLに '{needle}' がない"
    print("✅ Markdown/HTMLレポートに目黒区の内容が載る")


if __name__ == "__main__":
    test_detects_meguro_as_detailed()
    test_meguro_rule_contents()
    test_lodging_checks_include_meguro_specifics()
    test_generic_municipality_has_no_meguro_items()
    test_building_checks_include_meguro_notes()
    test_todos_include_meguro_contacts()
    test_zoning_outcomes_for_meguro_demo_addresses()
    test_simple_lodging_does_not_crash()
    test_minpaku_is_not_restricted_by_zoning()
    test_minpaku_allowed_in_low_rise_residential()
    test_capacity_uses_ordinance_and_never_exceeds_it()
    test_generic_municipality_has_no_legal_cap()
    test_reports_contain_meguro_content()
    print("\n🎉 目黒区テスト 全パス")
