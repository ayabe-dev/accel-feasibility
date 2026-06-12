"""詳細レポート生成エンジン.

FeasibilityReport から、クライアント向けまたは内部レビュー向けの
包括的なMarkdownレポートを生成する。

Markdownは Word / PDF / HTML への変換が容易で、版管理にも向く。
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .models import (
    BuildingCodeCheckItem,
    CheckStatus,
    FeasibilityReport,
    FireSafetyCheckItem,
    InvestigationPattern,
    JudgmentLevel,
    LodgingBusinessCheckItem,
    RecommendationOption,
)
from .scoring import DEFAULT_WEIGHTS, compute_score


# ----------------------------------------------------------------------
# ステータスアイコン
# ----------------------------------------------------------------------

LEVEL_ICONS = {
    JudgmentLevel.GO: "🟢",
    JudgmentLevel.CONDITIONAL: "🟡",
    JudgmentLevel.NO_GO: "🔴",
}
LEVEL_LABEL = {
    JudgmentLevel.GO: "ほぼ可能（GO）",
    JudgmentLevel.CONDITIONAL: "条件付き可能（CONDITIONAL）",
    JudgmentLevel.NO_GO: "立地不可（NO-GO）",
}

STATUS_ICONS = {
    CheckStatus.COMPLIANT: "✅",
    CheckStatus.NON_COMPLIANT: "❌",
    CheckStatus.NEEDS_REVIEW: "🔍",
    CheckStatus.NOT_APPLICABLE: "⚪",
    CheckStatus.UNKNOWN: "❓",
}
STATUS_LABEL = {
    CheckStatus.COMPLIANT: "適合",
    CheckStatus.NON_COMPLIANT: "不適合（要改修）",
    CheckStatus.NEEDS_REVIEW: "要確認（実測・追加調査）",
    CheckStatus.NOT_APPLICABLE: "非該当",
    CheckStatus.UNKNOWN: "判定不能",
}

PATTERN_LABEL = {
    InvestigationPattern.A: "A：軽量フロー（検査済証あり/築浅/図書完備）",
    InvestigationPattern.B: "B：標準フロー（中間レベル）",
    InvestigationPattern.C: "C：重量フロー（築古/ガイドライン調査相当）",
    InvestigationPattern.D: "D：フルガイドライン調査（検査済証なし）",
    InvestigationPattern.UNKNOWN: "判定不能",
}


# ----------------------------------------------------------------------
# メイン関数
# ----------------------------------------------------------------------


def generate_markdown_report(
    report: FeasibilityReport,
    weights: Optional[Dict[str, float]] = None,
) -> str:
    """詳細Markdownレポートを生成."""
    sections: List[str] = []

    sections.append(_section_header(report))
    sections.append(_section_executive_summary(report, weights))
    sections.append(_section_property_overview(report))
    sections.append(_section_location_regulations(report))
    sections.append(_section_investigation_pattern(report))
    sections.append(_section_application_requirement(report))
    sections.append(_section_building_code(report))
    sections.append(_section_fire_safety(report))
    sections.append(_section_lodging_business(report))
    sections.append(_section_cost_timeline(report))
    sections.append(_section_score_detail(report, weights))
    sections.append(_section_todos(report))
    sections.append(_section_missing_documents(report))
    sections.append(_section_extracted_documents(report))
    sections.append(_section_appendix(report))

    return "\n\n".join(s for s in sections if s)


# ----------------------------------------------------------------------
# セクション生成
# ----------------------------------------------------------------------


def _section_header(report: FeasibilityReport) -> str:
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    address = report.input.address or "未指定"
    biz = "旅館・ホテル営業"
    fa = report.input.floor_area_m2
    fa_str = f"{fa:.2f} ㎡" if fa else "未入力"
    return (
        f"# 用途変更フィジビリティ判定レポート\n\n"
        f"| 項目 | 内容 |\n"
        f"|------|------|\n"
        f"| 生成日時 | {now} |\n"
        f"| 物件所在地 | {address} |\n"
        f"| 対象業態 | {biz} |\n"
        f"| 用途地域 | {report.geo.zoning_name or '未取得'} |\n"
        f"| 構造 | {report.input.structure or '未入力'} |\n"
        f"| 延床面積 | {fa_str} |\n"
        f"\n"
        f"---\n"
        f"\n"
        f"> ⚠️ **本レポートは一次スクリーニング用の自動判定結果です。**"
        f"最終判断にあたっては、一級建築士・所轄行政庁・保健所・消防への確認が必須です。"
    )


def _section_executive_summary(
    report: FeasibilityReport, weights: Optional[Dict[str, float]]
) -> str:
    icon = LEVEL_ICONS[report.overall_level]
    label = LEVEL_LABEL[report.overall_level]

    score = compute_score(report, weights=weights)
    if score.blocked:
        score_line = f"**総合スコア**：ブロック（{score.blocked_reason}）"
    else:
        score_line = (
            f"**総合スコア**：{score.total:.1f} / 100　**グレード**：{score.grade}"
        )

    cost_line = "（見積不能）"
    if report.cost_estimate:
        ce = report.cost_estimate
        cost_line = (
            f"**総コスト目安**：{ce.total_cost_min:,} 〜 {ce.total_cost_max:,} 万円"
            f"　**期間**：{ce.total_months_min} 〜 {ce.total_months_max} ヶ月"
            f"　**信頼度**：{ce.confidence}"
        )

    return (
        f"## 1. エグゼクティブサマリー\n\n"
        f"### {icon} 総合判定：{label}\n\n"
        f"{report.overall_summary}\n\n"
        f"{score_line}\n\n"
        f"{cost_line}\n\n"
        f"**調査パターン**：{PATTERN_LABEL.get(report.pattern, '不明')}\n\n"
        f"{report.pattern_reason}"
    )


def _section_property_overview(report: FeasibilityReport) -> str:
    p = report.input
    rows = []
    if p.address:
        rows.append(("所在地", p.address))
    if p.structure:
        rows.append(("構造", p.structure))
    if p.floors_above:
        rows.append(("地上階数", f"{p.floors_above} 階"))
    if p.floors_below:
        rows.append(("地下階数", f"{p.floors_below} 階"))
    if p.floor_area_m2:
        rows.append(("延床面積", f"{p.floor_area_m2:,.2f} ㎡"))
    if p.built_year:
        rows.append(("建築年", f"{p.built_year} 年"))
    if p.has_inspection_certificate is not None:
        rows.append(
            ("検査済証", "有" if p.has_inspection_certificate else "無")
        )
    if p.renovation_history is not None:
        rows.append(
            ("改修・増築履歴", "あり" if p.renovation_history else "なし")
        )

    if not rows:
        return "## 2. 物件概要\n\n物件情報が未入力です。"

    body = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return (
        f"## 2. 物件概要\n\n"
        f"| 項目 | 値 |\n"
        f"|------|------|\n"
        f"{body}"
    )


def _section_location_regulations(report: FeasibilityReport) -> str:
    geo = report.geo
    zoning = report.zoning

    lines = ["## 3. 立地・規制（Phase 1）\n"]

    # 用途地域
    lines.append("### 3.1 用途地域")
    lines.append(f"- **判定**：{LEVEL_ICONS[zoning.level]} {LEVEL_LABEL[zoning.level]}")
    lines.append(f"- **用途地域**：{geo.zoning_name or '未取得'}")
    lines.append(f"- **根拠**：{zoning.reason}")
    if zoning.floor_area_check:
        lines.append(f"- **床面積チェック**：{zoning.floor_area_check}")
    lines.append("")

    # 都市計画情報
    lines.append("### 3.2 都市計画情報")
    cpc = geo.city_planning_classification or "取得不可"
    lines.append(f"- **区域区分**：{cpc}")
    if cpc and "市街化調整区域" in cpc:
        lines.append(
            f"  - 🔴 **市街化調整区域は原則として旅館・ホテルの建築不可**。"
            f"都市計画法34条の許可申請が必要。"
        )
    dp = geo.district_plan_name
    if dp:
        lines.append(f"- **地区計画**：⚠️ 「{dp}」の指定区域内")
        lines.append(
            "  - 用途・容積等の上乗せ規制の可能性があるため、自治体への照会推奨。"
        )
    else:
        lines.append("- **地区計画**：指定なし")
    hu = geo.high_use_district_name
    if hu:
        lines.append(f"- **高度利用地区**：「{hu}」（容積率の上乗せ根拠）")
    lines.append("")

    # 防火地域
    fd = geo.fire_district or "no_district"
    fd_label = {
        "fire_district": "防火地域（耐火建築物等が原則必須）",
        "quasi_fire_district": "準防火地域（規模・階数により要件）",
        "no_district": "指定なし",
    }.get(fd, fd)
    lines.append(f"### 3.3 防火地域\n- {fd_label}")

    # 建ぺい・容積
    if geo.coverage_ratio_pct or geo.floor_area_ratio_pct:
        lines.append("")
        lines.append("### 3.4 建ぺい率・容積率")
        if geo.coverage_ratio_pct:
            lines.append(f"- 建ぺい率：{geo.coverage_ratio_pct}%")
        if geo.floor_area_ratio_pct:
            lines.append(f"- 容積率：{geo.floor_area_ratio_pct}%")

    # 距離規制
    lines.append("")
    lines.append("### 3.5 距離規制（学校・保育園 100m）")
    if geo.nearby_facilities:
        lines.append(
            f"- 🔴 **半径100m以内に {len(geo.nearby_facilities)} 件の対象施設**"
        )
        for f in geo.nearby_facilities:
            lines.append(
                f"  - {f.name}（{f.facility_type}）／"
                f"約 {f.distance_m:.0f} m ／ {f.address}"
            )
        lines.append(
            "- **対応**：所轄保健所への連絡 → 学校等の管理者との協議 → 同意書取得"
        )
        lines.append(
            "- **注記**：本規制は手続き上の意見聴取が中心で、内容上の問題がなければ"
            "却下されるケースはほぼなし（実務上「あってないようなもの」）"
        )
    else:
        lines.append("- ✅ 半径100m以内に対象施設は確認されず")

    # GIS 取得元
    lines.append("")
    lines.append(f"_GIS データ取得元：{geo.source}_")
    if geo.notes:
        lines.append("\n**メモ**：")
        for n in geo.notes:
            lines.append(f"- {n}")

    return "\n".join(lines)


def _section_investigation_pattern(report: FeasibilityReport) -> str:
    lines = ["## 4. 既存建物の調査負荷判定（Phase 2）\n"]
    lines.append(f"### 4.1 パターン判定")
    lines.append(f"**{PATTERN_LABEL.get(report.pattern, '不明')}**\n")
    lines.append(report.pattern_reason)

    warnings_filtered = [
        w
        for w in report.warnings
        if any(k in w for k in ["新耐震", "木造2000", "2007年"])
    ]
    if warnings_filtered:
        lines.append("\n### 4.2 法改正節目またぎ警告")
        for w in warnings_filtered:
            lines.append(f"- ⚠️ {w}")

    lines.append("\n### 4.3 調査パターンの判定軸")
    lines.append(
        "| パターン | 検査済証 | 築年数 | 図書整備 | 改修履歴 | 想定調査負荷 |\n"
        "|---------|---------|-------|---------|---------|-----------|\n"
        "| A | あり | 〜15年 | 完備 | なし/軽微 | 軽（図面確認 + 現地照合） |\n"
        "| B | あり | 15〜30年 | 一部欠落 | あり | 中（既存図面復元 + 実測） |\n"
        "| C | あり | 30年以上 | 散逸 | あり | 重（ガイドライン調査相当） |\n"
        "| D | なし | 問わず | — | — | 重（ガイドライン調査フル実施） |"
    )

    return "\n".join(lines)


def _section_application_requirement(report: FeasibilityReport) -> str:
    ar = report.application_requirement
    lines = ["## 5. 用途変更確認申請の要否（Phase 3）\n"]

    if ar is None:
        lines.append("判定情報が不足しています。")
        return "\n".join(lines)

    icon = "🔴" if ar.required else "✅"
    label = "必要" if ar.required else "不要"
    lines.append(f"### 5.1 判定結果\n{icon} **確認申請：{label}**\n")
    lines.append(f"**理由**：{ar.reason}")

    fa = ar.floor_area_subject_m2
    lines.append(
        f"\n- 用途変更対象床面積：{fa:.1f} ㎡" if fa else "\n- 床面積：未確定"
    )
    lines.append(f"- 閾値：{ar.threshold_m2} ㎡")
    lines.append(f"- 特殊建築物：{'該当' if ar.is_special_building else '非該当'}")
    lines.append(f"- 類似用途間：{'該当' if ar.is_similar_use else '非該当'}")

    if ar.applicable_articles:
        lines.append("\n### 5.2 根拠条文")
        for a in ar.applicable_articles:
            lines.append(f"- {a}")

    if ar.related_obligations:
        lines.append("\n### 5.3 確認申請が不要でも遡及適用される規定")
        for o in ar.related_obligations:
            lines.append(f"- {o}")

    return "\n".join(lines)


def _section_building_code(report: FeasibilityReport) -> str:
    if not report.building_code_checks:
        return ""

    lines = ["## 6. 建築基準法 技術基準チェック（Phase 4）\n"]
    lines.append(
        "10規定について「適合 / 要確認 / 不適合 / 非該当」を判定。"
        "不適合・要確認の場合は対応オプション（費用・期間・ステップ別TODO）を展開。"
    )

    for c in report.building_code_checks:
        lines.append("\n---")
        lines.append(
            f"\n### {STATUS_ICONS[c.status]} {c.rule_name}"
        )
        lines.append(f"- **条文**：{c.article}")
        lines.append(f"- **要求**：{c.requirement}")
        if c.current:
            lines.append(f"- **現況**：{c.current}")
        lines.append(f"- **判定**：{STATUS_LABEL[c.status]}（影響度：{c.impact}）")
        if c.recommended_action:
            lines.append(f"- **推奨対応**：{c.recommended_action}")

        if c.options:
            lines.append("\n#### 対応オプション\n")
            for i, opt in enumerate(c.options, 1):
                lines.append(_format_option(opt, i))

    return "\n".join(lines)


def _format_option(opt: RecommendationOption, num: int) -> str:
    lines = [
        f"##### {opt.title}\n",
        f"{opt.summary}\n" if opt.summary else "",
        f"| 項目 | 値 |",
        f"|------|------|",
        f"| 概算費用 | {opt.cost_min_man:,} 〜 {opt.cost_max_man:,} 万円 |",
        f"| 概算期間 | {opt.duration_min_months} 〜 {opt.duration_max_months} ヶ月 |",
    ]
    if opt.contact:
        lines.append(f"| 連絡先 | {opt.contact} |")

    if opt.pros:
        lines.append("\n**メリット**")
        for p in opt.pros:
            lines.append(f"- {p}")
    if opt.cons:
        lines.append("\n**デメリット**")
        for d in opt.cons:
            lines.append(f"- {d}")

    if opt.steps:
        lines.append("\n**ステップ別 TODO**\n")
        for j, step in enumerate(opt.steps, 1):
            cost_str = ""
            if step.cost_min_man is not None and step.cost_max_man:
                cost_str = f" ／ 💰 {step.cost_min_man}〜{step.cost_max_man}万円"
            days_str = f"⏱️ 約{step.days}日" if step.days else ""
            owner_str = f"👤 {step.owner}" if step.owner else ""
            meta = " ／ ".join(filter(None, [days_str, cost_str.lstrip(" ／ "), owner_str]))
            lines.append(f"{j}. **{step.title}**")
            if step.description:
                lines.append(f"   - {step.description}")
            if meta:
                lines.append(f"   - _{meta}_")

    return "\n".join(filter(None, lines))


def _section_fire_safety(report: FeasibilityReport) -> str:
    if not report.fire_safety_checks:
        return ""
    lines = ["## 7. 消防法 設備チェック（Phase 5-A）\n"]
    lines.append(
        "消防法施行令別表第一 (5)項イ（旅館・ホテル）の主要設備要件。"
    )
    lines.append("\n| 設備 | 必要 | 閾値・条件 | 現況 | アクション |")
    lines.append("|------|------|-----------|------|----------|")
    for f in report.fire_safety_checks:
        required_icon = "🟢" if f.required else "⚪"
        lines.append(
            f"| {f.equipment_name} | {required_icon} {'必要' if f.required else '不要'} "
            f"| {f.threshold_note} | {f.current_status or '—'} | {f.action or '—'} |"
        )
    return "\n".join(lines)


def _section_lodging_business(report: FeasibilityReport) -> str:
    if not report.lodging_business_checks:
        return ""
    lines = ["## 8. 旅館業法 構造設備基準（Phase 5-B）\n"]
    lines.append(
        "旅館業法施行令1条 + 自治体条例による構造設備基準。"
        "自治体により上乗せ規制があるため、対象自治体への事前相談を推奨。"
    )
    lines.append("\n| 項目 | 基準 | 判定 | 備考 |")
    lines.append("|------|------|------|------|")
    for c in report.lodging_business_checks:
        lines.append(
            f"| {c.item_name} | {c.standard} | "
            f"{STATUS_ICONS[c.status]} {STATUS_LABEL[c.status]} | {c.note or '—'} |"
        )
    return "\n".join(lines)


def _section_cost_timeline(report: FeasibilityReport) -> str:
    if not report.cost_estimate:
        return ""
    ce = report.cost_estimate
    lines = ["## 9. 概算費用・期間\n"]
    lines.append(f"**信頼度**：{ce.confidence}\n")
    lines.append("| フェーズ | 費用（万円） | 主な作業 |")
    lines.append("|---------|-----------|---------|")
    lines.append(
        f"| 既存調査 | {ce.investigation_cost_min:,} 〜 {ce.investigation_cost_max:,} "
        f"| {', '.join(ce.investigation_tasks) if ce.investigation_tasks else '—'} |"
    )
    lines.append(
        f"| 確認申請 | {ce.application_cost_min:,} 〜 {ce.application_cost_max:,} "
        f"| {', '.join(ce.application_tasks) if ce.application_tasks else '—'} |"
    )
    lines.append(
        f"| 改修工事 | {ce.renovation_cost_min:,} 〜 {ce.renovation_cost_max:,} "
        f"| {', '.join(ce.renovation_tasks) if ce.renovation_tasks else '—'} |"
    )
    lines.append(
        f"| **合計** | **{ce.total_cost_min:,} 〜 {ce.total_cost_max:,}** | — |"
    )

    lines.append(
        f"\n**期間目安**：{ce.total_months_min} 〜 {ce.total_months_max} ヶ月"
    )

    return "\n".join(lines)


def _section_score_detail(
    report: FeasibilityReport, weights: Optional[Dict[str, float]]
) -> str:
    score = compute_score(report, weights=weights)
    if score.blocked:
        return f"## 10. 総合スコア詳細\n\nスコア計算ブロック：{score.blocked_reason}"

    lines = [
        f"## 10. 総合スコア詳細\n",
        f"**総合スコア**：{score.total:.1f} / 100　**グレード**：{score.grade}\n",
        "### 10.1 17項目の内訳\n",
        "| カテゴリ | 項目 | スコア | 重み | 寄与 | コメント |",
        "|---------|------|------|------|------|--------|",
    ]
    for item in score.items:
        lines.append(
            f"| {item.category} | {item.label} | {item.score:.0f} "
            f"| {item.weight:.1f} | {item.weighted:.0f} | {item.note} |"
        )

    lines.append("\n### 10.2 グレード基準")
    lines.append(
        "| グレード | スコア範囲 | 意味 |\n"
        "|---------|----------|------|\n"
        "| S | 85+ | 即GO候補 |\n"
        "| A | 75〜85 | 標準的に進行可 |\n"
        "| B | 60〜75 | 要対応事項あり |\n"
        "| C | 45〜60 | 大きな改修必要 |\n"
        "| D | <45 | 再検討推奨 |"
    )

    return "\n".join(lines)


def _section_todos(report: FeasibilityReport) -> str:
    if not report.todos:
        return ""
    lines = ["## 11. TODOリスト\n"]
    lines.append(
        "判定パターン・総合判定・不足書類から自動生成された次アクション一覧。"
    )
    pri_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for i, todo in enumerate(report.todos, 1):
        icon = pri_icons.get(todo.priority, "⚪")
        lines.append(
            f"\n### {icon} {i}. {todo.title}"
        )
        lines.append(f"{todo.description}")
        meta = []
        if todo.owner:
            meta.append(f"担当：{todo.owner}")
        if todo.estimated_days:
            meta.append(f"想定：{todo.estimated_days} 日")
        if meta:
            lines.append(f"\n_{' ／ '.join(meta)}_")
    return "\n".join(lines)


def _section_missing_documents(report: FeasibilityReport) -> str:
    if not report.missing_documents:
        return ""
    lines = ["## 12. 不足書類\n"]
    lines.append("以下の書類を追加で取得すると、判定精度が向上します。")
    for d in report.missing_documents:
        lines.append(f"- 📄 {d}")
    return "\n".join(lines)


def _section_extracted_documents(report: FeasibilityReport) -> str:
    if not report.extracted_documents:
        return ""
    lines = ["## 13. 抽出書類\n"]
    lines.append(
        "アップロードされた書類から LLM が自動抽出した内容。"
    )
    for d in report.extracted_documents:
        lines.append(f"\n### 📎 {d.file_name}")
        lines.append(f"- **書類種別**：{d.document_type.value}")
        lines.append(f"- **信頼度**：{d.confidence:.2f}")
        if d.raw_text_summary:
            lines.append(f"- **サマリ**：{d.raw_text_summary}")
        if d.extracted_fields:
            lines.append("\n**抽出フィールド**：")
            lines.append("\n| キー | 値 |")
            lines.append("|------|------|")
            for k, v in d.extracted_fields.items():
                lines.append(f"| {k} | {v} |")
        if d.warnings:
            lines.append("\n**警告**：")
            for w in d.warnings:
                lines.append(f"- ⚠️ {w}")
    return "\n".join(lines)


def _section_appendix(report: FeasibilityReport) -> str:
    lines = ["## 付録\n"]
    lines.append("### A. 関係者・連絡先\n")
    lines.append(
        "用途変更案件で関わる主要な関係者と、その役割。\n\n"
        "| 関係者 | 役割 |\n"
        "|--------|------|\n"
        "| 一級建築士事務所 | 確認申請・図面作成・プロジェクトマネジメント |\n"
        "| 構造設計事務所 | 構造耐力評価・耐火被覆設計・補強設計 |\n"
        "| 設備設計事務所 | 給排水・電気・換気・排煙の設計 |\n"
        "| 指定確認検査機関 | 確認申請の審査・完了検査・ガイドライン調査 |\n"
        "| 所轄消防署（予防課） | 消防設備設置届・消防法令適合通知書 |\n"
        "| 所轄保健所 | 旅館業許可申請・玄関帳場運用相談・距離規制協議 |\n"
        "| 自治体建築指導課 | 用途地域・建ぺい容積率・既存不適格判定 |\n"
        "| 自治体都市計画課 | 地区計画・高度利用・市街化調整区域 |\n"
        "| 行政書士 | 旅館業許可申請の代行 |\n"
        "| 解体工事業者 | 減築・部分解体工事 |\n"
        "| 耐火被覆専門業者 | 鉄骨耐火被覆工事 |\n"
        "| 鉄骨階段メーカー | 屋外階段の設計・製作・施工 |"
    )

    lines.append("\n### B. 主要根拠条文\n")
    lines.append(
        "- **建築基準法 48 条**：用途地域内の建築物制限（別表第二）\n"
        "- **建築基準法 27 条**：特殊建築物の耐火要件\n"
        "- **建築基準法 28 条**：採光・換気\n"
        "- **建築基準法 6 条 1 項 1 号**：特殊建築物の確認申請\n"
        "- **建築基準法 87 条**：用途変更の準用\n"
        "- **建築基準法 20 条**：構造耐力\n"
        "- **建築基準法 61 条**：防火地域内の建築物\n"
        "- **建築基準法施行令 137 条の 18**：類似用途間の用途変更\n"
        "- **建築基準法施行令 119 条**：廊下幅\n"
        "- **建築基準法施行令 120 条**：歩行距離\n"
        "- **建築基準法施行令 121 条**：二方向避難\n"
        "- **建築基準法施行令 126 条の 2・3**：排煙設備\n"
        "- **建築基準法施行令 126 条の 4・5**：非常用照明\n"
        "- **建築基準法施行令 128 条の 4・5**：内装制限\n"
        "- **建築基準法施行令 112 条**：防火区画\n"
        "- **消防法施行令別表第一 (5) 項イ**：旅館・ホテル用途\n"
        "- **消防法施行令 11 条**：屋内消火栓\n"
        "- **消防法施行令 12 条**：スプリンクラー設備\n"
        "- **消防法施行令 21 条**：自動火災報知設備\n"
        "- **旅館業法 3 条 3 項・4 項**：許可基準・距離規制\n"
        "- **旅館業法施行令 1 条**：構造設備基準\n"
        "- **都市計画法 34 条**：市街化調整区域内の開発許可"
    )

    lines.append("\n### C. データ取得元\n")
    lines.append(
        "- **国土交通省 不動産情報ライブラリ API**（XKT001/002/006/007/014/023/024）\n"
        "- **国土地理院 AddressSearch API**（住所→緯度経度）\n"
        "- **Google Gemini API**（書類OCR・構造化抽出）"
    )

    lines.append(
        "\n---\n\n"
        "_本レポートは AI による自動判定結果です。最終的な法令適合性および事業判断は、"
        "対象自治体・所轄行政庁、および一級建築士による精査が必要です。_"
    )
    return "\n".join(lines)
