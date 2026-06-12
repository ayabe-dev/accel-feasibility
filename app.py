"""Streamlit エントリポイント.

用途変更フィジビリティ判定 — Phase 1 MVP
"""
from __future__ import annotations

import os
from typing import List

import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

# --- Streamlit Cloud Secrets を環境変数に流す（クラウド/ローカル両対応） ---
try:
    if hasattr(st, "secrets") and len(st.secrets) > 0:
        for _k, _v in st.secrets.items():
            if _k not in os.environ and isinstance(_v, (str, int, float, bool)):
                os.environ[_k] = str(_v)
except Exception:
    pass

from core import judgment
from core.document_parser import from_streamlit_uploaded_file, parse_document
from core.models import (
    BusinessType,
    ExtractedDocument,
    GeoLookupResult,
    JudgmentLevel,
    ProjectInput,
)
from core.report_generator import generate_markdown_report
from core.report_html import generate_html_report, generate_pdf, is_pdf_available
from core.scoring import DEFAULT_WEIGHTS, compute_score
from guide import render_guide_page


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="用途変更フィジビリティ判定 — Phase 1 MVP",
    page_icon="🏨",
    layout="wide",
)


# ---------------------------------------------------------------------------
# サイドバー：環境チェック
# ---------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.title("🏨 設定")
        provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        gemini_ok = bool(os.getenv("GEMINI_API_KEY"))
        anthropic_ok = bool(os.getenv("ANTHROPIC_API_KEY"))
        openai_ok = bool(os.getenv("OPENAI_API_KEY"))
        reinfolib_ok = bool(os.getenv("REINFOLIB_API_KEY"))
        demo_mode = os.getenv("DEMO_MODE", "true").lower() == "true"

        st.markdown("### LLM (書類解析)")
        provider_label = {
            "gemini": "🟢 Gemini",
            "claude": "🟣 Claude",
            "openai": "🟦 OpenAI",
        }.get(provider, provider)
        active_ok = {
            "gemini": gemini_ok,
            "claude": anthropic_ok,
            "openai": openai_ok,
        }.get(provider, False)
        st.write(f"**プロバイダ**: {provider_label}")
        st.write(f"**APIキー**: {'✅ 設定済み' if active_ok else '❌ 未設定'}")

        st.markdown("### GIS")
        st.write(
            f"**不動産情報ライブラリ**: "
            f"{'✅ 設定済み' if reinfolib_ok else '⚠️ 未設定'}"
        )
        st.write(f"**デモモード**: {'🟢 ON' if demo_mode else '🔴 OFF'}")

        st.divider()
        st.markdown("### デモ用住所サンプル")
        st.code(
            "東京都新宿区西新宿2-8-1\n"
            "東京都渋谷区道玄坂1-1-1\n"
            "東京都世田谷区成城6-5-34（NG例）\n"
            "東京都目黒区中目黒1-1-1\n"
            "東京都港区六本木6-10-1\n"
            "京都府京都市東山区祇園町南側",
            language="text",
        )
        st.caption("デモモードではこれらの住所で動作確認できます。")


# ---------------------------------------------------------------------------
# メイン UI
# ---------------------------------------------------------------------------


def main() -> None:
    render_sidebar()

    st.title("🏨 用途変更フィジビリティ判定 — Phase 1 MVP")
    st.caption(
        "住宅 → 旅館・ホテル営業 の **一次スクリーニング**。"
        "立地の可否・概算費用・期間・不足書類・TODO を即時提示します。"
    )

    # 3タブ構成
    tab_input, tab_eval, tab_rules = st.tabs(
        ["📥 資料投入", "📊 評価結果", "📘 評価ルール詳細"]
    )

    with tab_input:
        render_input_tab()

    with tab_eval:
        if "report" in st.session_state and st.session_state.report is not None:
            render_report(st.session_state.report)
        else:
            st.info(
                "📥 まず「資料投入」タブで物件情報を入力し、判定を実行してください。\n\n"
                "実行後、ここに評価結果が表示されます。"
            )

    with tab_rules:
        render_guide_page()


def render_input_tab() -> None:
    """資料投入タブ：入力フォーム＋実行ボタン."""
    st.header("1. 基本情報を入力")
    col1, col2 = st.columns([2, 1])

    with col1:
        address = st.text_input(
            "物件所在地",
            placeholder="例：東京都新宿区西新宿2-8-1",
            help="住居表示または地番。デモモードでは上記サンプル住所で動作確認可能。",
        )

    with col2:
        business_type = st.selectbox(
            "業態",
            options=[BusinessType.HOTEL_RYOKAN],
            format_func=lambda x: {BusinessType.HOTEL_RYOKAN: "旅館・ホテル営業"}[x],
        )

    with st.expander("既知の情報があれば入力（任意）", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            floor_area = st.number_input(
                "延床面積（㎡）",
                min_value=0.0,
                value=0.0,
                step=10.0,
                help="未入力は 0 のまま。書類があれば自動抽出します。",
            )
            floors_above = st.number_input(
                "地上階数", min_value=0, max_value=100, value=0, step=1
            )
        with c2:
            built_year = st.number_input(
                "建築年（西暦）",
                min_value=0,
                max_value=2100,
                value=0,
                step=1,
            )
            floors_below = st.number_input(
                "地下階数", min_value=0, max_value=10, value=0, step=1
            )
        with c3:
            structure = st.selectbox(
                "構造",
                options=["", "木造", "RC造", "S造", "SRC造", "鉄筋ブロック造", "その他"],
                index=0,
            )
            has_inspection = st.selectbox(
                "検査済証の有無",
                options=["不明", "あり", "なし"],
                index=0,
            )
            renovation = st.selectbox(
                "増改築・大規模修繕の履歴",
                options=["不明", "あり", "なし"],
                index=0,
            )

    # 用途地域・防火地域はREINFOLIB API or 書類OCRから自動取得するため手動入力UIは廃止
    manual_zoning = "自動取得"
    manual_fire = "自動取得"

    # ── 距離規制 ───────────────────────────────────────
    with st.expander("距離規制チェック（学校等100m以内）", expanded=False):
        nearby_choice = st.radio(
            "周辺の対象施設",
            options=["不明", "あり", "なし"],
            horizontal=True,
        )
        nearby_facilities_text = ""
        if nearby_choice == "あり":
            nearby_facilities_text = st.text_input(
                "施設名（カンマ区切り）",
                placeholder="○○小学校, △△保育園",
            )

    # ── 追加情報（自由記述＋サジェスト） ────────────────
    with st.expander(
        "💬 追加情報・特記事項（任意・LLMが評価に反映します）",
        expanded=False,
    ):
        st.caption(
            "**ヒント：** 物件の元用途・既存設備・周辺状況・リスク要因など、"
            "判定に影響する情報があれば自由記述してください。Geminiが解釈して、"
            "評価結果の補正コメントを生成します。"
        )

        from core.context_interpreter import CONTEXT_SUGGESTIONS

        st.markdown("**💡 こんな情報があると判定が変わります：**")
        sugg_cols = st.columns(2)
        for i, sugg in enumerate(CONTEXT_SUGGESTIONS):
            with sugg_cols[i % 2]:
                with st.container(border=True):
                    st.markdown(f"**{sugg['title']}**")
                    for ex in sugg["examples"]:
                        st.caption(f"・{ex}")

        additional_context = st.text_area(
            "特記事項（自由記述）",
            placeholder=(
                "例：元は内科クリニックだったため、二方向避難・自火報・非常用照明・"
                "防火区画が既に整備されている。上階2階以上に住民が住んでおり、"
                "異種用途区画と動線分離の調整が必要。最寄り駅徒歩3分で立地良好、"
                "インバウンド需要を見込める。"
            ),
            height=140,
            help="LLM が解釈して、Phase 4 建基法、Phase 5 消防、コスト、事業性への影響を分析します",
        )
    if not "additional_context" in dir() or additional_context is None:
        additional_context = ""

    # ── ファイルアップロード ────────────────────────────
    st.header("2. 物件資料をアップロード（任意）")
    st.caption(
        "重要事項説明書・確認済証・検査済証・登記簿・既存図面 等。"
        "Gemini API で書類種別と重要フィールドを自動抽出します。"
    )
    uploaded_files = st.file_uploader(
        "PDF / 画像（PNG, JPEG）",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="file_uploader",
    )

    # 抽出結果プレビュー＋編集
    if uploaded_files:
        st.markdown("---")
        st.markdown("### 📋 資料から読み取った内容（プレビュー＋編集）")
        st.caption(
            "AIが書類から抽出した内容を、判定前に確認・修正できます。"
            "図面のスキャンミス等があれば、ここで人手で正しい値に直してください。"
        )

        if st.button(
            "🔍 資料を読み取る（プレビュー）",
            help="アップロードした資料を Gemini で解析し、抽出フィールドを一覧表示します。"
            "判定実行の前に内容を確認・修正できます。",
        ):
            with st.spinner("AIが資料を解析中..."):
                preview_docs = []
                for uf in uploaded_files:
                    doc = parse_document(from_streamlit_uploaded_file(uf))
                    preview_docs.append(doc)
                st.session_state.extracted_docs_preview = preview_docs
                # 編集状態をリセット
                if "doc_field_edits" in st.session_state:
                    del st.session_state["doc_field_edits"]
            st.success(f"✅ {len(preview_docs)}件の資料を解析しました。下記で内容を確認してください。")

        if "extracted_docs_preview" in st.session_state:
            edited_docs = _render_extraction_editor(
                st.session_state.extracted_docs_preview
            )
            st.session_state.edited_docs = edited_docs

    # ── 実行ボタン ────────────────────────────
    st.header("3. 判定実行")
    if st.button("🔍 一次スクリーニングを実行", type="primary", use_container_width=True):
        if not address and not uploaded_files:
            st.error("住所を入力するか、物件資料（マイソク等）をアップロードしてください。")
            return
        provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        provider_key = {
            "gemini": "GEMINI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(provider, "GEMINI_API_KEY")
        if not address and not os.getenv(provider_key):
            st.error(
                f"{provider_key} が未設定なので書類から住所を抽出できません。"
                f"住所を手動で入力するか、.env に {provider_key} を設定してください。"
            )
            return

        with st.spinner("書類解析と判定を実行中..."):
            # ProjectInput を組み立て
            project = ProjectInput(
                address=address,
                business_type=business_type,
                floor_area_m2=float(floor_area) if floor_area > 0 else None,
                floors_above=int(floors_above) if floors_above > 0 else None,
                floors_below=int(floors_below) if floors_below > 0 else None,
                structure=structure if structure else None,
                built_year=int(built_year) if built_year > 0 else None,
                has_inspection_certificate=_yesno(has_inspection),
                renovation_history=_yesno(renovation),
                additional_context=additional_context if additional_context else None,
            )

            # 書類解析
            #   1. 編集済み（プレビューで人手修正済）があればそれを最優先
            #   2. なければ新規解析
            docs: List[ExtractedDocument] = []
            if "edited_docs" in st.session_state and st.session_state.edited_docs:
                docs = st.session_state.edited_docs
                st.info(
                    f"📝 プレビューで確認・修正済の {len(docs)}件の資料を判定に使用します。"
                )
            elif uploaded_files:
                progress = st.progress(0.0, text="書類を解析中...")
                for i, uf in enumerate(uploaded_files):
                    progress.progress(
                        (i) / len(uploaded_files),
                        text=f"解析中: {uf.name}",
                    )
                    doc = parse_document(from_streamlit_uploaded_file(uf))
                    docs.append(doc)
                progress.progress(1.0, text="解析完了")

            # 手動GIS入力
            manual_geo = None
            if manual_zoning != "自動取得":
                from api import gis_client

                manual_geo = gis_client.from_manual_input(
                    address=address,
                    zoning_code=manual_zoning,
                    fire_district=(
                        manual_fire if manual_fire != "自動取得" else None
                    ),
                )

            # 距離規制
            nearby_facilities = [
                s.strip() for s in nearby_facilities_text.split(",") if s.strip()
            ]

            # 総合判定
            report = judgment.run(
                project=project,
                docs=docs,
                manual_geo=manual_geo,
                has_nearby_facility=_yesno(nearby_choice),
                nearby_facilities=nearby_facilities,
            )

        # Session State に保存して評価タブで表示
        st.session_state.report = report
        st.success(
            "✅ 判定完了！「📊 評価結果」タブに移動して結果を確認してください。"
        )
        st.balloons()


def render_report(report) -> None:
    st.divider()
    st.header("4. 判定結果")

    # スコア計算（重みは Session State から取得）
    weights = st.session_state.get("weights", DEFAULT_WEIGHTS)
    score = compute_score(report, weights=weights)

    # レポートのダウンロード（HTML / PDF / Markdown）
    from datetime import datetime as _dt

    md_report = generate_markdown_report(report, weights=weights)
    html_report = generate_html_report(report, weights=weights)
    fname_safe_addr = (
        (report.input.address or "report").replace("/", "_").replace(" ", "_")[:30]
    )
    timestamp = _dt.now().strftime("%Y%m%d_%H%M")
    fname_base = f"feasibility_{fname_safe_addr}_{timestamp}"

    st.markdown("### 📥 レポート出力")
    dl_col1, dl_col2, dl_col3 = st.columns(3)
    with dl_col1:
        st.download_button(
            label="🌐 HTML（ブラウザで開く）",
            data=html_report.encode("utf-8"),
            file_name=f"{fname_base}.html",
            mime="text/html",
            use_container_width=True,
            help="ダブルクリックでブラウザに表示。Cmd+P で PDF 化も可能",
        )
    with dl_col2:
        pdf_bytes = generate_pdf(report, weights=weights)
        if pdf_bytes:
            st.download_button(
                label="📑 PDF（印刷向け）",
                data=pdf_bytes,
                file_name=f"{fname_base}.pdf",
                mime="application/pdf",
                use_container_width=True,
                help="weasyprintで自動生成。レイアウト崩れ時はHTML+ブラウザ印刷を推奨",
            )
        else:
            st.button(
                "📑 PDF（要weasyprint）",
                disabled=True,
                use_container_width=True,
                help="weasyprint未インストール。HTMLをブラウザで開いて Cmd+P で PDF 化してください",
            )
    with dl_col3:
        st.download_button(
            label="📝 Markdown（編集用）",
            data=md_report.encode("utf-8"),
            file_name=f"{fname_base}.md",
            mime="text/markdown",
            use_container_width=True,
            help="編集・差分管理・社内Wikiへの貼り付けに",
        )
    if not is_pdf_available():
        st.caption(
            "💡 PDFを直接生成するには `pip install weasyprint` が必要です。"
            "未インストールの場合は HTML をダウンロード→ブラウザで開く→ Cmd+P で PDF保存できます。"
        )

    # 総合判定カード
    level_color = {
        JudgmentLevel.GO: "🟢",
        JudgmentLevel.CONDITIONAL: "🟡",
        JudgmentLevel.NO_GO: "🔴",
    }
    level_label = {
        JudgmentLevel.GO: "ほぼ可能",
        JudgmentLevel.CONDITIONAL: "条件付き可能",
        JudgmentLevel.NO_GO: "立地不可（NO-GO）",
    }
    st.subheader(
        f"{level_color[report.overall_level]} 総合判定：{level_label[report.overall_level]}"
    )
    st.info(report.overall_summary)

    # 追加情報による補正コメント
    if report.context_impact is not None:
        ci = report.context_impact
        if ci.warning:
            st.warning(f"💬 追加情報の解釈：{ci.warning}")
        else:
            with st.container(border=True):
                st.markdown("### 💬 追加情報の評価への影響")
                if ci.overall_summary:
                    st.info(ci.overall_summary)
                dir_color = {"プラス": "🟢", "マイナス": "🔴", "中立": "⚪"}
                for impact in ci.impacts:
                    icon = dir_color.get(impact.direction, "⚪")
                    sign = "+" if impact.score_adjustment_hint > 0 else ""
                    st.markdown(
                        f"{icon} **{impact.aspect}**（{impact.direction}・スコア影響目安: "
                        f"{sign}{impact.score_adjustment_hint}）"
                    )
                    st.caption(impact.summary)
                    if impact.affected_rules:
                        st.caption(
                            "_影響項目：_ " + "、".join(impact.affected_rules)
                        )
                if ci.suggested_additional_questions:
                    with st.expander("💡 更に評価を精緻化するための追加質問"):
                        for q in ci.suggested_additional_questions:
                            st.write(f"- {q}")

    # 主要指標 5列（スコア追加）
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("用途地域", report.geo.zoning_name or "未取得")
    with c2:
        st.metric("防火地域", _fire_label(report.geo.fire_district or "no_district"))
    with c3:
        st.metric("調査パターン", report.pattern.value)
    with c4:
        if report.cost_estimate:
            ce = report.cost_estimate
            st.metric(
                "総コスト目安（万円）",
                f"{ce.total_cost_min:,} 〜 {ce.total_cost_max:,}",
            )
    with c5:
        if score.blocked:
            st.metric("総合スコア", "ブロック")
        else:
            st.metric("総合スコア", f"{score.total:.1f} / 100", delta=f"Grade {score.grade}")

    # 詳細タブ
    tab_score, tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(
        [
            "🎯 スコア",
            "📍 立地",
            "🏗️ パターン判定",
            "💰 費用・期間",
            "📋 確認申請",
            "⚖️ 建基法チェック",
            "🚒 消防・旅館業",
            "📝 TODO・不足書類",
            "📄 抽出書類",
        ]
    )

    with tab_score:
        if score.blocked:
            st.error(f"🔴 立地が法律上不可のためスコア計算をブロックしています：{score.blocked_reason}")
        else:
            st.markdown(f"### 総合スコア：**{score.total:.1f}** / 100  （グレード **{score.grade}**）")
            st.caption(
                "ガイドページのスライダーで重みを変更できます。"
                "重視したい観点（コスト/安全/立地等）で総合評価を調整可能。"
            )
            # カテゴリ別に集計
            from collections import defaultdict
            cat_items = defaultdict(list)
            for item in score.items:
                cat_items[item.category].append(item)
            for cat, items in cat_items.items():
                cat_score = (
                    sum(i.weighted for i in items) / (sum(i.weight for i in items) or 1.0)
                )
                with st.expander(f"📂 {cat} （カテゴリ平均：{cat_score:.1f}）", expanded=True):
                    for item in items:
                        bar = "🟩" * int(item.score / 10) + "⬜" * (10 - int(item.score / 10))
                        st.markdown(
                            f"**{item.label}** （重み: {item.weight:.1f}） — {bar} {item.score:.0f}"
                        )
                        st.caption(item.note)

    with tab1:
        st.markdown("#### 立地・用途地域")
        st.write(f"**住所**: {report.geo.address}")
        st.write(f"**用途地域**: {report.geo.zoning_name or '取得失敗'}")
        st.write(
            f"**建ぺい率 / 容積率**: "
            f"{report.geo.coverage_ratio_pct or '?'}% / "
            f"{report.geo.floor_area_ratio_pct or '?'}%"
        )
        st.write(f"**取得元**: {report.geo.source}")
        st.write(f"**判定根拠**: {report.zoning.reason}")
        if report.zoning.floor_area_check:
            st.write(f"**床面積チェック**: {report.zoning.floor_area_check}")

        st.markdown("#### 都市計画情報")
        c1, c2, c3 = st.columns(3)
        with c1:
            cpc = report.geo.city_planning_classification
            if cpc and "市街化調整区域" in cpc:
                st.error(f"🔴 {cpc}")
            elif cpc:
                st.success(f"✅ {cpc}")
            else:
                st.info("区域区分: 取得不可")
        with c2:
            dp = report.geo.district_plan_name
            if dp:
                st.warning(f"📐 地区計画: {dp}")
            else:
                st.info("地区計画: 指定なし")
        with c3:
            hu = report.geo.high_use_district_name
            if hu:
                st.info(f"🏙️ 高度利用: {hu}")
            else:
                st.info("高度利用地区: 指定なし")

        st.markdown("#### 距離規制（学校・保育園 100m以内）")
        if report.geo.nearby_facilities:
            st.error(f"🔴 半径100m以内に {len(report.geo.nearby_facilities)} 件の対象施設あり")
            for f in report.geo.nearby_facilities:
                st.write(
                    f"- **{f.name}**（{f.facility_type}） 約 {f.distance_m:.0f}m "
                    f"／ {f.address}"
                )
        else:
            st.success("✅ 半径100m以内に学校・保育園・幼稚園は確認されず")

        if report.distance and report.distance.has_issue:
            st.warning(report.distance.note)

    with tab2:
        st.markdown(f"#### 調査パターン：**{report.pattern.value}**")
        st.write(report.pattern_reason)
        if report.warnings:
            st.markdown("#### ⚠️ 注意事項")
            for w in report.warnings:
                st.warning(w)

    with tab3:
        if report.cost_estimate is None:
            st.info("パターン判定が不能のため、費用見積をスキップしました。")
        else:
            ce = report.cost_estimate
            st.markdown(f"**信頼度**：{ce.confidence}")
            st.markdown("##### 費用レンジ（万円）")
            st.table(
                {
                    "項目": ["既存調査", "確認申請", "改修工事", "**合計**"],
                    "Min": [
                        ce.investigation_cost_min,
                        ce.application_cost_min,
                        ce.renovation_cost_min,
                        ce.total_cost_min,
                    ],
                    "Max": [
                        ce.investigation_cost_max,
                        ce.application_cost_max,
                        ce.renovation_cost_max,
                        ce.total_cost_max,
                    ],
                }
            )
            st.markdown(
                f"##### 期間目安：**{ce.total_months_min} 〜 {ce.total_months_max} か月**"
            )

            st.markdown("##### 主な作業内容")
            with st.expander("調査フェーズ"):
                for t in ce.investigation_tasks:
                    st.write(f"- {t}")
            with st.expander("申請フェーズ"):
                for t in ce.application_tasks:
                    st.write(f"- {t}")
            with st.expander("工事フェーズ"):
                for t in ce.renovation_tasks:
                    st.write(f"- {t}")

    with tab4:
        st.markdown("#### 用途変更確認申請の要否")
        if report.application_requirement is None:
            st.info("判定情報が不足しています。")
        else:
            ar = report.application_requirement
            if ar.required:
                st.error(f"📋 **確認申請：必要**")
            else:
                st.success(f"📋 **確認申請：不要**（ただし遡及あり）")
            st.write(ar.reason)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("特殊建築物", "該当" if ar.is_special_building else "非該当")
            with c2:
                st.metric("類似用途", "該当" if ar.is_similar_use else "非該当")
            with c3:
                fa = ar.floor_area_subject_m2
                st.metric(
                    "対象床面積",
                    f"{fa:,.0f}㎡" if fa else "未確定",
                )
            with st.expander("根拠条文"):
                for art in ar.applicable_articles:
                    st.write(f"- {art}")
            if ar.related_obligations:
                with st.expander("確認申請が不要でも遡及する規定"):
                    for ob in ar.related_obligations:
                        st.write(f"- {ob}")

    with tab5:
        st.markdown("#### 建築基準法 技術基準チェック")
        if not report.building_code_checks:
            st.info("チェック未実行。")
        else:
            status_icon = {
                "compliant": "✅",
                "non_compliant": "❌",
                "needs_review": "🔍",
                "not_applicable": "⚪",
                "unknown": "❓",
            }
            for c in report.building_code_checks:
                icon = status_icon.get(c.status.value, "❓")
                with st.expander(f"{icon} {c.rule_name} ({c.article})"):
                    st.markdown(f"**要求**：{c.requirement}")
                    if c.current:
                        st.markdown(f"**現況**：{c.current}")
                    if c.recommended_action:
                        st.markdown(f"**推奨対応**：{c.recommended_action}")
                    st.caption(
                        f"ステータス: {c.status.value} / 影響度: {c.impact}"
                    )

                    # 対応オプション詳細（不適合・要確認時）
                    if c.options:
                        st.divider()
                        st.markdown("### 🛠️ 対応オプション（費用・期間・TODO）")
                        st.caption(
                            "各案の費用・期間は規模と現況により変動します。"
                            "選定にあたっては一級建築士事務所と相談してください。"
                        )
                        for i, opt in enumerate(c.options, 1):
                            with st.container(border=True):
                                st.markdown(f"#### {opt.title}")
                                if opt.summary:
                                    st.markdown(opt.summary)

                                col1, col2 = st.columns(2)
                                with col1:
                                    st.metric(
                                        "💰 概算費用",
                                        f"{opt.cost_min_man:,} 〜 {opt.cost_max_man:,} 万円",
                                    )
                                with col2:
                                    st.metric(
                                        "⏱️ 概算期間",
                                        f"{opt.duration_min_months} 〜 {opt.duration_max_months} ヶ月",
                                    )

                                if opt.pros or opt.cons:
                                    pcol1, pcol2 = st.columns(2)
                                    with pcol1:
                                        if opt.pros:
                                            st.markdown("**✅ メリット**")
                                            for p in opt.pros:
                                                st.markdown(f"- {p}")
                                    with pcol2:
                                        if opt.cons:
                                            st.markdown("**⚠️ デメリット**")
                                            for d in opt.cons:
                                                st.markdown(f"- {d}")

                                if opt.steps:
                                    st.markdown("**📋 ステップ別 TODO**")
                                    for j, step in enumerate(opt.steps, 1):
                                        with st.container(border=False):
                                            st.markdown(f"**{step.title}**")
                                            if step.description:
                                                st.caption(step.description)
                                            meta_parts = []
                                            if step.days:
                                                meta_parts.append(f"⏱️ 約{step.days}日")
                                            if step.cost_min_man is not None and step.cost_max_man is not None:
                                                if step.cost_max_man > 0:
                                                    meta_parts.append(
                                                        f"💰 {step.cost_min_man}〜{step.cost_max_man}万円"
                                                    )
                                            if step.owner:
                                                meta_parts.append(f"👤 {step.owner}")
                                            if meta_parts:
                                                st.caption(" ／ ".join(meta_parts))

                                if opt.contact:
                                    st.markdown(f"**📞 連絡先・関係者**：{opt.contact}")

    with tab6:
        st.markdown("#### 消防法 設備チェック")
        for f in report.fire_safety_checks:
            with st.expander(
                f"{'🟢 必要' if f.required else '⚪ 不要'} {f.equipment_name} ({f.article})"
            ):
                st.markdown(f"**閾値**：{f.threshold_note}")
                if f.current_status:
                    st.markdown(f"**現況**：{f.current_status}")
                if f.action:
                    st.markdown(f"**アクション**：{f.action}")

        st.divider()
        st.markdown("#### 旅館業法 構造設備基準")
        for l in report.lodging_business_checks:
            with st.expander(f"📌 {l.item_name}"):
                st.markdown(f"**基準**：{l.standard}")
                if l.note:
                    st.markdown(f"**備考**：{l.note}")

    with tab7:
        st.markdown("#### 不足書類")
        if report.missing_documents:
            for label in report.missing_documents:
                st.warning(f"📄 {label}")
        else:
            st.success("必要書類は揃っています。")

        st.markdown("#### TODO リスト")
        if not report.todos:
            st.info("TODO はありません。")
        else:
            for i, todo in enumerate(report.todos, 1):
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                    todo.priority, "⚪"
                )
                with st.expander(f"{icon} {i}. {todo.title}"):
                    st.write(todo.description)
                    st.caption(
                        f"担当: {todo.owner} / "
                        f"想定: {todo.estimated_days or '?'} 日"
                    )

    with tab8:
        if not report.extracted_documents:
            st.info("アップロードされた書類はありません。")
        else:
            for d in report.extracted_documents:
                with st.expander(f"📎 {d.file_name} → {d.document_type.value}"):
                    st.write(f"**信頼度**: {d.confidence:.2f}")
                    if d.raw_text_summary:
                        st.write(f"**サマリ**: {d.raw_text_summary}")
                    if d.extracted_fields:
                        st.json(d.extracted_fields)
                    if d.warnings:
                        for w in d.warnings:
                            st.warning(w)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _render_extraction_editor(
    docs: List[ExtractedDocument],
) -> List[ExtractedDocument]:
    """抽出済み書類の各フィールドを編集可能に表示し、修正版を返す."""
    from copy import deepcopy

    # 主要フィールドの順序＋ラベル
    field_order = [
        ("address", "所在地", "str"),
        ("property_type", "物件種別", "str"),
        ("structure", "構造", "str"),
        ("floors_above", "地上階数", "int"),
        ("floors_below", "地下階数", "int"),
        ("total_floor_area_m2", "延床面積（㎡）", "float"),
        ("building_area_m2", "建築面積（㎡）", "float"),
        ("site_area_m2", "敷地面積（㎡）", "float"),
        ("zoning", "用途地域", "str"),
        ("fire_district", "防火地域", "str"),
        ("coverage_ratio_pct", "建ぺい率（%）", "float"),
        ("floor_area_ratio_pct", "容積率（%）", "float"),
        ("city_planning", "都市計画", "str"),
        ("built_year", "建築年（西暦）", "int"),
        ("built_month", "建築月", "int"),
        ("total_units", "総戸数", "int"),
        ("inspection_obtained", "検査済証", "bool"),
        ("confirmation_obtained", "建築確認取得", "bool"),
        ("road_width_m", "接道幅員（m）", "float"),
        ("layout_summary", "間取り", "str"),
        ("constructor", "施工会社", "str"),
        ("price_jpy_man", "価格（万円）", "int"),
        ("yield_pct", "想定利回り（%）", "float"),
        ("renovation_history", "改修履歴", "str"),
    ]

    edited_docs = []
    for doc_idx, doc in enumerate(docs):
        with st.container(border=True):
            doc_type_label = {
                "real_estate_flyer": "📄 マイソク",
                "inspection_certificate": "📋 検査済証",
                "confirmation_certificate": "📋 確認済証",
                "building_plan_summary": "📋 建築計画概要書",
                "important_matters": "📋 重要事項説明書",
                "land_registry": "📋 登記簿（土地）",
                "building_registry": "📋 登記簿（建物）",
                "sales_contract": "📋 売買契約書",
                "design_drawing": "📐 設計図書",
                "structural_calc": "📐 構造計算書",
                "other": "📎 その他",
            }.get(doc.document_type.value, "📎 書類")

            st.markdown(
                f"#### {doc_type_label}：{doc.file_name}"
                f"　_（信頼度 {doc.confidence:.2f}）_"
            )
            if doc.raw_text_summary:
                st.caption(f"💬 {doc.raw_text_summary}")
            if doc.warnings:
                for w in doc.warnings:
                    st.warning(f"⚠️ {w}")

            new_fields = dict(doc.extracted_fields)

            # 抽出されたフィールドだけ編集UI表示（順序維持）
            present_fields = [
                (k, label, ftype)
                for k, label, ftype in field_order
                if k in new_fields
            ]
            # その他のフィールド（順序リストにないもの）
            other_fields = [
                (k, k, "str")
                for k in new_fields
                if k not in {f[0] for f in field_order}
            ]
            all_fields = present_fields + other_fields

            if not all_fields:
                st.info("抽出されたフィールドはありません。")
                edited_docs.append(doc)
                continue

            # 2列レイアウト
            for i in range(0, len(all_fields), 2):
                cols = st.columns(2)
                for j, col in enumerate(cols):
                    if i + j >= len(all_fields):
                        break
                    key, label, ftype = all_fields[i + j]
                    original_value = new_fields.get(key)
                    field_key = f"edit_doc{doc_idx}_{key}"

                    with col:
                        new_value = _render_field_editor(
                            label, original_value, ftype, field_key
                        )
                        new_fields[key] = new_value

            # 修正後の ExtractedDocument を作る
            edited_doc = deepcopy(doc)
            edited_doc.extracted_fields = new_fields
            edited_docs.append(edited_doc)

    if edited_docs:
        st.success(
            "✅ 上記の値を確認・修正したうえで、下の「一次スクリーニングを実行」を押してください。"
            "修正値が判定に反映されます。"
        )

    return edited_docs


def _render_field_editor(label: str, value, ftype: str, key: str):
    """フィールド型に応じた編集UI."""
    if ftype == "int":
        try:
            current = int(value) if value not in (None, "") else 0
        except (ValueError, TypeError):
            current = 0
        new_val = st.number_input(label, value=current, step=1, key=key)
        return new_val if new_val != 0 else None
    if ftype == "float":
        try:
            current = float(value) if value not in (None, "") else 0.0
        except (ValueError, TypeError):
            current = 0.0
        new_val = st.number_input(
            label, value=current, step=0.1, format="%.2f", key=key
        )
        return new_val if new_val != 0.0 else None
    if ftype == "bool":
        options = ["不明", "有", "無"]
        if value is True:
            idx = 1
        elif value is False:
            idx = 2
        else:
            idx = 0
        choice = st.selectbox(label, options, index=idx, key=key)
        return {"有": True, "無": False, "不明": None}[choice]
    # str
    current_str = "" if value is None else str(value)
    new_val = st.text_input(label, value=current_str, key=key)
    return new_val if new_val else None


def _yesno(value: str):
    return {"あり": True, "なし": False, "不明": None}.get(value)


def _zoning_label(code: str) -> str:
    if code == "自動取得":
        return "自動取得"
    labels = {
        "first_low_residential": "第一種低層住居専用地域",
        "second_low_residential": "第二種低層住居専用地域",
        "rural_residential": "田園住居地域",
        "first_mid_residential": "第一種中高層住居専用地域",
        "second_mid_residential": "第二種中高層住居専用地域",
        "first_residential": "第一種住居地域",
        "second_residential": "第二種住居地域",
        "quasi_residential": "準住居地域",
        "neighborhood_commercial": "近隣商業地域",
        "commercial": "商業地域",
        "quasi_industrial": "準工業地域",
        "industrial": "工業地域",
        "exclusive_industrial": "工業専用地域",
        "non_zoned": "用途地域指定なし",
    }
    return labels.get(code, code)


def _fire_label(code: str) -> str:
    labels = {
        "自動取得": "自動取得",
        "fire_district": "防火地域",
        "quasi_fire_district": "準防火地域",
        "no_district": "指定なし",
    }
    return labels.get(code, code)


if __name__ == "__main__":
    main()
