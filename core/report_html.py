"""HTML / PDF レポート生成エンジン.

クライアント・社内レビュー向けに見栄えの整ったHTMLレポートを生成し、
weasyprintが利用可能な環境ではPDFも生成する。

weasyprintは macOS では `brew install pango` 等のシステム依存があるため、
未インストール時は HTML のみ返し、ブラウザの印刷機能で PDF化してもらう想定。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

from .models import (
    CheckStatus,
    FeasibilityReport,
    InvestigationPattern,
    JudgmentLevel,
)
from .scoring import DEFAULT_WEIGHTS, compute_score

logger = logging.getLogger(__name__)

try:
    from weasyprint import HTML as WeasyHTML  # type: ignore

    _WEASYPRINT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _WEASYPRINT_AVAILABLE = False


# ----------------------------------------------------------------------
# 共通スタイル
# ----------------------------------------------------------------------

CSS = """
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans",
                 "Helvetica Neue", Arial, sans-serif;
    color: #1a1a1a;
    line-height: 1.6;
    max-width: 820px;
    margin: 0 auto;
    padding: 24px 32px;
    font-size: 13px;
    background: #fff;
}

h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
h2 {
    font-size: 18px;
    font-weight: 600;
    margin: 40px 0 16px;
    padding: 10px 14px;
    border-left: 5px solid #2563eb;
    background: #eff6ff;
    color: #1e40af;
    page-break-after: avoid;
    break-after: avoid-page;
}
h3 {
    font-size: 14px;
    font-weight: 600;
    margin: 22px 0 10px;
    color: #334155;
    page-break-after: avoid;
    break-after: avoid-page;
}
h4 {
    font-size: 13px;
    font-weight: 600;
    margin: 14px 0 8px;
    color: #475569;
    page-break-after: avoid;
    break-after: avoid-page;
}
p {
    margin-bottom: 10px;
    line-height: 1.75;
    orphans: 3;
    widows: 3;
}

.meta-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 18px;
}
.meta-table td {
    padding: 6px 10px;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: top;
}
.meta-table td:first-child {
    color: #64748b;
    width: 130px;
    font-weight: 500;
    background: #f8fafc;
}

.summary-card {
    background: #f8fafc;
    border-left: 4px solid #2563eb;
    padding: 16px 18px;
    border-radius: 4px;
    margin: 14px 0 20px;
}
.summary-card.go {
    background: #ecfdf5;
    border-left-color: #10b981;
}
.summary-card.conditional {
    background: #fefce8;
    border-left-color: #eab308;
}
.summary-card.nogo {
    background: #fef2f2;
    border-left-color: #dc2626;
}

.judgment-line {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 8px;
}
.judgment-icon { font-size: 20px; margin-right: 8px; }

.metric-row {
    display: flex;
    gap: 16px;
    margin: 16px 0;
    flex-wrap: wrap;
}
.metric {
    flex: 1;
    min-width: 140px;
    padding: 12px 14px;
    background: #f1f5f9;
    border-radius: 6px;
    border: 1px solid #e2e8f0;
}
.metric-label { font-size: 11px; color: #64748b; margin-bottom: 4px; }
.metric-value { font-size: 18px; font-weight: 600; color: #0f172a; }

table {
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0 18px;
    font-size: 12px;
    page-break-inside: auto;
}
th, td {
    border: 1px solid #e5e7eb;
    padding: 7px 10px;
    text-align: left;
    vertical-align: top;
}
th {
    background: #f1f5f9;
    color: #334155;
    font-weight: 600;
}
tr { page-break-inside: avoid; }

.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 500;
}
.badge-success { background: #d1fae5; color: #065f46; }
.badge-warning { background: #fef3c7; color: #92400e; }
.badge-danger { background: #fee2e2; color: #991b1b; }
.badge-neutral { background: #e5e7eb; color: #374151; }
.badge-info { background: #dbeafe; color: #1e40af; }

.grade-pill {
    display: inline-block;
    padding: 4px 16px;
    border-radius: 6px;
    font-size: 18px;
    font-weight: 600;
    color: #fff;
}
.grade-S { background: #16a34a; }
.grade-A { background: #0d9488; }
.grade-B { background: #2563eb; }
.grade-C { background: #d97706; }
.grade-D { background: #dc2626; }

ul, ol {
    margin: 4px 0 12px 18px;
}
li { margin-bottom: 3px; }

.option-card {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 12px 14px;
    margin: 10px 0;
    background: #fafbfc;
    page-break-inside: avoid;
}
.option-card h4 {
    color: #1e40af;
    margin-bottom: 6px;
}
.option-meta {
    display: flex;
    gap: 12px;
    margin: 8px 0 6px;
}
.option-meta .metric { padding: 8px 10px; min-width: 0; }

.step-list {
    margin: 6px 0 4px 18px;
    font-size: 12px;
    color: #475569;
}
.step-list li {
    margin-bottom: 4px;
}
.step-meta {
    color: #64748b;
    font-size: 11px;
    margin-left: 4px;
}

.pros-cons {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 8px 0;
    font-size: 12px;
}
.pros, .cons {
    padding: 8px 10px;
    border-radius: 4px;
}
.pros { background: #f0fdf4; }
.cons { background: #fef2f2; }
.pros strong { color: #15803d; }
.cons strong { color: #b91c1c; }

.facility-list {
    margin: 6px 0 12px 0;
}
.facility-list li {
    padding: 4px 0;
    border-bottom: 1px dashed #e5e7eb;
}

footer {
    margin-top: 32px;
    padding-top: 12px;
    border-top: 1px solid #e5e7eb;
    font-size: 11px;
    color: #64748b;
    text-align: center;
}

.disclaimer {
    background: #fffbeb;
    border-left: 3px solid #f59e0b;
    padding: 10px 14px;
    margin: 12px 0;
    font-size: 11px;
    color: #92400e;
    border-radius: 3px;
}

.page-break { page-break-after: always; break-after: page; }

/* セクションラッパー：各 <h2> 直前で改ページ */
section.report-section {
    page-break-before: always;
    break-before: page;
}
section.report-section:first-of-type {
    page-break-before: auto;
    break-before: auto;
}

/* テーブルはなるべく1ページに収める */
table {
    page-break-inside: avoid;
    break-inside: avoid;
}
.option-card {
    page-break-inside: avoid;
    break-inside: avoid;
}
.summary-card {
    page-break-inside: avoid;
    break-inside: avoid;
}

/* 印刷時の強化 */
@media print {
    body {
        max-width: none;
        padding: 0;
        font-size: 11px;
    }
    section.report-section {
        page-break-before: always;
        break-before: page;
    }
    section.report-section:first-of-type {
        page-break-before: auto;
        break-before: auto;
    }
    h2 {
        margin-top: 0;
    }
    h3, h4 {
        page-break-after: avoid;
        break-after: avoid-page;
    }
    table, .option-card, .summary-card, .metric-row {
        page-break-inside: avoid;
        break-inside: avoid;
    }
    tr {
        page-break-inside: avoid;
        break-inside: avoid;
    }
    @page {
        size: A4;
        margin: 18mm 16mm 18mm 16mm;
    }
    footer { page-break-before: avoid; }
}
"""


# ----------------------------------------------------------------------
# ヘルパー
# ----------------------------------------------------------------------


def _esc(s) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


LEVEL_CLASS = {
    JudgmentLevel.GO: ("go", "✅", "ほぼ可能（GO）"),
    JudgmentLevel.CONDITIONAL: ("conditional", "⚠️", "条件付き可能"),
    JudgmentLevel.NO_GO: ("nogo", "🛑", "立地不可（NO-GO）"),
}
STATUS_CLASS = {
    CheckStatus.COMPLIANT: ("badge-success", "適合"),
    CheckStatus.NON_COMPLIANT: ("badge-danger", "不適合"),
    CheckStatus.NEEDS_REVIEW: ("badge-warning", "要確認"),
    CheckStatus.NOT_APPLICABLE: ("badge-neutral", "非該当"),
    CheckStatus.UNKNOWN: ("badge-neutral", "判定不能"),
}
PATTERN_LABEL = {
    InvestigationPattern.A: "A：軽量フロー",
    InvestigationPattern.B: "B：標準フロー",
    InvestigationPattern.C: "C：重量フロー（ガイドライン相当）",
    InvestigationPattern.D: "D：フルガイドライン調査",
    InvestigationPattern.UNKNOWN: "判定不能",
}


# ----------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------


def generate_html_report(
    report: FeasibilityReport,
    weights: Optional[Dict[str, float]] = None,
) -> str:
    """調査結果のHTMLレポートを生成."""
    weights = weights or DEFAULT_WEIGHTS
    score = compute_score(report, weights=weights)
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    # 各セクションを <section class="report-section"> でラップ
    # → 印刷時に各セクション直前で改ページが効く
    raw_sections = [
        _render_header(report, now),
        _render_executive_summary(report, score),
        _render_context_impact(report),
        _render_property_overview(report),
        _render_location(report),
        _render_investigation_pattern(report),
        _render_application(report),
        _render_building_code(report),
        _render_fire_safety(report),
        _render_lodging(report),
        _render_cost_timeline(report),
        _render_score(score),
        _render_todos(report),
        _render_missing_docs(report),
        _render_extracted(report),
        _render_appendix(),
    ]
    wrapped = [
        f'<section class="report-section">{s}</section>'
        for s in raw_sections
        if s
    ]
    wrapped.append(_render_footer(now))
    body = "\n".join(wrapped)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>用途変更フィジビリティ判定レポート — {_esc(report.input.address or '物件')}</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def generate_pdf(report: FeasibilityReport, weights: Optional[Dict] = None) -> Optional[bytes]:
    """HTMLからPDFを生成。weasyprint未インストール時はNone."""
    if not _WEASYPRINT_AVAILABLE:
        return None
    html = generate_html_report(report, weights=weights)
    try:
        pdf_bytes = WeasyHTML(string=html).write_pdf()
        return pdf_bytes
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"weasyprint PDF生成失敗: {exc}")
        return None


def is_pdf_available() -> bool:
    return _WEASYPRINT_AVAILABLE


# ----------------------------------------------------------------------
# セクションレンダラ
# ----------------------------------------------------------------------


def _render_header(report: FeasibilityReport, now: str) -> str:
    p = report.input
    fa = f"{p.floor_area_m2:.2f} ㎡" if p.floor_area_m2 else "未入力"
    return f"""
<h1>用途変更フィジビリティ判定レポート</h1>
<p style="color:#64748b; margin-bottom:14px;">住宅 → 旅館・ホテル営業 一次スクリーニング</p>
<table class="meta-table">
<tr><td>生成日時</td><td>{_esc(now)}</td></tr>
<tr><td>物件所在地</td><td>{_esc(p.address or '未指定')}</td></tr>
<tr><td>対象業態</td><td>旅館・ホテル営業</td></tr>
<tr><td>用途地域</td><td>{_esc(report.geo.zoning_name or '未取得')}</td></tr>
<tr><td>構造</td><td>{_esc(p.structure or '未入力')}</td></tr>
<tr><td>延床面積</td><td>{_esc(fa)}</td></tr>
<tr><td>建築年</td><td>{_esc(str(p.built_year) + ' 年') if p.built_year else '未入力'}</td></tr>
<tr><td>検査済証</td><td>{'有' if p.has_inspection_certificate else '無' if p.has_inspection_certificate is False else '不明'}</td></tr>
</table>
<div class="disclaimer">
⚠️ 本レポートは AI による一次スクリーニング結果です。最終判断にあたっては、
一級建築士・所轄行政庁・保健所・消防への確認が必須です。
</div>
"""


def _render_executive_summary(report: FeasibilityReport, score) -> str:
    cls, icon, label = LEVEL_CLASS[report.overall_level]
    if score.blocked:
        score_html = f'<div class="metric-value">ブロック</div><div class="metric-label">{_esc(score.blocked_reason)}</div>'
    else:
        score_html = (
            f'<div class="metric-value">{score.total:.1f} / 100</div>'
            f'<div class="metric-label">'
            f'<span class="grade-pill grade-{score.grade}">{score.grade}</span></div>'
        )

    if report.cost_estimate:
        ce = report.cost_estimate
        cost_block = (
            f'<div class="metric"><div class="metric-label">総コスト目安（万円）</div>'
            f'<div class="metric-value">{ce.total_cost_min:,} 〜 {ce.total_cost_max:,}</div></div>'
            f'<div class="metric"><div class="metric-label">期間目安</div>'
            f'<div class="metric-value">{ce.total_months_min} 〜 {ce.total_months_max} ヶ月</div></div>'
        )
    else:
        cost_block = ""

    return f"""
<h2>1. エグゼクティブサマリー</h2>
<div class="summary-card {cls}">
<div class="judgment-line"><span class="judgment-icon">{icon}</span> 総合判定：{_esc(label)}</div>
<p>{_esc(report.overall_summary)}</p>
</div>

<div class="metric-row">
<div class="metric"><div class="metric-label">総合スコア</div>{score_html}</div>
{cost_block}
<div class="metric"><div class="metric-label">調査パターン</div>
<div class="metric-value">{_esc(PATTERN_LABEL.get(report.pattern, '不明'))}</div></div>
</div>

<p style="font-size:12px; color:#475569;">{_esc(report.pattern_reason)}</p>
"""


def _render_context_impact(report: FeasibilityReport) -> str:
    """追加情報の LLM 解釈結果セクション."""
    ci = report.context_impact
    if ci is None:
        return ""

    if ci.warning and not ci.impacts:
        return f"""
<h2>1.5 追加情報の評価への影響</h2>
<div class="disclaimer">
⚠️ {_esc(ci.warning)}
</div>
{f'<p><strong>記入内容：</strong>{_esc(ci.raw_context)}</p>' if ci.raw_context else ''}
"""

    direction_class = {
        "プラス": ("badge-success", "🟢"),
        "マイナス": ("badge-danger", "🔴"),
        "中立": ("badge-neutral", "⚪"),
    }

    summary_html = (
        f'<div class="summary-card"><p>{_esc(ci.overall_summary)}</p></div>'
        if ci.overall_summary
        else ""
    )

    raw_html = (
        f"""
<details style="margin: 8px 0 14px;">
<summary style="cursor:pointer; color:#475569; font-size:12px;">📝 ユーザー入力の特記事項</summary>
<pre style="background:#f8fafc; padding:10px; border-radius:4px; white-space:pre-wrap;
font-size:12px; margin:6px 0; border:1px solid #e2e8f0;">{_esc(ci.raw_context)}</pre>
</details>
"""
        if ci.raw_context
        else ""
    )

    impact_rows = []
    for imp in ci.impacts:
        cls, icon = direction_class.get(imp.direction, ("badge-neutral", "⚪"))
        sign = "+" if imp.score_adjustment_hint > 0 else ""
        affected = (
            f'<div style="font-size:11px; color:#64748b; margin-top:4px;">'
            f'影響項目：{_esc("、".join(imp.affected_rules))}</div>'
            if imp.affected_rules
            else ""
        )
        impact_rows.append(
            f"""
<div class="option-card">
<h4 style="margin-bottom:6px;">
{icon} {_esc(imp.aspect)}
<span class="badge {cls}" style="margin-left:8px;">{_esc(imp.direction)}</span>
<span class="badge badge-info" style="margin-left:4px;">
スコア影響目安：{sign}{imp.score_adjustment_hint}</span>
</h4>
<p style="margin:6px 0;">{_esc(imp.summary)}</p>
{affected}
</div>
"""
        )

    questions_html = ""
    if ci.suggested_additional_questions:
        items = "".join(
            f"<li>{_esc(q)}</li>" for q in ci.suggested_additional_questions
        )
        questions_html = f"""
<h3>💡 更に評価を精緻化するための追加質問</h3>
<ul>{items}</ul>
"""

    return f"""
<h2>1.5 追加情報の評価への影響</h2>
<p style="font-size:12px; color:#64748b;">
ユーザーが入力した特記事項を Gemini が解釈し、評価への影響を観点別に整理した結果です。
このスコア影響目安は重み調整UIには自動反映されません（建築士による最終判断の材料として）。
</p>
{raw_html}
{summary_html}
<h3>観点別の影響</h3>
{''.join(impact_rows) if impact_rows else '<p>影響項目は抽出されませんでした。</p>'}
{questions_html}
"""


def _render_property_overview(report: FeasibilityReport) -> str:
    p = report.input
    rows = []
    items = [
        ("所在地", p.address),
        ("構造", p.structure),
        ("地上階数", f"{p.floors_above} 階" if p.floors_above else None),
        ("地下階数", f"{p.floors_below} 階" if p.floors_below else None),
        ("延床面積", f"{p.floor_area_m2:,.2f} ㎡" if p.floor_area_m2 else None),
        ("建築年", f"{p.built_year} 年" if p.built_year else None),
        ("検査済証", "有" if p.has_inspection_certificate else "無" if p.has_inspection_certificate is False else None),
        ("改修・増築履歴", "あり" if p.renovation_history else "なし" if p.renovation_history is False else None),
    ]
    for k, v in items:
        if v:
            rows.append(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>")
    if not rows:
        return ""
    return f"""
<h2>2. 物件概要</h2>
<table class="meta-table">{''.join(rows)}</table>
"""


def _render_location(report: FeasibilityReport) -> str:
    geo = report.geo
    zoning = report.zoning
    cls, _, label = LEVEL_CLASS[zoning.level]

    cpc = geo.city_planning_classification or "取得不可"
    cpc_html = f'<span class="badge badge-danger">{_esc(cpc)}</span>' if "市街化調整区域" in (cpc or "") else f'<span class="badge badge-success">{_esc(cpc)}</span>'

    dp_html = (
        f'<span class="badge badge-warning">⚠️ {_esc(geo.district_plan_name)}</span> ／ 自治体に照会推奨'
        if geo.district_plan_name
        else '<span class="badge badge-neutral">指定なし</span>'
    )
    hu_html = (
        f'<span class="badge badge-info">{_esc(geo.high_use_district_name)}</span>'
        if geo.high_use_district_name
        else '<span class="badge badge-neutral">指定なし</span>'
    )
    fd_map = {
        "fire_district": '<span class="badge badge-danger">防火地域</span> 原則 耐火建築物が必須',
        "quasi_fire_district": '<span class="badge badge-warning">準防火地域</span> 規模・階数により耐火/準耐火要件',
        "no_district": '<span class="badge badge-success">指定なし</span>',
    }
    fd_html = fd_map.get(geo.fire_district or "no_district", '<span class="badge badge-neutral">不明</span>')

    facilities_html = ""
    if geo.nearby_facilities:
        items = "".join(
            f"<li><strong>{_esc(f.name)}</strong>（{_esc(f.facility_type)}）"
            f" 約 {f.distance_m:.0f} m ／ {_esc(f.address)}</li>"
            for f in geo.nearby_facilities
        )
        facilities_html = f"""
<div class="summary-card conditional">
<strong>半径100m以内に {len(geo.nearby_facilities)} 件の対象施設</strong>
<ul class="facility-list">{items}</ul>
<p style="font-size:11px; color:#92400e;">
保健所への連絡 → 学校等の管理者との協議 → 同意書取得で通常通る手続きです（実務上「あってないようなもの」）。
</p>
</div>
"""
    else:
        facilities_html = '<p><span class="badge badge-success">✓ 半径100m以内に対象施設なし</span></p>'

    return f"""
<h2>3. 立地・規制（Phase 1）</h2>
<h3>3.1 用途地域</h3>
<div class="summary-card {cls}">
<strong>{_esc(label)}</strong> ／ {_esc(geo.zoning_name or '未取得')}
<p style="margin-top:6px;">{_esc(zoning.reason)}</p>
</div>

<h3>3.2 都市計画情報</h3>
<table>
<tr><th>項目</th><th>状態</th></tr>
<tr><td>区域区分</td><td>{cpc_html}</td></tr>
<tr><td>地区計画</td><td>{dp_html}</td></tr>
<tr><td>高度利用地区</td><td>{hu_html}</td></tr>
<tr><td>防火地域</td><td>{fd_html}</td></tr>
</table>

<h3>3.3 建ぺい率・容積率</h3>
<p>建ぺい率：{geo.coverage_ratio_pct or '?'}% ／ 容積率：{geo.floor_area_ratio_pct or '?'}%</p>

<h3>3.4 距離規制（学校・保育園 100m）</h3>
{facilities_html}

<p style="font-size:11px; color:#64748b; margin-top:8px;">_GIS取得元：{_esc(geo.source)}_</p>
"""


def _render_investigation_pattern(report: FeasibilityReport) -> str:
    milestone_warnings = [
        w for w in report.warnings if any(k in w for k in ["新耐震", "木造2000", "2007年"])
    ]
    ms_html = ""
    if milestone_warnings:
        items = "".join(f"<li>⚠️ {_esc(w)}</li>" for w in milestone_warnings)
        ms_html = f'<h3>4.2 法改正節目またぎ警告</h3><ul>{items}</ul>'
    return f"""
<h2>4. 既存建物の調査負荷判定（Phase 2）</h2>
<h3>4.1 パターン判定</h3>
<p><strong>{_esc(PATTERN_LABEL.get(report.pattern, '不明'))}</strong></p>
<p>{_esc(report.pattern_reason)}</p>
{ms_html}
<h3>4.3 パターン分類</h3>
<table>
<tr><th>パターン</th><th>検査済証</th><th>築年</th><th>図書</th><th>負荷</th></tr>
<tr><td>A</td><td>あり</td><td>〜15年</td><td>完備</td><td>軽</td></tr>
<tr><td>B</td><td>あり</td><td>15〜30年</td><td>一部欠落</td><td>中</td></tr>
<tr><td>C</td><td>あり</td><td>30年以上</td><td>散逸</td><td>重（ガイドライン相当）</td></tr>
<tr><td>D</td><td>なし</td><td>—</td><td>—</td><td>重（フル実施）</td></tr>
</table>
"""


def _render_application(report: FeasibilityReport) -> str:
    ar = report.application_requirement
    if ar is None:
        return "<h2>5. 用途変更確認申請</h2><p>判定情報不足。</p>"
    cls = "nogo" if ar.required else "go"
    icon = "📝" if ar.required else "✓"
    label = "必要" if ar.required else "不要"
    fa = f"{ar.floor_area_subject_m2:.1f} ㎡" if ar.floor_area_subject_m2 else "未確定"
    arts = "".join(f"<li>{_esc(a)}</li>" for a in ar.applicable_articles)
    obls = "".join(f"<li>{_esc(o)}</li>" for o in ar.related_obligations)
    obls_html = (
        f'<h3>5.3 申請不要でも遡及適用される規定</h3><ul>{obls}</ul>'
        if obls
        else ""
    )
    return f"""
<h2>5. 用途変更確認申請（Phase 3）</h2>
<div class="summary-card {cls}">
<strong>{icon} 確認申請：{label}</strong>
<p>{_esc(ar.reason)}</p>
</div>
<h3>5.1 判定パラメータ</h3>
<table>
<tr><td>用途変更対象床面積</td><td>{_esc(fa)} （閾値 {ar.threshold_m2} ㎡）</td></tr>
<tr><td>特殊建築物</td><td>{'該当' if ar.is_special_building else '非該当'}</td></tr>
<tr><td>類似用途間</td><td>{'該当' if ar.is_similar_use else '非該当'}</td></tr>
</table>
<h3>5.2 根拠条文</h3>
<ul>{arts}</ul>
{obls_html}
"""


def _render_building_code(report: FeasibilityReport) -> str:
    if not report.building_code_checks:
        return ""
    cards = []
    for c in report.building_code_checks:
        badge_cls, badge_label = STATUS_CLASS[c.status]
        opts_html = ""
        if c.options:
            opt_blocks = []
            for i, opt in enumerate(c.options, 1):
                pros = (
                    "".join(f"<li>{_esc(p)}</li>" for p in opt.pros)
                    if opt.pros
                    else ""
                )
                cons = (
                    "".join(f"<li>{_esc(d)}</li>" for d in opt.cons)
                    if opt.cons
                    else ""
                )
                pros_cons = (
                    f'<div class="pros-cons">'
                    f'<div class="pros"><strong>メリット</strong><ul>{pros}</ul></div>'
                    f'<div class="cons"><strong>デメリット</strong><ul>{cons}</ul></div>'
                    f'</div>'
                    if pros or cons
                    else ""
                )
                steps_html = ""
                if opt.steps:
                    step_items = []
                    for j, step in enumerate(opt.steps, 1):
                        meta_parts = []
                        if step.days:
                            meta_parts.append(f"約{step.days}日")
                        if step.cost_min_man is not None and step.cost_max_man:
                            meta_parts.append(f"{step.cost_min_man}〜{step.cost_max_man}万円")
                        if step.owner:
                            meta_parts.append(step.owner)
                        meta = " ／ ".join(meta_parts)
                        step_items.append(
                            f"<li><strong>{_esc(step.title)}</strong>"
                            + (f"<br>{_esc(step.description)}" if step.description else "")
                            + (f'<br><span class="step-meta">{_esc(meta)}</span>' if meta else "")
                            + "</li>"
                        )
                    steps_html = f'<strong>ステップ別 TODO</strong><ol class="step-list">{"".join(step_items)}</ol>'

                contact_html = f"<p><strong>連絡先：</strong>{_esc(opt.contact)}</p>" if opt.contact else ""

                opt_blocks.append(f"""
<div class="option-card">
<h4>{_esc(opt.title)}</h4>
<p>{_esc(opt.summary)}</p>
<div class="option-meta">
<div class="metric"><div class="metric-label">費用（万円）</div>
<div class="metric-value">{opt.cost_min_man:,} 〜 {opt.cost_max_man:,}</div></div>
<div class="metric"><div class="metric-label">期間（ヶ月）</div>
<div class="metric-value">{opt.duration_min_months} 〜 {opt.duration_max_months}</div></div>
</div>
{pros_cons}
{steps_html}
{contact_html}
</div>
""")
            opts_html = f"<h4>対応オプション</h4>{''.join(opt_blocks)}"

        cards.append(f"""
<h3>{_esc(c.rule_name)} <span class="badge {badge_cls}">{badge_label}</span></h3>
<table>
<tr><td>条文</td><td>{_esc(c.article)}</td></tr>
<tr><td>要求</td><td>{_esc(c.requirement)}</td></tr>
{f'<tr><td>現況</td><td>{_esc(c.current)}</td></tr>' if c.current else ''}
{f'<tr><td>推奨対応</td><td>{_esc(c.recommended_action)}</td></tr>' if c.recommended_action else ''}
<tr><td>影響度</td><td>{_esc(c.impact)}</td></tr>
</table>
{opts_html}
""")
    return f"<h2>6. 建築基準法 技術基準チェック（Phase 4）</h2>{''.join(cards)}"


def _render_fire_safety(report: FeasibilityReport) -> str:
    if not report.fire_safety_checks:
        return ""
    rows = []
    for f in report.fire_safety_checks:
        badge = "badge-warning" if f.required else "badge-neutral"
        label = "必要" if f.required else "不要"
        rows.append(
            f"<tr><td>{_esc(f.equipment_name)}</td>"
            f"<td><span class='badge {badge}'>{label}</span></td>"
            f"<td>{_esc(f.threshold_note)}</td>"
            f"<td>{_esc(f.current_status or '—')}</td>"
            f"<td>{_esc(f.action or '—')}</td></tr>"
        )
    return f"""
<h2>7. 消防法 設備チェック（Phase 5-A）</h2>
<p>消防法施行令別表第一 (5)項イ（旅館・ホテル）の主要設備要件。</p>
<table>
<tr><th>設備</th><th>判定</th><th>閾値・条件</th><th>現況</th><th>アクション</th></tr>
{''.join(rows)}
</table>
"""


def _render_lodging(report: FeasibilityReport) -> str:
    if not report.lodging_business_checks:
        return ""
    rows = []
    for c in report.lodging_business_checks:
        badge_cls, badge_label = STATUS_CLASS[c.status]
        rows.append(
            f"<tr><td>{_esc(c.item_name)}</td>"
            f"<td>{_esc(c.standard)}</td>"
            f"<td><span class='badge {badge_cls}'>{badge_label}</span></td>"
            f"<td>{_esc(c.note or '—')}</td></tr>"
        )
    return f"""
<h2>8. 旅館業法 構造設備基準（Phase 5-B）</h2>
<p>旅館業法施行令1条 + 自治体条例。京都市等は厳格運用、東京23区はICT代替可。</p>
<table>
<tr><th>項目</th><th>基準</th><th>判定</th><th>備考</th></tr>
{''.join(rows)}
</table>
"""


def _render_cost_timeline(report: FeasibilityReport) -> str:
    if not report.cost_estimate:
        return ""
    ce = report.cost_estimate
    return f"""
<h2>9. 概算費用・期間</h2>
<p><strong>信頼度：</strong>{_esc(ce.confidence)}</p>
<table>
<tr><th>フェーズ</th><th>費用（万円）</th><th>主な作業</th></tr>
<tr><td>既存調査</td><td>{ce.investigation_cost_min:,} 〜 {ce.investigation_cost_max:,}</td>
<td>{_esc(', '.join(ce.investigation_tasks) or '—')}</td></tr>
<tr><td>確認申請</td><td>{ce.application_cost_min:,} 〜 {ce.application_cost_max:,}</td>
<td>{_esc(', '.join(ce.application_tasks) or '—')}</td></tr>
<tr><td>改修工事</td><td>{ce.renovation_cost_min:,} 〜 {ce.renovation_cost_max:,}</td>
<td>{_esc(', '.join(ce.renovation_tasks) or '—')}</td></tr>
<tr><td><strong>合計</strong></td>
<td><strong>{ce.total_cost_min:,} 〜 {ce.total_cost_max:,}</strong></td>
<td><strong>期間：{ce.total_months_min} 〜 {ce.total_months_max} ヶ月</strong></td></tr>
</table>
"""


def _render_score(score) -> str:
    if score.blocked:
        return f"<h2>10. 総合スコア</h2><p>{_esc(score.blocked_reason)}</p>"
    rows = []
    for item in score.items:
        rows.append(
            f"<tr><td>{_esc(item.category)}</td>"
            f"<td>{_esc(item.label)}</td>"
            f"<td>{item.score:.0f}</td>"
            f"<td>{item.weight:.1f}</td>"
            f"<td>{item.weighted:.0f}</td>"
            f"<td style='font-size:11px;'>{_esc(item.note)}</td></tr>"
        )
    return f"""
<h2>10. 総合スコア詳細</h2>
<p style="font-size:16px;">
<strong>総合スコア：{score.total:.1f} / 100</strong>
<span class="grade-pill grade-{score.grade}" style="margin-left:12px;">{score.grade}</span>
</p>
<table>
<tr><th>カテゴリ</th><th>項目</th><th>スコア</th><th>重み</th><th>寄与</th><th>コメント</th></tr>
{''.join(rows)}
</table>
"""


def _render_todos(report: FeasibilityReport) -> str:
    if not report.todos:
        return ""
    icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    items = []
    for i, t in enumerate(report.todos, 1):
        icon = icons.get(t.priority, "")
        meta = []
        if t.owner:
            meta.append(f"担当: {_esc(t.owner)}")
        if t.estimated_days:
            meta.append(f"想定: {t.estimated_days}日")
        items.append(
            f"<li><strong>{icon} {_esc(t.title)}</strong><br>"
            f"{_esc(t.description)}"
            + (f'<br><span class="step-meta">{" ／ ".join(meta)}</span>' if meta else "")
            + "</li>"
        )
    return f"<h2>11. TODOリスト</h2><ul>{''.join(items)}</ul>"


def _render_missing_docs(report: FeasibilityReport) -> str:
    if not report.missing_documents:
        return ""
    items = "".join(f"<li>📄 {_esc(d)}</li>" for d in report.missing_documents)
    return f"<h2>12. 不足書類</h2><ul>{items}</ul>"


def _render_extracted(report: FeasibilityReport) -> str:
    if not report.extracted_documents:
        return ""
    blocks = []
    for d in report.extracted_documents:
        fields_html = ""
        if d.extracted_fields:
            rows = "".join(
                f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
                for k, v in d.extracted_fields.items()
            )
            fields_html = f"<table>{rows}</table>"
        blocks.append(f"""
<h3>📎 {_esc(d.file_name)}</h3>
<p>書類種別：<span class="badge badge-info">{_esc(d.document_type.value)}</span>
／ 信頼度：{d.confidence:.2f}</p>
{f'<p><em>{_esc(d.raw_text_summary)}</em></p>' if d.raw_text_summary else ''}
{fields_html}
""")
    return f"<h2>13. 抽出書類</h2>{''.join(blocks)}"


def _render_appendix() -> str:
    return """
<h2>付録</h2>

<h3>A. 関係者・連絡先</h3>
<table>
<tr><th>関係者</th><th>役割</th></tr>
<tr><td>一級建築士事務所</td><td>確認申請・図面作成・PM</td></tr>
<tr><td>構造設計事務所</td><td>構造耐力評価・耐火被覆設計</td></tr>
<tr><td>設備設計事務所</td><td>給排水・電気・換気・排煙</td></tr>
<tr><td>指定確認検査機関</td><td>確認申請審査・ガイドライン調査</td></tr>
<tr><td>所轄消防署（予防課）</td><td>消防設備届・消防法令適合通知書</td></tr>
<tr><td>所轄保健所</td><td>旅館業許可・距離規制協議・玄関帳場運用</td></tr>
<tr><td>自治体建築指導課</td><td>用途地域・建ぺい容積率・既存不適格</td></tr>
<tr><td>自治体都市計画課</td><td>地区計画・高度利用・市街化調整区域</td></tr>
<tr><td>行政書士</td><td>旅館業許可申請代行</td></tr>
</table>

<h3>B. 主要根拠条文</h3>
<ul style="font-size:11px;">
<li>建築基準法 48条（用途地域）／27条（耐火）／28条（採光換気）／6条1項1号（特殊建築物確認申請）／87条（用途変更）／20条（構造耐力）／61条（防火地域）</li>
<li>建築基準法施行令 137条の18（類似用途）／119条（廊下幅）／120条（歩行距離）／121条（二方向避難）／126条の2・3（排煙）／126条の4・5（非常用照明）／128条の4・5（内装制限）／112条（防火区画）</li>
<li>消防法施行令 別表第一(5)項イ／11条（屋内消火栓）／12条（SP）／21条（自火報）</li>
<li>旅館業法 3条3項・4項（許可・距離規制）／施行令1条（構造設備基準）</li>
<li>都市計画法 34条（市街化調整区域）／12条の4・5（地区計画）</li>
</ul>

<h3>C. データ取得元</h3>
<ul style="font-size:11px;">
<li>国土交通省 不動産情報ライブラリ API（XKT001/002/006/007/014/023/024）</li>
<li>国土地理院 AddressSearch API（ジオコーディング）</li>
<li>Google Gemini API（書類OCR・構造化抽出）</li>
</ul>
"""


def _render_footer(now: str) -> str:
    return f"""
<footer>
本レポートは AI による自動判定結果です。最終的な法令適合性および事業判断は、
対象自治体・所轄行政庁、および一級建築士による精査が必要です。<br>
生成日時：{_esc(now)} ／ Phase 1 MVP
</footer>
"""
