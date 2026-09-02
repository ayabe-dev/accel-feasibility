"""旅館業許可の可否判定（core/license_gate.py）のテスト.

実行： python3 tests/test_license_gate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.application_check import judge_application_requirement  # noqa: E402
from core.building_code_check import run_building_code_checks  # noqa: E402
from core.evidence import PRIMARY_SOURCES, SourceTier  # noqa: E402
from core.legal_research import ResearchResult, _is_verified_finding  # noqa: E402
from core.license_gate import (  # noqa: E402
    GateStatus,
    LicenseVerdict,
    judge_license,
)
from core.models import (  # noqa: E402
    BusinessType,
    CheckStatus,
    GeoLookupResult,
    ProjectInput,
)
from core.zoning import judge_zoning  # noqa: E402


def _judge(
    bt=BusinessType.HOTEL_RYOKAN,
    zoning_code="first_residential",
    fa=92.54,
    floors=3,
    structure="木造",
    insp=None,
    conversion=None,
    rooms=None,
    address="東京都目黒区中目黒1-1-1",
    city_planning="市街化区域",
    research=None,
    nearby=None,
):
    project = ProjectInput(
        address=address,
        business_type=bt,
        floor_area_m2=fa,
        floors_above=floors,
        structure=structure,
        has_inspection_certificate=insp,
        conversion_area_m2=conversion,
        guest_room_count=rooms,
    )
    geo = GeoLookupResult(
        address=address,
        zoning_code=zoning_code,
        zoning_name=zoning_code,
        fire_district="quasi_fire_district",
        city_planning_classification=city_planning,
        nearby_facilities=nearby or [],
    )
    zoning = judge_zoning(geo, project)
    return project, geo, judge_license(
        project, geo, zoning, municipality_key="meguro_ku",
        municipality_name="目黒区", research=research,
    )


def _gate(j, gate_id):
    return next(g for g in j.gates if g.gate_id == gate_id)


# ---------------------------------------------------------------------------
# 法27条：令和元年の緩和
# ---------------------------------------------------------------------------


def test_law27_relaxation_applies_to_small_3story():
    """3階建・延べ200㎡未満の木造は、耐火不適合ではなく『警報設備で対応可』."""
    _, _, j = _judge(fa=92.54, floors=3, structure="木造")
    g = _gate(j, "B2_fire_resistance")
    assert g.status == GateStatus.CONDITIONAL, g.status
    assert "警報設備" in g.finding
    assert j.verdict != LicenseVerdict.DIFFICULT, j.verdict
    print("✅ 3階建200㎡未満の木造に法27条の緩和が効く（耐火改修を前提にしない）")


def test_law27_still_fails_when_over_200m2():
    """同じ3階建でも200㎡以上なら緩和は効かず不適合."""
    _, _, j = _judge(fa=250.0, floors=3, structure="木造")
    g = _gate(j, "B2_fire_resistance")
    assert g.status == GateStatus.FAIL, g.status
    assert j.verdict == LicenseVerdict.DIFFICULT, j.verdict
    print("✅ 3階建でも200㎡以上なら緩和は効かない")


def test_law27_not_applicable_for_4story():
    """4階建は緩和の対象外（階数3が条件）."""
    _, _, j = _judge(fa=150.0, floors=4, structure="木造")
    assert _gate(j, "B2_fire_resistance").status == GateStatus.FAIL
    print("✅ 4階建は緩和の対象外")


def test_law27_relaxation_reflected_in_building_code_check():
    """Phase4の建基法チェック側にも同じ緩和が入っている（二重管理の齟齬防止）."""
    p = ProjectInput(
        address="東京都目黒区中目黒1-1-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=92.54,
        floors_above=3,
        structure="木造",
    )
    geo = GeoLookupResult(address=p.address, zoning_code="first_residential")
    checks = run_building_code_checks(p, geo, "meguro_ku")
    law27 = next(c for c in checks if c.rule_id == "law_27")
    assert law27.status == CheckStatus.NEEDS_REVIEW, law27.status
    assert "警報設備" in law27.requirement or "警報設備" in law27.recommended_action
    print("✅ 建基法チェック側にも法27条の緩和が反映されている")


# ---------------------------------------------------------------------------
# 立地で落ちる場合と別ルート
# ---------------------------------------------------------------------------


def test_low_rise_residential_blocks_and_offers_minpaku():
    """一種低層は旅館業も簡易宿所も不可。民泊だけが残る."""
    _, _, j = _judge(zoning_code="first_low_residential")
    assert j.verdict == LicenseVerdict.BLOCKED, j.verdict
    assert _gate(j, "A1_zoning").hard is True

    alts = {a.business_type: a for a in j.alternatives}
    assert alts[BusinessType.SIMPLE_LODGING].verdict == LicenseVerdict.BLOCKED
    assert "同じ" in alts[BusinessType.SIMPLE_LODGING].summary
    assert alts[BusinessType.MINPAKU].verdict != LicenseVerdict.BLOCKED
    print("✅ 一種低層：旅館業も簡易宿所も不可、民泊ルートだけが残る")


def test_urbanization_control_area_is_blocked():
    _, _, j = _judge(zoning_code="commercial", city_planning="市街化調整区域")
    assert j.verdict == LicenseVerdict.BLOCKED
    assert _gate(j, "A2_city_planning").status == GateStatus.FAIL
    print("✅ 市街化調整区域は不可")


def test_school_nearby_moves_verdict_to_consult():
    """学校等100m以内は即不許可ではないが、行政判断（意見聴取）に依存する."""
    from core.models import NearbyFacility

    _, _, j = _judge(
        zoning_code="commercial",
        floors=2,
        fa=150.0,
        structure="RC造",
        insp=True,
        nearby=[
            NearbyFacility(
                name="区立第一小学校", facility_type="小学校", distance_m=60.0
            )
        ],
    )
    g = _gate(j, "A3_school_distance")
    assert g.status == GateStatus.CONSULT, g.status
    assert "即不許可ではない" in g.finding
    assert j.verdict == LicenseVerdict.CONSULT, j.verdict
    print("✅ 学校100m以内は CONSULT（意見聴取が必要・即不許可ではない）")


# ---------------------------------------------------------------------------
# 200㎡判定は「用途変更部分」で行う
# ---------------------------------------------------------------------------


def test_conversion_area_drives_200m2_threshold():
    """延床400㎡でも、宿泊にするのが150㎡なら確認申請は不要."""
    _, _, j = _judge(
        zoning_code="commercial", fa=400.0, conversion=150.0, floors=2,
        structure="RC造", insp=True,
    )
    g = _gate(j, "B1_use_change")
    assert g.status == GateStatus.PASS, g.status
    assert "200㎡以下" in g.finding
    assert "用途変更部分 150.0㎡" in g.finding
    print("✅ 200㎡判定に用途変更部分の面積を使う（延床ではない）")


def test_conversion_area_used_by_application_check():
    """Phase3の確認申請判定も用途変更部分の面積を使う."""
    p = ProjectInput(
        address="東京都目黒区中目黒1-1-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=400.0,
        conversion_area_m2=150.0,
    )
    geo = GeoLookupResult(address=p.address, zoning_code="commercial")
    zoning = judge_zoning(geo, p)
    req = judge_application_requirement(p, geo, zoning)
    assert req.required is False, req.reason
    assert req.floor_area_subject_m2 == 150.0
    print("✅ 確認申請判定も用途変更部分の面積で行う")


# ---------------------------------------------------------------------------
# 簡易宿所は特殊建築物（回帰テスト）
# ---------------------------------------------------------------------------


def test_simple_lodging_is_special_building():
    """簡易宿所も建基法上は『ホテル又は旅館』＝特殊建築物.

    ここから漏れると『特殊建築物でない＝確認申請不要』と誤判定する。
    """
    p = ProjectInput(
        address="東京都目黒区中目黒1-1-1",
        business_type=BusinessType.SIMPLE_LODGING,
        floor_area_m2=400.0,
    )
    geo = GeoLookupResult(address=p.address, zoning_code="commercial")
    zoning = judge_zoning(geo, p)
    req = judge_application_requirement(p, geo, zoning)
    assert req.is_special_building is True
    assert req.required is True, req.reason
    print("✅ 簡易宿所は特殊建築物として扱われ、400㎡なら確認申請が必要になる")


def test_minpaku_needs_no_use_change_application():
    """民泊は建基法上『住宅』なので用途変更にあたらない."""
    p = ProjectInput(
        address="東京都目黒区中目黒1-1-1",
        business_type=BusinessType.MINPAKU,
        floor_area_m2=400.0,
    )
    geo = GeoLookupResult(address=p.address, zoning_code="first_low_residential")
    zoning = judge_zoning(geo, p)
    req = judge_application_requirement(p, geo, zoning)
    assert req.required is False
    assert req.is_special_building is False
    assert "住宅" in req.reason
    print("✅ 民泊は用途変更確認申請が不要（建基法上「住宅」）")


# ---------------------------------------------------------------------------
# 簡易宿所の客室面積基準
# ---------------------------------------------------------------------------


def test_simple_lodging_room_area_uses_33m2_standard():
    _, _, j = _judge(bt=BusinessType.SIMPLE_LODGING, zoning_code="commercial", fa=90.0)
    g = _gate(j, "C1_room_area")
    assert "33" in g.finding
    assert "施行令1条2項" in g.title
    print("✅ 簡易宿所は客室延床33㎡基準で判定される")


def test_hotel_room_area_uses_7m2_standard():
    _, _, j = _judge(zoning_code="commercial", fa=90.0, rooms=3)
    g = _gate(j, "C1_room_area")
    assert "7㎡" in g.finding
    assert "1室あたり約" in g.finding  # 客室数が入ったら概算を出す
    print("✅ 旅館・ホテル営業は1室7㎡基準で判定される")


# ---------------------------------------------------------------------------
# 民泊を主業態にした場合
# ---------------------------------------------------------------------------


def test_minpaku_primary_judgment():
    _, _, j = _judge(bt=BusinessType.MINPAKU, zoning_code="first_low_residential")
    assert _gate(j, "M1_zoning").status == GateStatus.PASS
    assert j.verdict in (LicenseVerdict.CONDITIONAL, LicenseVerdict.UNKNOWN)
    ids = {g.gate_id for g in j.gates}
    assert {"M1_zoning", "M2_ordinance", "M3_days", "M4_safety", "M5_management"} <= ids
    print("✅ 民泊を主業態にすると届出ベースのゲートに切り替わる")


def test_minpaku_zero_day_ordinance_blocks():
    """条例で禁止されている自治体では民泊ルートも塞がる（ゼロ日規制）."""
    research = ResearchResult(
        municipality_key="x",
        municipality_name="テスト市",
        researched_on="2026-08-31",
        minpaku={
            "status": "prohibited",
            "restriction_summary": "全域で住宅宿泊事業を実施できない",
            "max_days_per_year": 0,
            "url": "https://www.city.example.lg.jp/minpaku",
        },
    )
    _, _, j = _judge(
        bt=BusinessType.MINPAKU, zoning_code="first_low_residential", research=research
    )
    assert j.verdict == LicenseVerdict.BLOCKED, j.verdict
    print("✅ ゼロ日規制の自治体では民泊も BLOCKED になる")


# ---------------------------------------------------------------------------
# 検証ガード：未検証の調査結果は判定を動かさない
# ---------------------------------------------------------------------------


def test_unverified_research_finding_is_not_used_for_judgment():
    """出典URL・原文引用がない所見は、blocking でも判定を動かしてはいけない."""
    bad = ResearchResult(
        municipality_key="x",
        municipality_name="テスト市",
        researched_on="2026-08-31",
        findings=[
            {
                "topic": "zoning_local",
                "title": "文教地区でホテル禁止",
                "requirement": "ホテルは建築できない",
                "impact": "blocking",
                "law_name": "テスト市建築条例",
                "article": "第3条",
                "quote": "",  # 原文引用なし
                "url": "https://example.com/blog",  # 公的ドメインでもない
                "publisher": "個人ブログ",
            }
        ],
    )
    assert bad.blocking_findings(["zoning_local"]) == []
    _, _, j = _judge(zoning_code="commercial", research=bad)
    assert _gate(j, "A2_city_planning").status != GateStatus.FAIL
    print("✅ 未検証の所見は blocking でも判定を動かさない")


def test_verified_research_finding_blocks():
    """原文引用＋公的ドメインURLが揃った所見は判定を動かす."""
    good = ResearchResult(
        municipality_key="x",
        municipality_name="テスト市",
        researched_on="2026-08-31",
        findings=[
            {
                "topic": "zoning_local",
                "title": "文教地区内はホテル・旅館の建築不可",
                "requirement": "文教地区内ではホテル又は旅館を建築できない",
                "impact": "blocking",
                "law_name": "テスト市文教地区建築条例",
                "article": "第3条第1項",
                "quote": "文教地区内においては、ホテル又は旅館を建築してはならない。",
                "url": "https://www.city.test.lg.jp/reiki/bunkyo.html",
                "publisher": "テスト市例規集",
            }
        ],
    )
    assert len(good.blocking_findings(["zoning_local"])) == 1
    _, _, j = _judge(zoning_code="commercial", research=good)
    g = _gate(j, "A2_city_planning")
    assert g.status == GateStatus.FAIL, g.status
    assert j.verdict == LicenseVerdict.BLOCKED
    print("✅ 検証済みの条例所見は判定を動かす（文教地区で BLOCKED）")


def test_trusted_domain_check():
    assert _is_verified_finding(
        {"quote": "あ", "url": "https://www.city.meguro.tokyo.lg.jp/x.html"}
    )
    assert _is_verified_finding({"quote": "あ", "url": "https://laws.e-gov.go.jp/law/1"})
    assert not _is_verified_finding({"quote": "あ", "url": "https://note.com/x"})
    assert not _is_verified_finding({"quote": "", "url": "https://www.city.a.lg.jp/x"})
    print("✅ 信頼ドメイン＋原文引用の両方が揃わないと検証済みにならない")


# ---------------------------------------------------------------------------
# 一次情報レジストリの健全性
# ---------------------------------------------------------------------------


def test_primary_sources_are_self_consistent():
    """verified=True を名乗る根拠は、必ず出典URLと原文引用を持つこと."""
    for key, ev in PRIMARY_SOURCES.items():
        if ev.source_tier == SourceTier.UNVERIFIED:
            assert not ev.verified, f"{key}: UNVERIFIED なのに verified=True"
            continue
        assert ev.url, f"{key}: 出典URLがない"
        assert ev.quote, f"{key}: 原文引用がない"
        assert ev.checked_on, f"{key}: 確認日がない"
        assert ev.verified, f"{key}: verified にならない"
    print(f"✅ 一次情報レジストリ {len(PRIMARY_SOURCES)}件がスキーマどおり")


def test_every_blocking_gate_has_evidence():
    """総合判定を動かすゲートは、必ず根拠を持つこと."""
    for bt in (
        BusinessType.HOTEL_RYOKAN,
        BusinessType.SIMPLE_LODGING,
        BusinessType.MINPAKU,
    ):
        _, _, j = _judge(bt=bt, zoning_code="commercial")
        for g in j.gates:
            if g.blocking:
                assert g.evidences, f"{bt.value} / {g.gate_id} に根拠がない"
    print("✅ 判定を動かす全ゲートに根拠がぶら下がっている")


def test_judgment_confidence_and_completeness():
    _, _, j = _judge(zoning_code="commercial", fa=150.0, floors=2, structure="RC造")
    assert 0.0 <= j.data_completeness <= 1.0
    assert j.confidence in ("高", "中", "低")
    assert j.next_actions
    print(
        f"✅ 充足率 {j.data_completeness} / 信頼度 {j.confidence} / "
        f"次アクション {len(j.next_actions)}件 を出力する"
    )


# ---------------------------------------------------------------------------
# 出力との統合（スコア・レポート）
# ---------------------------------------------------------------------------


def test_score_is_blocked_when_license_is_blocked():
    """許可が下りない物件で、スコアだけ良く見えることがあってはいけない."""
    from core import judgment
    from core.scoring import compute_score

    p = ProjectInput(
        address="東京都目黒区青葉台2-1-1",  # 一種低層
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=92.54,
        floors_above=3,
        structure="木造",
        has_inspection_certificate=True,
    )
    r = judgment.run(project=p, docs=[])
    assert r.license_judgment.verdict == LicenseVerdict.BLOCKED
    score = compute_score(r)
    assert score.blocked is True, "許可が下りないのにスコアが出ている"
    print("✅ 許可BLOCKEDならスコアもブロックされる（判定とスコアが矛盾しない）")


def test_license_verdict_is_scored_as_an_item():
    from core import judgment
    from core.scoring import compute_score

    p = ProjectInput(
        address="東京都目黒区下目黒1-1-1",  # 商業
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=150.0,
        floors_above=2,
        structure="RC造",
        has_inspection_certificate=True,
    )
    r = judgment.run(project=p, docs=[])
    score = compute_score(r)
    assert not score.blocked
    item = next((i for i in score.items if i.key == "license_verdict"), None)
    assert item is not None, "スコアに許可判定の項目がない"
    assert item.weight >= 10.0, "許可判定は主判定なので最大重みであるべき"
    print(f"✅ 許可判定がスコア項目に入る（{item.score:.0f}点 × 重み{item.weight}）")


def test_reports_carry_the_license_section():
    """Markdown / HTML の両方に許可判定が載る（PDF出力はHTML経由）."""
    from core import judgment
    from core.report_generator import generate_markdown_report
    from core.report_html import generate_html_report

    p = ProjectInput(
        address="東京都目黒区中目黒1-1-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=92.54,
        floors_above=3,
        structure="木造",
        has_inspection_certificate=True,
    )
    r = judgment.run(project=p, docs=[])
    md = generate_markdown_report(r)
    html = generate_html_report(r)
    for doc, name in ((md, "Markdown"), (html, "HTML")):
        assert "旅館業許可の可否" in doc, f"{name}に許可判定がない"
        assert "許可までに越えるゲート" in doc, f"{name}にゲート一覧がない"
        assert "e-Gov" in doc, f"{name}に一次情報の出典がない"
    print("✅ Markdown・HTML の両方に許可判定と一次情報の出典が載る")


if __name__ == "__main__":
    test_law27_relaxation_applies_to_small_3story()
    test_law27_still_fails_when_over_200m2()
    test_law27_not_applicable_for_4story()
    test_law27_relaxation_reflected_in_building_code_check()
    test_low_rise_residential_blocks_and_offers_minpaku()
    test_urbanization_control_area_is_blocked()
    test_school_nearby_moves_verdict_to_consult()
    test_conversion_area_drives_200m2_threshold()
    test_conversion_area_used_by_application_check()
    test_simple_lodging_is_special_building()
    test_minpaku_needs_no_use_change_application()
    test_simple_lodging_room_area_uses_33m2_standard()
    test_hotel_room_area_uses_7m2_standard()
    test_minpaku_primary_judgment()
    test_minpaku_zero_day_ordinance_blocks()
    test_unverified_research_finding_is_not_used_for_judgment()
    test_verified_research_finding_blocks()
    test_trusted_domain_check()
    test_primary_sources_are_self_consistent()
    test_every_blocking_gate_has_evidence()
    test_judgment_confidence_and_completeness()
    test_score_is_blocked_when_license_is_blocked()
    test_license_verdict_is_scored_as_an_item()
    test_reports_carry_the_license_section()
    print("\n🎉 許可判定テスト 全パス")
