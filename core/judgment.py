"""総合判定オーケストレータ.

入力：ProjectInput + アップロード書類リスト
出力：FeasibilityReport（全部入り）

各サブモジュールを束ねて、Streamlit UI に表示する最終JSONを組み立てる.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from api import gis_client

from .application_check import judge_application_requirement
from .building_code_check import run_building_code_checks
from .context_interpreter import interpret_context
from .distance import check_distance_regulation
from .estimator import estimate, scale_notes
from .fire_safety_check import run_fire_safety_checks
from .lodging_business_check import run_lodging_business_checks
from .municipality import detect_municipality
from .models import (
    DistanceCheckResult,
    ExtractedDocument,
    FeasibilityReport,
    GeoLookupResult,
    InvestigationPattern,
    JudgmentLevel,
    ProjectInput,
)
from .pattern_classifier import classify_pattern
from .todo_generator import generate_todos, missing_documents
from .zoning import fire_district_note, judge_zoning

# 用途地域名 → 内部コード変換用（マイソク等から抽出した日本語名を変換）
ZONING_NAME_TO_CODE = {
    "第一種低層住居専用地域": "first_low_residential",
    "第二種低層住居専用地域": "second_low_residential",
    "第一種中高層住居専用地域": "first_mid_residential",
    "第二種中高層住居専用地域": "second_mid_residential",
    "第一種住居地域": "first_residential",
    "第二種住居地域": "second_residential",
    "準住居地域": "quasi_residential",
    "田園住居地域": "rural_residential",
    "近隣商業地域": "neighborhood_commercial",
    "商業地域": "commercial",
    "準工業地域": "quasi_industrial",
    "工業地域": "industrial",
    "工業専用地域": "exclusive_industrial",
}

FIRE_NAME_TO_CODE = {
    "防火地域": "fire_district",
    "準防火地域": "quasi_fire_district",
    "指定なし": "no_district",
    "なし": "no_district",
}


def run(
    project: ProjectInput,
    docs: List[ExtractedDocument],
    manual_geo: Optional[GeoLookupResult] = None,
    has_nearby_facility: Optional[bool] = None,
    nearby_facilities: Optional[List[str]] = None,
    municipality_key: Optional[str] = None,
) -> FeasibilityReport:
    """総合判定を実行."""
    # 1. 抽出書類から ProjectInput を補完（先に！住所も上書きあり得る）
    project = _enrich_project_from_docs(project, docs)

    # 2. GIS情報
    #    優先順位：手動入力 > 書類で完全に揃っている場合のみ書類 > GIS API
    doc_geo = _build_geo_from_docs(project.address, docs)
    if manual_geo is not None:
        geo = manual_geo
    elif doc_geo is not None and doc_geo.zoning_code:
        # 書類に用途地域コードが取れていれば優先
        geo = doc_geo
    else:
        # 書類に用途地域がない or 不完全 → GIS API で取得
        geo = gis_client.lookup(project.address)
        # 書類で取れた断片情報があれば補完（防火地域・建ぺい率・容積率）
        if doc_geo is not None:
            patches = {}
            if (not geo.fire_district or geo.fire_district == "no_district") and (
                doc_geo.fire_district and doc_geo.fire_district != "no_district"
            ):
                patches["fire_district"] = doc_geo.fire_district
            if geo.coverage_ratio_pct is None and doc_geo.coverage_ratio_pct is not None:
                patches["coverage_ratio_pct"] = doc_geo.coverage_ratio_pct
            if geo.floor_area_ratio_pct is None and doc_geo.floor_area_ratio_pct is not None:
                patches["floor_area_ratio_pct"] = doc_geo.floor_area_ratio_pct
            if patches:
                geo = geo.model_copy(update=patches)
                geo.notes.append("一部情報を書類から補完しました")

    # 3. 用途地域判定
    zoning = judge_zoning(geo, project)

    # 4. 自治体自動判定（明示指定がなければ住所から推定）
    if municipality_key is None:
        municipality_key = detect_municipality(project.address)

    # 5. 距離規制
    #    GIS API から取得した近接施設があれば優先（自動判定）
    auto_nearby_names: List[str] = []
    if geo.nearby_facilities:
        for f in geo.nearby_facilities:
            auto_nearby_names.append(
                f"{f.name}（{f.facility_type}, {f.distance_m:.0f}m）"
            )
        # API取得結果でユーザー入力を上書き
        has_nearby_facility = True
        nearby_facilities = auto_nearby_names
    distance = check_distance_regulation(
        has_nearby_facility=has_nearby_facility,
        nearby_facilities=nearby_facilities,
        municipality_key=municipality_key,
    )

    # 5. パターン判定
    pattern, pattern_reason, milestone_flags = classify_pattern(project, docs)

    # 6. 費用・期間見積
    cost = estimate(pattern, project)
    scale_comment = scale_notes(project)

    # 7. 総合レベル
    overall_level, overall_summary = _decide_overall_level(
        zoning=zoning, distance=distance, pattern=pattern
    )

    # 8. 不足書類 / TODO
    missing = missing_documents(pattern, docs)
    todos = generate_todos(
        pattern=pattern,
        overall_level=overall_level,
        zoning=zoning,
        missing_docs=missing,
        milestone_flags=milestone_flags,
    )

    # 9. 警告
    warnings: List[str] = []
    fd_note = fire_district_note(geo)
    if fd_note:
        warnings.append(fd_note)
    if scale_comment:
        warnings.append(scale_comment)
    warnings.extend(milestone_flags)

    # 10. Phase 3：用途変更確認申請要否判定
    application_req = judge_application_requirement(project, geo, zoning)

    # 11. Phase 4：建基法技術基準チェックリスト
    building_checks = run_building_code_checks(project, geo)

    # 12. Phase 5：消防法 + 旅館業法チェック
    fire_checks = run_fire_safety_checks(project)
    lodging_checks = run_lodging_business_checks(project, municipality_key)

    # 13. 追加情報の LLM 解釈
    context_impact = None
    if project.additional_context and project.additional_context.strip():
        is_pre_1981 = (
            project.built_year is not None and project.built_year < 1981
        )
        is_pre_wooden_2000 = (
            project.built_year is not None
            and project.built_year < 2000
            and project.structure is not None
            and "木" in project.structure
        )
        is_pre_2007_structural = (
            project.built_year is not None
            and project.built_year < 2007
            and project.structure is not None
            and any(s in project.structure for s in ["RC", "SRC", "鉄筋", "鉄骨"])
        )
        report_summary = {
            "overall_level": overall_level.value,
            "overall_summary": overall_summary,
            "pattern": pattern.value,
            "zoning_code": geo.zoning_code,
            "zoning_name": geo.zoning_name,
            "fire_district": geo.fire_district,
            "cost_total_max_man": cost.total_cost_max if cost else None,
            "months_max": cost.total_months_max if cost else None,
            "is_pre_1981_old_seismic": is_pre_1981,
            "is_pre_2000_wooden": is_pre_wooden_2000,
            "is_pre_2007_structural_calc": is_pre_2007_structural,
        }
        context_impact = interpret_context(
            additional_context=project.additional_context,
            project=project,
            report_summary=report_summary,
        )

    return FeasibilityReport(
        input=project,
        geo=geo,
        zoning=zoning,
        distance=distance,
        pattern=pattern,
        pattern_reason=pattern_reason,
        cost_estimate=cost,
        overall_level=overall_level,
        overall_summary=overall_summary,
        extracted_documents=docs,
        missing_documents=missing,
        todos=todos,
        warnings=warnings,
        application_requirement=application_req,
        building_code_checks=building_checks,
        fire_safety_checks=fire_checks,
        lodging_business_checks=lodging_checks,
        context_impact=context_impact,
    )


def _enrich_project_from_docs(
    project: ProjectInput, docs: List[ExtractedDocument]
) -> ProjectInput:
    """書類から ProjectInput を補完（未入力フィールドのみ）."""
    address = project.address
    floor_area = project.floor_area_m2
    floors_above = project.floors_above
    floors_below = project.floors_below
    structure = project.structure
    built_year = project.built_year
    has_inspection = project.has_inspection_certificate
    renovation_history = project.renovation_history

    for d in docs:
        f = d.extracted_fields

        # 住所（マイソクなら明確）
        if not address and f.get("address"):
            address = str(f["address"]).strip()

        # 床面積
        if floor_area is None:
            v = f.get("total_floor_area_m2")
            if isinstance(v, (int, float)):
                floor_area = float(v)

        # 階数
        if floors_above is None:
            v = f.get("floors_above")
            if isinstance(v, int):
                floors_above = v
        if floors_below is None:
            v = f.get("floors_below")
            if isinstance(v, int):
                floors_below = v

        # 構造
        if structure is None and f.get("structure"):
            structure = _normalize_structure(str(f["structure"]))

        # 築年
        if built_year is None:
            v = f.get("built_year")
            if isinstance(v, int):
                built_year = v
            else:
                date_str = f.get("confirmation_date") or f.get("inspection_date")
                if isinstance(date_str, str) and len(date_str) >= 4:
                    try:
                        built_year = int(date_str[:4])
                    except ValueError:
                        pass

        # 検査済証
        if has_inspection is None:
            v = f.get("inspection_obtained")
            if isinstance(v, bool):
                has_inspection = v
            elif isinstance(v, str):
                has_inspection = v.strip() in ("有", "あり", "true", "True", "○", "◯")

        # 改修履歴
        if renovation_history is None and f.get("renovation_history"):
            renovation_history = True

    return project.model_copy(
        update={
            "address": address,
            "floor_area_m2": floor_area,
            "floors_above": floors_above,
            "floors_below": floors_below,
            "structure": structure,
            "built_year": built_year,
            "has_inspection_certificate": has_inspection,
            "renovation_history": renovation_history,
        }
    )


def _normalize_structure(text: str) -> str:
    """マイソクの記述（鉄筋コンクリート造陸屋根4階建 等）から構造名を抽出."""
    s = text
    if "鉄骨鉄筋コンクリート" in s or "SRC" in s:
        return "SRC造"
    if "鉄筋コンクリート" in s or "RC" in s:
        return "RC造"
    if "鉄骨" in s or "S造" in s:
        return "S造"
    if "木造" in s or "W造" in s:
        return "木造"
    return s.strip()


def _build_geo_from_docs(
    address: str, docs: List[ExtractedDocument]
) -> Optional[GeoLookupResult]:
    """マイソク等の書類から用途地域・防火地域を構築. 一つでも取れたら GeoLookupResult を返す."""
    zoning_code: Optional[str] = None
    zoning_name: Optional[str] = None
    fire_district: Optional[str] = None
    coverage: Optional[float] = None
    far: Optional[float] = None
    notes: List[str] = []
    source_doc = None

    for d in docs:
        f = d.extracted_fields
        if zoning_code is None:
            name = f.get("zoning")
            if isinstance(name, str) and name.strip():
                cleaned = name.strip()
                code = ZONING_NAME_TO_CODE.get(cleaned)
                if code:
                    zoning_code = code
                    zoning_name = cleaned
                    source_doc = d.file_name
        if fire_district is None:
            fd = f.get("fire_district")
            if isinstance(fd, str) and fd.strip():
                fire_district = FIRE_NAME_TO_CODE.get(fd.strip(), None)
        if coverage is None:
            v = f.get("coverage_ratio_pct")
            if isinstance(v, (int, float)):
                coverage = float(v)
        if far is None:
            v = f.get("floor_area_ratio_pct")
            if isinstance(v, (int, float)):
                far = float(v)

    if zoning_code is None and fire_district is None:
        return None

    if source_doc:
        notes.append(f"用途地域・防火地域を「{source_doc}」から抽出（GIS API不使用）")

    return GeoLookupResult(
        address=address,
        zoning_code=zoning_code,
        zoning_name=zoning_name,
        fire_district=fire_district or "no_district",
        coverage_ratio_pct=coverage,
        floor_area_ratio_pct=far,
        source="document",
        notes=notes,
    )


def _decide_overall_level(
    zoning,
    distance: DistanceCheckResult,
    pattern: InvestigationPattern,
) -> Tuple[JudgmentLevel, str]:
    """総合レベル決定."""
    # 立地 NG なら問答無用
    if zoning.level == JudgmentLevel.NO_GO:
        return (
            JudgmentLevel.NO_GO,
            f"立地が法律上不可です。{zoning.reason}",
        )

    # 距離規制で要協議
    if distance and distance.has_issue:
        return (
            JudgmentLevel.CONDITIONAL,
            (
                "立地は可能ですが、距離規制（学校等100m以内）に該当します。"
                "都道府県知事への意見聴取等の手続きが必要。"
            ),
        )

    # 立地 CONDITIONAL
    if zoning.level == JudgmentLevel.CONDITIONAL:
        return (
            JudgmentLevel.CONDITIONAL,
            (
                "立地は条件付きで可能。延床面積や手動入力情報を補完して再判定してください。"
            ),
        )

    # 立地 GO
    if pattern == InvestigationPattern.A:
        return (
            JudgmentLevel.GO,
            "立地・既存建物条件ともに良好。標準的な用途変更フローで進められます。",
        )
    if pattern == InvestigationPattern.B:
        return (
            JudgmentLevel.GO,
            "立地は可能。既存図面復元と現地実測が必要だが、現実的なスコープで進められます。",
        )
    if pattern in {InvestigationPattern.C, InvestigationPattern.D}:
        return (
            JudgmentLevel.CONDITIONAL,
            (
                "立地は可能だが、既存建物の調査負荷が高い案件。"
                "ガイドライン調査相当のコスト・期間が見込まれます。"
            ),
        )
    return (
        JudgmentLevel.CONDITIONAL,
        "立地は可能だが、判定材料が不足しています。書類の追加で精度が上がります。",
    )
