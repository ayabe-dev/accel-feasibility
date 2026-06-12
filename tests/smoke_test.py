"""スモークテスト. 主要シナリオで判定ロジックが期待通り動くか検証."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import judgment
from core.models import BusinessType, ExtractedDocument, DocumentType, ProjectInput


def test_no_go_case() -> None:
    """成城（第一種低層住居専用）→ 旅館・ホテル は NO-GO"""
    p = ProjectInput(
        address="東京都世田谷区成城6-5-34",
        business_type=BusinessType.HOTEL_RYOKAN,
    )
    r = judgment.run(project=p, docs=[])
    assert r.overall_level.value == "NO_GO", f"期待: NO_GO 実際: {r.overall_level}"
    assert "低層住居専用" in r.zoning.reason
    print(f"✅ NO-GOケース: {r.overall_summary}")


def test_go_case() -> None:
    """新宿（商業地域）→ 旅館・ホテル は GO/CONDITIONAL（書類なしのため）"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
    )
    r = judgment.run(project=p, docs=[])
    assert r.zoning.level.value in ("GO",), f"期待: GO 実際: {r.zoning.level}"
    assert r.geo.zoning_name == "商業地域"
    print(f"✅ 商業地域ケース: 用途地域={r.geo.zoning_name}, 総合={r.overall_level.value}")


def test_first_residential_over_3000m2() -> None:
    """第一種住居地域 + 床面積3500㎡ → NO-GO"""
    p = ProjectInput(
        address="東京都目黒区中目黒1-1-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=3500.0,
    )
    r = judgment.run(project=p, docs=[])
    assert r.overall_level.value == "NO_GO"
    assert "上限" in r.zoning.reason
    print(f"✅ 第一種住居3000㎡超: {r.overall_summary}")


def test_first_residential_under_3000m2() -> None:
    """第一種住居地域 + 床面積1500㎡ → GO"""
    p = ProjectInput(
        address="東京都目黒区中目黒1-1-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=1500.0,
        has_inspection_certificate=True,
        built_year=2018,
    )
    r = judgment.run(project=p, docs=[])
    assert r.zoning.level.value == "GO"
    assert r.pattern.value == "A"
    print(f"✅ 第一種住居1500㎡(築浅): パターン={r.pattern.value}, "
          f"費用={r.cost_estimate.total_cost_min}〜{r.cost_estimate.total_cost_max}万円")


def test_pattern_d_no_inspection() -> None:
    """検査済証なし → パターン D"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        has_inspection_certificate=False,
        floor_area_m2=500.0,
    )
    r = judgment.run(project=p, docs=[])
    assert r.pattern.value == "D"
    print(
        f"✅ 検査済証なし: パターン={r.pattern.value}, "
        f"期間={r.cost_estimate.total_months_min}〜{r.cost_estimate.total_months_max}か月, "
        f"費用={r.cost_estimate.total_cost_min:,}〜{r.cost_estimate.total_cost_max:,}万円"
    )


def test_pattern_c_old_building() -> None:
    """築古（築40年）+ 検査済証あり → パターン C"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        has_inspection_certificate=True,
        built_year=1985,
        structure="RC造",
        floor_area_m2=800.0,
    )
    r = judgment.run(project=p, docs=[])
    assert r.pattern.value == "C"
    print(f"✅ 築古ケース: パターン={r.pattern.value}, 注意={r.warnings}")


def test_milestone_seismic() -> None:
    """1980年建築（新耐震前）の警告が出る"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        has_inspection_certificate=True,
        built_year=1979,
        structure="RC造",
    )
    r = judgment.run(project=p, docs=[])
    has_seismic_flag = any("新耐震" in w for w in r.warnings)
    assert has_seismic_flag, f"新耐震フラグなし: {r.warnings}"
    print(f"✅ 新耐震フラグ確認")


def test_doc_enrichment() -> None:
    """書類抽出から床面積を補完できる"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
    )
    docs = [
        ExtractedDocument(
            file_name="検査済証.pdf",
            document_type=DocumentType.INSPECTION_CERTIFICATE,
            confidence=0.95,
            extracted_fields={
                "total_floor_area_m2": 1200.0,
                "structure": "RC造",
                "built_year": 2020,  # 築浅でパターンA期待
            },
        )
    ]
    r = judgment.run(project=p, docs=docs)
    assert r.input.floor_area_m2 == 1200.0, f"floor_area: {r.input.floor_area_m2}"
    assert r.input.built_year == 2020, f"built_year: {r.input.built_year}"
    assert r.pattern.value == "A", f"pattern: {r.pattern.value}"
    print(
        f"✅ 書類抽出での補完OK: 床面積={r.input.floor_area_m2}㎡, "
        f"築年={r.input.built_year}, パターン={r.pattern.value}"
    )


def test_missing_documents() -> None:
    """不足書類が正しくTODO化される"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        has_inspection_certificate=False,
    )
    r = judgment.run(project=p, docs=[])
    assert len(r.missing_documents) > 0
    assert len(r.todos) > 0
    print(
        f"✅ 不足書類検出: {len(r.missing_documents)}件, "
        f"TODO: {len(r.todos)}件"
    )


def test_myso_flow() -> None:
    """マイソク（フラッツ目黒南）相当のデータで全体フロー検証"""
    p = ProjectInput(
        address="東京都目黒区南三丁目12-5",
        business_type=BusinessType.HOTEL_RYOKAN,
    )
    docs = [
        ExtractedDocument(
            file_name="フラッツ目黒南_マイソク.pdf",
            document_type=DocumentType.REAL_ESTATE_FLYER,
            confidence=0.95,
            extracted_fields={
                "address": "東京都目黒区南三丁目12-5",
                "structure": "鉄筋コンクリート造陸屋根4階建",
                "floors_above": 4,
                "total_floor_area_m2": 256.63,
                "site_area_m2": 174.8,
                "zoning": "第二種住居地域",
                "fire_district": "準防火地域",
                "coverage_ratio_pct": 60,
                "floor_area_ratio_pct": 300,
                "city_planning": "市街化区域",
                "built_year": 1989,
                "built_month": 8,
                "inspection_obtained": True,
                "property_type": "共同住宅",
                "total_units": 6,
                "price_jpy_man": 22000,
            },
        )
    ]
    r = judgment.run(project=p, docs=docs)
    assert r.input.floor_area_m2 == 256.63
    assert r.input.structure == "RC造"  # 構造正規化
    assert r.input.built_year == 1989
    assert r.input.has_inspection_certificate is True
    assert r.geo.zoning_code == "second_residential"
    assert r.geo.fire_district == "quasi_fire_district"
    assert r.geo.source == "document"
    assert r.zoning.level.value == "GO"
    print(
        f"✅ マイソクから判定: 用途地域={r.geo.zoning_name} ({r.geo.source}), "
        f"パターン={r.pattern.value}, 申請={'必要' if r.application_requirement.required else '不要'}"
    )


def test_phase3_application_required() -> None:
    """200㎡超 + 住宅→旅館 → 確認申請必要"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=500.0,
        has_inspection_certificate=True,
    )
    r = judgment.run(project=p, docs=[])
    assert r.application_requirement.required is True
    assert "200㎡" in r.application_requirement.reason
    print(f"✅ Phase 3 申請必要: {r.application_requirement.reason[:60]}")


def test_phase3_application_not_required() -> None:
    """200㎡以下 → 確認申請不要"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=180.0,
        has_inspection_certificate=True,
    )
    r = judgment.run(project=p, docs=[])
    assert r.application_requirement.required is False
    assert "200" in r.application_requirement.reason
    print(f"✅ Phase 3 申請不要: {r.application_requirement.reason[:60]}")


def test_phase4_law27_high_building() -> None:
    """3階建で非耐火 → 法27条 NON_COMPLIANT"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=500.0,
        floors_above=4,
        structure="木造",
    )
    r = judgment.run(project=p, docs=[])
    law27 = next(c for c in r.building_code_checks if c.rule_id == "law_27")
    assert law27.status.value == "non_compliant"
    print(f"✅ Phase 4 法27条 NG検出: {law27.recommended_action[:50]}")


def test_phase5_fire_alarm_required() -> None:
    """自火報は旅館全件必要"""
    p = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=200.0,
    )
    r = judgment.run(project=p, docs=[])
    afa = next(c for c in r.fire_safety_checks if c.rule_id == "auto_fire_alarm")
    assert afa.required is True
    print(f"✅ Phase 5 自火報: {afa.action[:50]}")


def test_municipality_detection() -> None:
    """住所から自治体自動判定"""
    p = ProjectInput(
        address="京都府京都市東山区祇園町南側",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=300.0,
    )
    r = judgment.run(project=p, docs=[])
    school_check = next(
        c for c in r.lodging_business_checks if c.rule_id == "distance_to_school"
    )
    assert "京都市" in school_check.standard
    print(f"✅ 自治体自動判定: {school_check.standard[:80]}")


def test_structure_cost_multiplier() -> None:
    """RC造は木造より改修コスト高め"""
    base = ProjectInput(
        address="東京都新宿区西新宿2-8-1",
        business_type=BusinessType.HOTEL_RYOKAN,
        floor_area_m2=500.0,
        has_inspection_certificate=True,
        built_year=2020,
    )
    r_wood = judgment.run(project=base.model_copy(update={"structure": "木造"}), docs=[])
    r_rc = judgment.run(project=base.model_copy(update={"structure": "RC造"}), docs=[])
    assert r_rc.cost_estimate.renovation_cost_max > r_wood.cost_estimate.renovation_cost_max
    print(
        f"✅ 構造係数: 木造{r_wood.cost_estimate.renovation_cost_max:,}万 "
        f"< RC造{r_rc.cost_estimate.renovation_cost_max:,}万"
    )


def main() -> None:
    print("=" * 60)
    print("Phase 1+2+3+4+5 MVP スモークテスト")
    print("=" * 60)
    tests = [
        test_no_go_case,
        test_go_case,
        test_first_residential_over_3000m2,
        test_first_residential_under_3000m2,
        test_pattern_d_no_inspection,
        test_pattern_c_old_building,
        test_milestone_seismic,
        test_doc_enrichment,
        test_missing_documents,
        test_myso_flow,
        test_phase3_application_required,
        test_phase3_application_not_required,
        test_phase4_law27_high_building,
        test_phase5_fire_alarm_required,
        test_municipality_detection,
        test_structure_cost_multiplier,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"❌ {t.__name__} で予期せぬエラー: {e}")
            failed += 1
    print("=" * 60)
    if failed == 0:
        print(f"🎉 全 {len(tests)} テスト合格")
    else:
        print(f"⚠️  {failed} / {len(tests)} テスト失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
