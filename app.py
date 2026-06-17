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
from core import profitability
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

    # トップは2軸（①旅館業が取れるか ②収益化できるか）＋ 財務・銀行 / 計算ロジック / ルール
    (tab_input, tab_feas, tab_money, tab_finance,
     tab_algo, tab_rules) = st.tabs(
        [
            "📥 資料投入",
            "🏨 ①旅館業が取れるか",
            "💹 ②収益化できるか",
            "🏦 財務・銀行",
            "📐 収益計算の仕組み",
            "📘 評価ルール詳細",
        ]
    )

    def _need_report():
        st.info(
            "📥 まず「資料投入」タブで物件情報を入力し、判定を実行してください。\n\n"
            "実行後、ここに結果が表示されます。"
        )

    with tab_input:
        render_input_tab()

    with tab_feas:
        if st.session_state.get("report") is not None:
            render_report(st.session_state.report)
        else:
            _need_report()

    with tab_money:
        if st.session_state.get("report") is not None:
            render_monetization_tab(st.session_state.report)
        else:
            _need_report()

    with tab_finance:
        if st.session_state.get("report") is not None:
            render_finance_bank_tab(st.session_state.report)
        else:
            _need_report()

    with tab_algo:
        render_algorithm_page()

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


# ---------------------------------------------------------------------------
# 収益化タブ（②収益化できるか）
# ---------------------------------------------------------------------------


def _profit_overrides() -> dict:
    """収益化タブの前提上書きウィジェット → overrides dict."""
    with st.expander("⚙️ 前提を調整（任意・空欄ならエリア相場で自動）", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            land_area = st.number_input("土地面積（㎡）", min_value=0.0, value=0.0, step=10.0, key="prof_land_area")
            room_area = st.number_input("1室面積（㎡）", min_value=0.0, value=0.0, step=1.0, key="prof_room_area")
        with c2:
            price = st.number_input("販売価格（売出・万円）", min_value=0.0, value=0.0, step=100.0, key="prof_price",
                                    help="売出/取得想定価格。相場との割安・割高判定とCFに使用")
            ltv = st.slider("LTV（借入比率）", 0.0, 1.0, 0.70, 0.05, key="prof_ltv")
        with c3:
            loan_rate = st.slider("借入金利", 0.0, 0.08, 0.025, 0.005, key="prof_rate")
            loan_term = st.number_input("借入期間（年）", min_value=1, max_value=50, value=25, step=1, key="prof_term")
        exit_year = st.slider("出口（売却）想定年", 1, 30, 10, 1, key="prof_exit")
        st.markdown("**Airbnb/民泊 相場（手動・任意）** — 近隣のAirbnb/民泊のADR・稼働率を入力すると収益試算に反映")
        ac1, ac2 = st.columns(2)
        with ac1:
            adr = st.number_input("ADR 客室単価（円/泊）", min_value=0, value=0, step=1000, key="prof_adr")
        with ac2:
            occ = st.slider("想定稼働率（%）", 0, 100, 0, 5, key="prof_occ")
    return {
        "land_area_m2": land_area or None,
        "room_area_m2": room_area or None,
        "purchase_price_man": price or None,
        "ltv": ltv, "loan_rate": loan_rate, "loan_term_years": int(loan_term),
        "exit_year": int(exit_year),
        "adr_yen": (adr or None),
        "occupancy_input": (occ / 100.0) if occ else None,
    }


def _get_profit_result(report, overrides=None):
    """収益性結果を計算しsession_stateにも保持."""
    res = profitability.compute(report, overrides=overrides or {})
    report.profitability = res
    st.session_state["prof_res"] = res
    return res


def _render_cf_projection(proj, label):
    rows = proj["rows"]
    st.markdown(f"#### {label}：年次キャッシュフロー（税引前・標準シナリオ）")
    table = {
        "年": [r["year"] for r in rows],
        "NOI(万円)": [f"{r['noi']:,.0f}" for r in rows],
        "年間CF(万円)": [f"{r['annual_cf']:,.0f}" for r in rows],
        "累計CF(万円)": [f"{r['cumulative_cf']:,.0f}" for r in rows],
        "ローン残債(万円)": [f"{r['loan_balance']:,.0f}" for r in rows],
        "売却時純利益(万円)": [f"{r['net_profit_if_sell']:,.0f}" for r in rows],
    }
    st.table(table)
    m1, m2 = st.columns(2)
    with m1:
        st.metric(f"{label} 累計CF（万円）", f"{proj['cumulative_cf']:,.0f}")
    with m2:
        st.metric(f"{label} 末に売却した場合の純利益（万円）", f"{proj['net_profit_if_sell_end']:,.0f}",
                  help="運営CF累計＋売却純手取り−自己資金（税引前）")
    st.caption("NOIは横ばい前提（成長0%）。売却時純利益＝累計CF＋売却純手取り−自己資金。すべて税引前の試算。")


def render_monetization_tab(report) -> None:
    """②収益化できるか — 価値・複数年CF・感度."""
    st.markdown("## 💹 ②収益化できるか")
    st.caption("⚠️ 前提明示型の試算（estimate）です。鑑定評価・融資審査の代替ではありません。数値はレンジで確認してください。")

    overrides = _profit_overrides()
    try:
        res = _get_profit_result(report, overrides)
    except Exception as e:  # noqa: BLE001
        st.error(f"収益性計算でエラー：{e}")
        return
    for w in res.get("warnings", []):
        st.warning(w)

    sub_sum, sub5, sub10, sub_sens = st.tabs(
        ["📌 価値・サマリ", "📅 5年キャッシュフロー", "📅 10年キャッシュフロー", "🎚️ 感度"]
    )

    with sub_sum:
        v = res["valuation"]
        st.markdown("### ① この物件は割安か割高か（相場 vs 販売価格）")
        mv = v["market_value_man"]
        if v["asking_price_man"]:
            vc = st.columns(3)
            vc[0].metric("販売価格（売出）", f"{v['asking_price_man']:,.0f}万円")
            vc[1].metric("想定適正価格(mid)", f"{mv['mid']:,.0f}万円",
                         help=f"相場レンジ {mv['min']:,.0f}〜{mv['max']:,.0f}万円")
            d = v["gap_pct"]
            vc[2].metric("判定", v["price_verdict"],
                         delta=(f"相場mid比 {d:+.1f}%" if d is not None else None), delta_color="inverse")
            cost_txt = f"{v['cost_value_man']:,.0f}万円" if v["cost_value_man"] else "土地面積未入力"
            st.caption(f"基準：{v['basis']}（収益価格[A] {v['income_value_mid_man']:,.0f}万円 ／ 原価法[B] {cost_txt}）")
            if v.get("asking_land_per_tsubo_man"):
                tl = v["tier_land_per_tsubo_man"]
                st.caption(
                    f"参考・土地坪単価：販売価格ベース {v['asking_land_per_tsubo_man']:,.0f}万円/坪 ↔ "
                    f"エリア相場 {tl['min']}〜{tl['max']}万円/坪"
                )
        else:
            st.info(
                "「⚙️ 前提を調整」で **販売価格** を入力すると、相場との割安/割高を判定します。"
                f"（現在の想定適正価格 mid {mv['mid']:,.0f}万円・レンジ {mv['min']:,.0f}〜{mv['max']:,.0f}万円）"
            )
        st.divider()
        st.markdown("### ② 物件価値とNOIのサマリ")
        cols = st.columns(4)
        iv = res["income_value_man"]; noi = res["noi"]; cv = res["cost_value_man"]
        with cols[0]:
            st.metric("収益価格[A]（万円・mid）", f"{iv['mid']:,.0f}", help=f"レンジ {iv['min']:,.0f}〜{iv['max']:,.0f}（Inwood有期還元）")
        with cols[1]:
            st.metric("原価法[B]（万円）", f"{cv:,.0f}" if cv is not None else "土地面積要入力")
        with cols[2]:
            st.metric("NOI（万円・mid）", f"{noi['mid']:,.0f}", help=f"レンジ {noi['min']:,.0f}〜{noi['max']:,.0f}")
        with cols[3]:
            st.metric("儲かりやすさ", res["verdict"])
        st.caption(
            f"エリア:{res['area_tier']}／構造:{res['structure']}／業態:{res['business_type']}"
            f"／営業日:{res['operating_days_used']}日／客室:{res['rooms']}室"
            f"／残存耐用:{res['remaining_useful_life_years']}年／RevPAR:{res['revpar_source']}／設定:{res['config_version']}"
        )
        with st.expander("📊 NOI内訳（USALI階層・mid）"):
            nb = res["noi_breakdown_mid"]
            st.table({
                "項目": ["GPI(総収入)", "EGI(実効総収入)", "−変動費", "GOP前", "−固定費", "GOP", "−FF&E積立", "= NOI"],
                "万円/年": [nb["gpi"], nb["egi"], nb["variable"], nb["gop_pre"], nb["fixed"], nb["gop"], nb["ffe"], nb["noi"]],
            })
        if res.get("theoretical_max_floor"):
            th = res["theoretical_max_floor"]
            with st.expander("🏗️ 容積ポテンシャル（参考値・要鑑定）"):
                st.write(
                    f"現況容積消化率 **{th['current_consumption_pct']}%** ／ 指定容積率 {th['designated_far_pct']}% ／ "
                    f"理論最大床 **{th['theoretical_max_floor_m2']:,.0f}㎡**"
                    + (f"（余地 約{th['headroom_x']}倍）" if th.get("headroom_x") else "")
                )
                st.caption(th["note"])
        else:
            st.caption("※ 容積ポテンシャルは土地面積の入力で表示されます。")
        st.info(res["disclaimer"])

    with sub5:
        _render_cf_projection(res["projection"]["5y"], "5年")
    with sub10:
        _render_cf_projection(res["projection"]["10y"], "10年")

    with sub_sens:
        st.markdown("#### 感度（結論ドライバー）")
        for t in res["tornado"]:
            st.markdown(
                f"**{t['driver']}** — {t['metric']}： {t['low_label']} `{t['low']:,.0f}` ／ "
                f"基準 `{t['base']:,.0f}` ／ {t['high_label']} `{t['high']:,.0f}`"
            )
        st.markdown("#### シナリオ別 投資指標（税引前）")
        fin = res["financing"]
        def _f(v, p=2, suf=""):
            return f"{v:.{p}f}{suf}" if isinstance(v, (int, float)) else "—"
        st.table({
            "シナリオ": ["弱気", "標準", "強気"],
            "DSCR": [_f(fin[k]["dscr"]) for k in ("min", "mid", "max")],
            "返済比率(対EGI)": [_f((fin[k]["repayment_ratio"] or 0)*100, 1, "%") for k in ("min", "mid", "max")],
            "年間CF(万円)": [_f(fin[k]["pretax_cf"], 0) for k in ("min", "mid", "max")],
            "表面利回り": [_f((fin[k]["gross_yield"] or 0)*100, 1, "%") for k in ("min", "mid", "max")],
            "NOI利回り": [_f((fin[k]["noi_yield"] or 0)*100, 1, "%") for k in ("min", "mid", "max")],
        })


# ---------------------------------------------------------------------------
# 財務・銀行タブ
# ---------------------------------------------------------------------------


def render_finance_bank_tab(report) -> None:
    """財務的な観点＋どの銀行なら通りそうか."""
    st.markdown("## 🏦 財務・銀行")
    res = st.session_state.get("prof_res")
    if res is None:
        try:
            res = _get_profit_result(report, {})
        except Exception as e:  # noqa: BLE001
            st.error(f"計算エラー：{e}")
            return
    a = res["assumptions"]; fin = res["financing"]; ex = res["exit"]

    st.markdown("#### 資金計画の前提")
    c = st.columns(4)
    c[0].metric("想定取得価格(万円)", f"{a['price_assumption_man']:,.0f}",
                help="手入力" if a["price_is_override"] else "収益価格midを仮定")
    c[1].metric("借入(万円)", f"{a['loan_man']:,.0f}", help=f"LTV {a['ltv']*100:.0f}%")
    c[2].metric("自己資金(万円)", f"{a['equity_man']:,.0f}")
    c[3].metric("金利 / 期間", f"{a['loan_rate']*100:.1f}% / {a['loan_term_years']}年")

    st.markdown("#### キャッシュフロー・返済指標（標準・税引前）")
    m = fin["mid"]
    def _g(v, p=2, suf=""):
        return f"{v:.{p}f}{suf}" if isinstance(v, (int, float)) else "—"
    cc = st.columns(4)
    cc[0].metric("DSCR", _g(m["dscr"]), help="NOI÷年間返済。1.2以上が目安")
    cc[1].metric("返済比率(対EGI)", _g((m["repayment_ratio"] or 0)*100, 1, "%"), help="低いほど安全(〜50%目安)")
    cc[2].metric("年間CF(万円)", _g(m["pretax_cf"], 0))
    cc[3].metric("自己資金回収年", _g(m["payback_years"], 1))
    st.caption("DSCR・返済比率・CF・回収年数はすべて税引前。返済比率の分母は実効総収入(EGI)。")

    st.markdown("#### 出口・トータルリターン（mid・税引前）")
    e = st.columns(3)
    e[0].metric("売却純手取り(万円)", f"{ex['sale_net_man']:,.0f}",
                help=f"{ex['exit_year']}年後・売却{ex['sale_price_man']:,.0f}−残債{ex['loan_balance_man']:,.0f}")
    e[1].metric("簡易IRR", f"{ex['simple_irr']*100:.1f}%" if ex["simple_irr"] is not None else "—")
    e[2].metric("トータルリターン(万円)", f"{ex['total_return_man']:,.0f}")

    st.markdown("#### 🏦 この物件なら、どの銀行が通りそうか（目安）")
    ld = res.get("lenders", {})
    cands = ld.get("candidates", [])
    if cands:
        st.table({
            "金融機関タイプ": [c["type"] for c in cands],
            "通りやすさ": [c["fit"] for c in cands],
            "金利目安": [c["rate_hint"] for c in cands],
            "期間目安": [c["term_hint"] for c in cands],
            "特徴": [c["rationale"] for c in cands],
        })
        with st.expander("各候補の注意点"):
            for c in cands:
                st.markdown(f"- **{c['type']}**：{c['caveat']}")
        st.info(ld.get("note", ""))
    st.warning(
        "⚠️ 融資の可否・金利・LTV・年数は各社の商品要項と時期、申込人の属性で大きく変動します。"
        "ここでの提示は一般的傾向に基づく目安であり、確定条件は各金融機関の個別審査で必ずご確認ください。"
        "「融資が付くか（高金利のノンバンク含めれば付きやすい）」と「その金利でCFが回るか」は別問題です。"
    )


# ---------------------------------------------------------------------------
# 収益計算の仕組み（アルゴリズム説明ページ）
# ---------------------------------------------------------------------------


def render_algorithm_page() -> None:
    st.markdown("## 📐 収益計算の仕組み（アルゴリズム）")
    st.caption("本ページは収益化タブ・財務銀行タブの計算ロジックを説明します。すべて前提明示型の試算（estimate）で、鑑定評価ではありません。")
    st.markdown(
        """
### 0. 基本方針：2つの問い
1. **物件そのものの価値はいくらか？** … 収益価格[A]・原価法[B]
2. **儲かりやすいか（CFが回るか）？** … DSCR・返済比率・年間CF・複数年CF

すべての主要出力は **弱気(min)／標準(mid)／強気(max)** のレンジで算出します。

### 0.5 まず「相場 vs 販売価格」（割安/割高）
収益価格[A]（NOI還元）と原価法[B]（土地値＋建物）の両方から **想定適正価格レンジ** を作り、
入力した **販売価格（売出）** と比較して 割安／適正／割高 を判定します。
```
想定適正価格レンジ = [min(A.min, B), max(A.max, B)]   （土地未入力時はAのみ）
販売価格 < レンジ下限 → 割安 ／ レンジ内 → 適正 ／ レンジ上限超 → 割高
相場mid比(%) = 販売価格 ÷ 適正価格mid − 1
```
土地坪単価（販売価格ベース）とエリア相場の坪単価も並べて、土地としての割安感も確認できます。

### 0.6 Airbnb（民泊）相場の扱い
近隣Airbnb/民泊の **ADR・稼働率を手動入力** すると RevPAR=ADR×稼働 として収益試算に反映します。
Airbnbには無料の公式相場APIがないため、自動取得は将来の有料API（AirDNA等）連携で対応予定（スクレイピングは行いません）。

### 1. 収益（USALI階層でNOIを作る）
運営費を「率1本」で引かず、ホテル会計（USALI）の費目で積み上げます。

```
GPI（総収入） = RevPAR × 客室数 × 営業日数
              （客室数 = 延床 × 客室占有率 ÷ 1室面積。1室面積は法令下限でクランプ）
              （業態が民泊なら営業日数は最大180日）
EGI（実効総収入） = GPI ×（1 − 空室・貸倒控除）
 − 変動費：OTA手数料・清掃/泊・リネン・水光熱変動
 = GOP前
 − 固定費：人件費・運営委託料・損害保険・固定資産税都市計画税・水光熱基本
 = GOP
 − FF&E更新積立（売上の3〜5%）
 = NOI（営業純利益）
```

### 2. 物件価値
**[A] 収益価格（Inwood有期還元）** … 残存耐用年数nで割り戻します（永続還元は使いません）。
```
収益価格 = NOI × ( 1 − (1+cap)^(−n) ) ÷ cap        （n=残存耐用年数）
出口を指定した場合は簡易DCF（各年NOIの現在価値＋復帰価格）も使用。
```
**[B] 原価法** … 土地値＋建物値。
```
土地値 = 土地坪単価 × 土地面積(坪)
建物値 = 再調達原価 −減価（再調達原価 × 経過年数 ÷ 法定耐用年数）
```

### 3. キャッシュフローと融資指標（税引前）
```
年間返済額 = 元利均等（借入額・金利・期間）     ※金利0は元金均等で安全に算出
DSCR        = NOI ÷ 年間返済額                  （1.2以上が目安）
返済比率    = 年間返済額 ÷ EGI                  （分母はEGI。低いほど安全）
年間CF      = NOI − 年間返済額
回収年数    = 自己資金 ÷ 年間CF
```

### 4. 複数年キャッシュフロー（5年・10年）
各年について NOI・年間CF・累計CF・ローン残債・その年に売却した場合の純利益を試算します。
```
売却純手取り   = NOI ÷ 出口cap − ローン残債 − 売却益 × 譲渡税率
売却時純利益   = 累計CF + 売却純手取り − 自己資金
簡易IRR        = 自己資金・各年CF・出口手取りの内部収益率
```

### 5. 感度分析（tornado）
結論を動かす主要因（**cap rate・稼働率/RevPAR・運営費・金利**）を振って、価値とCFへの影響幅を可視化します。

### 6. 容積ポテンシャル（参考値・要鑑定）
```
現況容積消化率 = 延床 ÷ 土地面積
理論最大床     = 土地面積 × 指定容積率
余地(倍)       = 指定容積率 ÷ 現況消化率
```
※斜線・日影・高度地区・天空率・前面道路制限・地階の扱いは未考慮の参考値です。

### 7. 銀行マッチングの考え方
物件プロファイル（構造・築年・利回り・DSCR）から候補金融機関タイプを提示します。
- RC築浅・高DSCR → 都銀/メガが通りやすい（低金利だが審査厳しめ）
- 一般の収益物件 → 地銀・信金
- 築古・変則・高利回り・バリューアップ前提 → ノンバンク（オリックス銀行／セゾンファンデックス等。金利は高めだが収益性重視で柔軟）

「融資が付くか」と「その金利でCFが回るか」は分けて評価します。金利・LTV・年数は各社・時期で変動するため、確定条件は個別審査で要確認です。

### データソースについて
- 自動連携は公式オープンAPIの **REINFOLIB**（取引価格・地価）を主軸とします。
- SUUMO・レインズ・Airbnb等は規約・クローズドの都合で**自動取得せず手入力**（将来は正規の有料API連携を想定）。
"""
    )


if __name__ == "__main__":
    main()

