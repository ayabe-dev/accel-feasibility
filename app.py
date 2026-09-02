"""Streamlit エントリポイント.

用途変更フィジビリティ判定 — Phase 1 MVP
"""
from __future__ import annotations

import os
from typing import List

import pandas as pd
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
from core import legal_research
from core import profitability
from core.license_gate import GateStatus, LicenseVerdict
from core.municipality import detect_municipality, get_municipality_name
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
import ui_charts as charts
import ui_chat


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
        st.markdown("### 📞 目黒区の相談窓口")
        st.markdown(
            "- 旅館業許可（衛生）：保健所 生活衛生課 環境衛生係\n"
            "  `03-5722-9502`\n"
            "- 用途変更・建基法：建築課 建築指導係\n"
            "  `03-5722-9637`\n"
            "- 建築士相談：東京都建築士事務所協会 目黒支部\n"
            "  `03-5724-5061`"
        )
        st.caption(
            "出典：目黒区「旅館業の手引き」令和6年1月改訂／"
            "「既存住宅等を利用し、旅館・ホテルへの用途変更を検討している皆様へ」令和7年8月8日"
        )

        st.divider()
        st.markdown("### デモ用住所サンプル")
        st.caption("**目黒区**（区の手引き・条例を実データで反映済み）")
        st.code(
            "東京都目黒区中目黒1-1-1（一種住居・3000㎡以下可）\n"
            "東京都目黒区下目黒1-1-1（商業・上限なし）\n"
            "東京都目黒区自由が丘1-25-9（商業）\n"
            "東京都目黒区鷹番3-2-1（近隣商業・学芸大学）\n"
            "東京都目黒区目黒本町3-1-1（二種住居）\n"
            "東京都目黒区青葉台2-1-1（一種低層＝NG例）\n"
            "東京都目黒区駒場4-6-1（一種中高層＝NG例）",
            language="text",
        )
        st.caption("その他（東京都標準の当て込み）")
        st.code(
            "東京都新宿区西新宿2-8-1\n"
            "東京都渋谷区道玄坂1-1-1\n"
            "東京都世田谷区成城6-5-34（NG例）\n"
            "東京都港区六本木6-10-1\n"
            "京都府京都市東山区祇園町南側",
            language="text",
        )
        st.caption(
            "デモモードではこれらの住所で動作確認できます。"
            "用途地域は例示であり、目黒区は「めぐろ地図情報サービス」で必ず確認してください。"
        )


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

    # レポートDL欄のスロットは毎回作り直す（前回runのコンテナ参照を使い回さない）
    st.session_state["_report_dl_slot"] = None

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

    # 全タブの計算が終わったあとにレポートDLを生成（最新の前提・議論ログを反映）
    if st.session_state.get("report") is not None:
        _fill_report_downloads(st.session_state.report)


def render_input_tab() -> None:
    """資料投入タブ：入力フォーム＋実行ボタン."""
    st.header("1. 基本情報を入力")
    col1, col2 = st.columns([2, 1])

    with col1:
        address = st.text_input(
            "物件所在地",
            value="東京都目黒区中目黒1-1-1",
            placeholder="例：東京都目黒区中目黒1-1-1",
            help="住居表示または地番。デモモードではサイドバーのサンプル住所で動作確認可能。"
                 "目黒区は区の手引き・条例を実データで反映しています。",
        )

    with col2:
        business_type = st.selectbox(
            "業態",
            options=[
                BusinessType.HOTEL_RYOKAN,
                BusinessType.SIMPLE_LODGING,
                BusinessType.MINPAKU,
            ],
            format_func=lambda x: {
                BusinessType.HOTEL_RYOKAN: "旅館・ホテル営業（旅館業許可）",
                BusinessType.SIMPLE_LODGING: "簡易宿所営業（旅館業許可）",
                BusinessType.MINPAKU: "住宅宿泊事業（民泊・届出）",
            }[x],
            help="旅館業の2業態は建築基準法上どちらも「ホテル又は旅館」用途で、"
                 "立地・耐火・避難の要件は同じ。違うのは旅館業法の構造設備基準"
                 "（簡易宿所は1室単位の面積下限がなく客室延床33㎡以上）。"
                 "民泊は建基法上「住宅」のままなので用途地域の制限を受けない代わりに、"
                 "年180日上限と自治体条例の区域・期間制限がかかります。",
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

    # ── 許可判定に効く計画情報 ─────────────────────────
    with st.expander("🛂 許可判定に効く計画情報（任意・入れると判定が確定します）", expanded=False):
        p1, p2, p3 = st.columns(3)
        with p1:
            conversion_area = st.number_input(
                "用途変更部分の床面積（㎡）",
                min_value=0.0,
                value=0.0,
                step=10.0,
                help="建物の一部だけを宿泊用途にする場合に入力（例：1階は住居のまま残す）。"
                     "建基法87条の200㎡判定はこの面積で行います。"
                     "未入力なら延床面積で判定します。",
            )
        with p2:
            guest_room_count = st.number_input(
                "計画する客室数",
                min_value=0,
                max_value=200,
                value=0,
                step=1,
                help="旅館・ホテル営業の客室面積基準（1室7㎡・寝台9㎡以上）の目安計算に使います。",
            )
        with p3:
            owner_resident_choice = st.selectbox(
                "家主居住の予定（民泊ルート）",
                options=["未定", "家主居住型", "家主不在型"],
                index=0,
                help="民泊では『宿泊室50㎡以下かつ家主が不在とならない』場合に"
                     "非常用照明の設置が免除され、初期投資が大きく変わります。",
            )

    # ── 自治体条例の調査（LLM） ─────────────────────────
    with st.expander("🔍 自治体の条例・手引きを一次情報から調べる", expanded=False):
        st.caption(
            "許可の可否を実際に左右するのは自治体の上乗せ条例です。"
            "Claude が web 検索で自治体の例規集・審査基準・手引きを実際に開いて確認し、"
            "**原文の引用と出典URLが取れたものだけ**を判定に使います。"
            "調査結果はキャッシュされ、次回以降は即座に反映されます。"
        )
        research_enabled = st.checkbox(
            "自治体条例を調査する（1〜3分かかります）",
            value=False,
            disabled=not legal_research.is_available(),
        )
        if not legal_research.is_available():
            st.info(
                "Anthropic API の資格情報が見つからないため自治体調査は使えません。"
                "`.env` または Streamlit Secrets に ANTHROPIC_API_KEY を設定してください。"
                "未設定でも、全国共通の法令（旅館業法・同施行令・建築基準法）に基づく"
                "許可可否の判定は動きます。"
            )
        research_refresh = st.checkbox(
            "キャッシュを無視して再調査する（条例改正の反映）",
            value=False,
            disabled=not research_enabled,
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
                conversion_area_m2=(
                    float(conversion_area) if conversion_area > 0 else None
                ),
                guest_room_count=(
                    int(guest_room_count) if guest_room_count > 0 else None
                ),
                owner_resident={
                    "家主居住型": True,
                    "家主不在型": False,
                }.get(owner_resident_choice),
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

            # 自治体条例の調査（任意）。判定より先に済ませて判定に食わせる
            research = None
            muni_key = detect_municipality(project.address)
            if research_enabled and muni_key:
                with st.spinner(
                    f"{get_municipality_name(muni_key) or muni_key} の条例・手引きを"
                    "一次情報から調査中…（web検索）"
                ):
                    research = legal_research.research_municipality(
                        municipality_key=muni_key,
                        municipality_name=get_municipality_name(muni_key) or muni_key,
                        address=project.address,
                        business_type=project.business_type,
                        refresh=research_refresh,
                    )
            elif research_enabled and not muni_key:
                st.warning(
                    "住所から自治体を特定できなかったため、条例調査をスキップしました。"
                )
            elif muni_key:
                # 調査を明示的に走らせていなくても、既存キャッシュがあれば使う
                research = legal_research.cached_result(
                    muni_key, project.business_type
                )

            # 総合判定
            report = judgment.run(
                project=project,
                docs=docs,
                manual_geo=manual_geo,
                has_nearby_facility=_yesno(nearby_choice),
                nearby_facilities=nearby_facilities,
                research=research,
            )

        # Session State に保存して評価タブで表示
        st.session_state.report = report
        st.success(
            "✅ 判定完了！「📊 評価結果」タブに移動して結果を確認してください。"
        )
        st.balloons()


def _fill_report_downloads(report) -> None:
    """確保しておいたスロットにレポートDLボタンを描画する（全タブ描画後に呼ぶ）."""
    slot = st.session_state.get("_report_dl_slot")
    if slot is None:
        return
    from datetime import datetime as _dt

    weights = st.session_state.get("weights", DEFAULT_WEIGHTS)
    # 収益性：収益化タブで計算済みならそれを使う。未計算なら既定前提で計算してレポートに載せる。
    if not getattr(report, "profitability", None):
        cached = st.session_state.get("prof_res")
        if cached:
            report.profitability = cached
        else:
            try:
                report.profitability = profitability.compute(report, overrides={})
            except Exception:  # noqa: BLE001
                pass
    chat_logs = ui_chat.all_logs()

    try:
        md_report = generate_markdown_report(report, weights=weights, chat_logs=chat_logs)
        html_report = generate_html_report(report, weights=weights, chat_logs=chat_logs)
    except Exception as e:  # noqa: BLE001
        with slot:
            st.error(f"レポート生成でエラー：{e}")
        return

    fname_safe_addr = (
        (report.input.address or "report").replace("/", "_").replace(" ", "_")[:30]
    )
    timestamp = _dt.now().strftime("%Y%m%d_%H%M")
    fname_base = f"feasibility_{fname_safe_addr}_{timestamp}"

    with slot:
        st.caption(
            "法規判定・収益性・キャッシュフロー・議論ログをまとめて出力します"
            "（収益性は「②収益化できるか」タブの現在の前提を反映）。"
        )
        dl_col1, dl_col2, dl_col3, dl_col4 = st.columns(4)
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
            pdf_bytes = generate_pdf(report, weights=weights, chat_logs=chat_logs)
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
                    help="weasyprint未インストール。HTMLをダウンロード→ブラウザで開く→ Cmd+P でPDF保存",
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
        with dl_col4:
            ui_chat.render_transcript_download()
        if not is_pdf_available():
            st.caption(
                "💡 PDFを直接生成するには `pip install weasyprint` が必要です。"
                "未インストールの場合は HTML をダウンロード→ブラウザで開く→ Cmd+P で PDF保存できます。"
            )


def render_license_tab(report) -> None:
    """🛂 許可可否タブ：この物件で旅館業の許可が取れるかを主判定として出す."""
    j = getattr(report, "license_judgment", None)
    if j is None:
        st.info("許可可否の判定結果がありません。入力タブから再判定してください。")
        return

    # ── 結論 ──────────────────────────────────────────
    banner = {
        LicenseVerdict.GRANTABLE: st.success,
        LicenseVerdict.CONDITIONAL: st.info,
        LicenseVerdict.CONSULT: st.warning,
        LicenseVerdict.DIFFICULT: st.warning,
        LicenseVerdict.BLOCKED: st.error,
        LicenseVerdict.UNKNOWN: st.info,
    }.get(j.verdict, st.info)
    banner(f"### {j.verdict_label}\n\n{j.headline}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("対象業態", j.business_label.split("（")[0])
    c2.metric("自治体", j.municipality_name or "未特定")
    c3.metric("情報の充足率", f"{j.data_completeness * 100:.0f}%")
    c4.metric("判定の信頼度", j.confidence)

    if j.permit_authority:
        st.caption(f"**許可権者・窓口**：{j.permit_authority}")
    st.caption(j.disclaimer)

    # ── 次にやること ───────────────────────────────────
    if j.next_actions:
        st.markdown("#### ✅ 次にやること")
        for i, a in enumerate(j.next_actions, 1):
            st.markdown(f"{i}. {a}")

    # ── 自治体調査の状態 ───────────────────────────────
    if j.research_note:
        st.warning(f"🔍 {j.research_note}")
    elif j.research_summary:
        with st.expander(
            f"🔍 {j.municipality_name} の条例調査結果（{j.researched_on} 時点）",
            expanded=False,
        ):
            st.markdown(j.research_summary)
            if j.unresolved:
                st.markdown("**確認できなかった論点（保健所へ直接確認）**")
                for u in j.unresolved:
                    st.markdown(f"- {u}")

    st.divider()

    # ── ゲート ────────────────────────────────────────
    st.markdown("#### 🚪 許可までに越えるゲート")
    st.caption(
        "旅館業法3条の不許可事由を起点に、許可までに越える必要がある要件を並べています。"
        "根拠は法令の原文と出典URLつきで表示します。"
        "⚠️未検証の根拠は判定には使っていません。"
    )

    order = ["立地", "建物", "構造設備", "事業条件", "申請者"]
    by_cat = {}
    for g in j.gates:
        by_cat.setdefault(g.category, []).append(g)

    for cat in order:
        gates = by_cat.get(cat)
        if not gates:
            continue
        st.markdown(f"##### {cat}")
        for g in gates:
            expanded = g.status in (
                GateStatus.FAIL,
                GateStatus.CONSULT,
                GateStatus.UNKNOWN,
            )
            with st.expander(f"{g.status_label}　{g.title}", expanded=expanded):
                st.markdown(g.finding)
                if g.remedy:
                    st.markdown(f"**対応**：{g.remedy}")
                if g.data_gaps:
                    st.markdown("**確定に必要な情報**")
                    for gap in g.data_gaps:
                        st.markdown(f"- {gap}")
                if g.evidences:
                    st.markdown(f"**根拠**（この項目の根拠信頼度：{g.confidence}）")
                    for e in g.evidences:
                        st.markdown(e.to_markdown())
                        st.markdown("")

    # ── 別ルート ──────────────────────────────────────
    if j.alternatives:
        st.divider()
        st.markdown("#### 🔀 別ルートの見込み")
        st.caption(
            "主判定でつまずいた場合に、業態を変えれば成立するかを同じ物件条件で評価します。"
        )
        for alt in j.alternatives:
            with st.expander(f"{alt.verdict_label}　{alt.label}", expanded=True):
                st.markdown(alt.summary)
                if alt.blockers:
                    st.markdown("**越えられない要因**")
                    for b in alt.blockers:
                        st.markdown(f"- ⛔ {b}")
                if alt.conditions:
                    st.markdown("**条件**")
                    for cnd in alt.conditions:
                        st.markdown(f"- {cnd}")
                if alt.evidences:
                    with st.expander("根拠を見る", expanded=False):
                        for e in alt.evidences:
                            st.markdown(e.to_markdown())
                            st.markdown("")


def render_report(report) -> None:
    st.divider()
    st.header("4. 判定結果")

    # スコア計算（重みは Session State から取得）
    weights = st.session_state.get("weights", DEFAULT_WEIGHTS)
    score = compute_score(report, weights=weights)

    # チャットに渡す収益性結果（②収益化タブを開いていれば計算済み）
    res_prof = getattr(report, "profitability", None) or st.session_state.get("prof_res")

    # 適用した自治体ルールを明示（実データ反映済みか／東京都標準の当て込みか）
    from core import municipality as _muni_mod
    _mkey = _muni_mod.detect_municipality(report.input.address)
    _mname = _muni_mod.get_municipality_name(_mkey)
    if _mname and _muni_mod.is_detailed(_mkey):
        _rule = _muni_mod.get_municipality_rule(_mkey)
        st.success(
            f"📗 **{_mname}** の条例・手引きを実データで反映して判定しています"
            f"（出典時点：{_rule.get('as_of', '—')}）"
        )
        with st.expander(f"{_mname}の出典・相談窓口"):
            for src in _rule.get("sources", []):
                st.markdown(f"- [{src.get('title', '')}]({src.get('url', '')})")
            st.markdown("**相談窓口**")
            for c in _rule.get("contacts", []):
                tel = c.get("tel", "")
                fax = f"／FAX {c['fax']}" if c.get("fax") else ""
                st.markdown(f"- {c.get('role', '')}：{c.get('dept', '')}　`{tel}`{fax}")
    elif _mname:
        st.info(
            f"ℹ️ **{_mname}** は東京都/一般基準の当て込みで判定しています。"
            "区独自の上乗せ条例・特別用途地区は反映されていないため、必ず所管窓口で確認してください。"
            "（目黒区は区の手引き・条例を実データで反映済みです）"
        )

    # レポート出力欄は「収益性の計算・チャットの描画が終わったあと」に中身を入れる。
    # ここでは場所だけ確保し、main() の最後で _fill_report_downloads() が埋める。
    # （そうしないと、前提を変えた直後のレポートが1操作分古くなる）
    st.markdown("### 📥 レポート出力")
    st.session_state["_report_dl_slot"] = st.container()

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
    (tab_license, tab_score, tab1, tab2, tab3, tab4, tab5, tab6, tab7,
     tab8) = st.tabs(
        [
            "🛂 許可可否",
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

    with tab_license:
        render_license_tab(report)

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

        ui_chat.chat_panel("overall", report, res_prof, expanded=True)

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

        ui_chat.chat_panel("location", report, res_prof, expanded=False)

    with tab2:
        st.markdown(f"#### 調査パターン：**{report.pattern.value}**")
        st.write(report.pattern_reason)
        if report.warnings:
            st.markdown("#### ⚠️ 注意事項")
            for w in report.warnings:
                st.warning(w)

        ui_chat.chat_panel("pattern", report, res_prof, expanded=False)

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

        ui_chat.chat_panel("cost", report, res_prof, expanded=False)

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
        st.markdown("**課金モデル・収益目標**")
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            unit_label = st.radio("課金モデル", ["一棟貸し（建物まるごと）", "客室ごと"], key="prof_unit",
                                  help="一棟貸し=ADRは建物1棟/泊。小型戸建て民泊向き。客室ごと=ADR×客室数")
        with mc2:
            target_yield = st.slider("目標NOI利回り（%）", 5, 25, 15, 1, key="prof_target",
                                     help="この利回りを満たす適正価格を逆算します")
        with mc3:
            works_in = st.number_input("初期費用 リノベ＋消防許可（万円・空欄=自動）", min_value=0.0, value=0.0, step=100.0, key="prof_works")
        st.markdown("**Airbnb/民泊 相場（手動・任意）** — 近隣のADR・稼働率を入れると反映（一棟貸しなら建物1棟の1泊単価）")
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            adr = st.number_input("ADR（円/泊）", min_value=0, value=0, step=1000, key="prof_adr",
                                  help="1件だけの単一値。入力するとコンプ表より優先されます")
        with ac2:
            occ = st.slider("想定稼働率（%）", 0, 100, 0, 5, key="prof_occ")
        with ac3:
            los = st.number_input("平均宿泊日数（泊）", min_value=1.0, max_value=14.0,
                                  value=3.0, step=0.5, key="prof_los",
                                  help="清掃回数の分母。長期滞在が多いほど清掃費は下がります")

        st.markdown("---")
        st.markdown(
            "**📊 近隣コンプ（類似物件）を入れてADR相場を作る** — "
            "AirDNA等で調べた近隣の類似スペック物件を数件入れると、"
            "中央値をmid・四分位をレンジとして採用します（単一値の±15%より実勢に近くなります）"
        )
        comp_df = st.data_editor(
            pd.DataFrame(
                [{"物件メモ": "", "ADR（円/泊）": None, "稼働率（%）": None} for _ in range(4)]
            ),
            key="prof_comps",
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "物件メモ": st.column_config.TextColumn("物件メモ", help="物件名・スペックなど（任意）"),
                "ADR（円/泊）": st.column_config.NumberColumn(
                    "ADR（円/泊）", min_value=0, step=1000, format="%d"),
                "稼働率（%）": st.column_config.NumberColumn(
                    "稼働率（%）", min_value=0, max_value=100, step=1, format="%d"),
            },
        )
        comps = []
        try:
            for _, row in comp_df.iterrows():
                a = row.get("ADR（円/泊）")
                o = row.get("稼働率（%）")
                if (a is not None and not pd.isna(a) and float(a) > 0) or \
                   (o is not None and not pd.isna(o) and float(o) > 0):
                    comps.append({
                        "name": ("" if pd.isna(row.get("物件メモ")) else str(row.get("物件メモ"))),
                        "adr_yen": (None if a is None or pd.isna(a) else float(a)),
                        "occupancy": (None if o is None or pd.isna(o) else float(o)),
                    })
        except Exception:  # noqa: BLE001
            comps = []
        if comps:
            st.caption(f"✅ コンプ {len(comps)}件を反映します（手動ADRが入っている場合は手動が優先）")

        st.markdown("**清掃費（1組あたり・空欄=課金モデル別の既定値）**")
        cl1, cl2 = st.columns(2)
        with cl1:
            clean_cost = st.number_input("清掃の原価（円/組）", min_value=0, value=0, step=1000,
                                         key="prof_clean_cost",
                                         help="一棟貸し既定=12,000円／客室ごと既定=4,000円")
        with cl2:
            clean_fee = st.number_input("ゲストへ請求する清掃料金（円/組）", min_value=0, value=0,
                                        step=1000, key="prof_clean_fee",
                                        help="既定0＝『ADRに清掃費が含まれている』想定。"
                                             "ADRとは別に請求している場合のみ入れてください（二重計上防止）")
    return {
        "land_area_m2": land_area or None,
        "room_area_m2": room_area or None,
        "purchase_price_man": price or None,
        "ltv": ltv, "loan_rate": loan_rate, "loan_term_years": int(loan_term),
        "exit_year": int(exit_year),
        "adr_yen": (adr or None),
        "occupancy_input": (occ / 100.0) if occ else None,
        "avg_length_of_stay": los,
        "comps": comps or None,
        "cleaning_cost_man": (clean_cost / 10000.0) if clean_cost else None,
        "cleaning_fee_man": (clean_fee / 10000.0) if clean_fee else None,
        "revenue_unit": "whole" if unit_label.startswith("一棟") else "per_room",
        "target_noi_yield": target_yield / 100.0,
        "initial_works_man": (works_in or None),
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
    m1, m2 = st.columns(2)
    with m1:
        st.metric(f"{label} 累計CF（万円）", f"{proj['cumulative_cf']:,.0f}")
    with m2:
        st.metric(f"{label} 末に売却した場合の純利益（万円）", f"{proj['net_profit_if_sell_end']:,.0f}",
                  help="運営CF累計＋売却純手取り−自己資金（税引前）")

    st.markdown("##### 推移グラフ")
    st.altair_chart(charts.cumulative_cf_chart(proj))
    st.caption(
        "🔵累計CF＝運営で手元に残った累計 ／ ⚪ローン残債＝その年の借入残 ／ "
        "🟠売却時の純利益＝その年に売った場合のトータル（累計CF＋売却純手取り−自己資金）。"
        "🟠が0を上回る年が『損せず抜けられる』最短ラインです。"
    )
    st.altair_chart(charts.annual_cf_chart(proj))
    st.caption("赤い年はその年のCFが赤字（NOI＜返済）です。初年度は開業立ち上がりを反映しています。")

    with st.expander("📋 数値表で見る"):
        st.table(table)
    st.caption("2年目以降のNOIは横ばい前提（成長0%）。売却時純利益＝累計CF＋売却純手取り−自己資金。すべて税引前の試算。")


def _render_backward(res, report) -> None:
    """プロの5ステップ思考でNOI目標から適正価格を逆算して表示."""
    b = res.get("backward")
    st.markdown("## 🎯 適正価格の逆算（プロの5ステップ思考）")
    st.caption("「目標NOI利回りを満たすには、いくらまでなら払っていいか」を逆算し、売出価格と比較します。前提は『前提を調整』で変更可。")
    if not b:
        st.info("逆算はアプリ更新の反映待ちです。Reboot後に表示されます。")
        return

    # STEP1 瞬殺フィルター（旅館業の可否：既存判定から）
    st.markdown("### STEP1 ⏱️ 瞬殺フィルター（旅館業がそもそも可能か）")
    lvl = getattr(report.overall_level, "value", str(report.overall_level))
    zone = report.geo.zoning_name or "未取得"
    by = report.input.built_year
    kyu = (by is not None and by <= 1981)
    f1, f2, f3 = st.columns(3)
    f1.metric("旅館業 総合判定", {"GO": "🟢 可能", "CONDITIONAL": "🟡 条件付き", "NO_GO": "🔴 不可"}.get(lvl, lvl))
    f2.metric("用途地域", zone)
    f3.metric("旧耐震(1981以前)", "⚠️ 該当" if kyu else "OK")
    if lvl == "NO_GO":
        st.error("🔴 立地・法規でNGの可能性。『①旅館業が取れるか』タブで詳細を確認してください。割高以前にそもそも事業化できない恐れ。")
    st.divider()

    # STEP2 売上ポテンシャル
    st.markdown("### STEP2 💰 売上ポテンシャル（定員→年間売上）")
    s1, s2, s3 = st.columns(3)
    _cap_help = res.get("capacity_basis", "専有面積 ÷ 1人あたり面積")
    if res.get("capacity_legal_max"):
        _cap_help += (
            f"／{res.get('municipality_name', '')}条例の法令上限は約{res['capacity_legal_max']}名"
            "（実務は寝具・便所数・消防が先に頭打ち）"
        )
    s1.metric("最大定員の目安", f"{res['capacity_est']} 名", help=_cap_help)
    s2.metric("課金モデル", "一棟貸し" if res["revenue_unit"] == "whole" else "客室ごと")
    s3.metric("年間想定売上 GPI（mid）", f"{b['gpi_mid_man']:,.0f} 万円")
    st.caption(f"RevPAR算定：{res['revpar_source']}。年間売上 ＝ RevPAR(=ADR×稼働) × {res['rooms_used_for_revenue']}室 × 営業日数。")
    st.divider()

    # STEP3 投資上限の逆算
    st.markdown("### STEP3 🧮 投資上限の逆算（NOI ÷ 目標利回り）")
    t1, t2, t3 = st.columns(3)
    t1.metric("NOI（USALI・mid）", f"{b['noi_mid_man']:,.0f} 万円")
    t2.metric("NOI（売上50%の簡易・参考）", f"{b['noi_quick50_mid_man']:,.0f} 万円")
    t3.metric(f"総投資上限（目標{b['target_noi_yield']*100:.0f}%）", f"{b['budget_cap_man']['mid']:,.0f} 万円",
              help=f"レンジ {b['budget_cap_man']['min']:,.0f}〜{b['budget_cap_man']['max']:,.0f} 万円")
    st.caption("総投資上限 ＝ NOI ÷ 目標NOI利回り。これが事業に突っ込んでよい『お金の総額の上限』。")
    st.divider()

    # STEP4 初期費用の引き算 → 適正価格
    st.markdown("### STEP4 ➖ 初期費用を引いて『物件に払っていい適正価格』")
    w = b["initial_works_man"]
    st.markdown(
        f"総投資上限 **{b['budget_cap_man']['mid']:,.0f}万** － 初期費用(リノベ＋消防許可) **{w['mid']:,.0f}万** "
        f"を取得諸経費{b['acq_cost_rate']*100:.0f}%で割り戻し → **適正価格 {b['fair_price_man']['mid']:,.0f}万円**"
    )
    st.caption(f"初期費用の根拠：{b['initial_works_source']}。適正価格 ＝ (総投資上限 − 初期費用) ÷ (1＋取得諸経費率)。")
    fp = b["fair_price_man"]
    st.metric("🏠 物件に払っていい適正価格（mid）", f"{fp['mid']:,.0f} 万円",
              help=f"レンジ {fp['min']:,.0f}〜{fp['max']:,.0f} 万円")
    st.divider()

    # STEP5 最終ジャッジ
    st.markdown("### STEP5 ⚖️ 最終ジャッジ（適正価格 vs 売出価格）")
    _pos = charts.price_position_chart(res.get("valuation") or {}, b.get("fair_price_man"))
    if _pos is not None:
        st.altair_chart(_pos)
        st.caption(
            "灰色の帯＝収益価格[A]と原価法[B]から作った相場レンジ。"
            "🔴売出価格が帯より右なら割高、左なら割安。"
            "🟢目標NOIから逆算した価格より売出が高い場合、その差が必要な指値額です。"
        )
    if b["asking_price_man"]:
        j1, j2, j3 = st.columns(3)
        j1.metric("売出価格", f"{b['asking_price_man']:,.0f} 万円")
        j2.metric("適正価格(mid)", f"{fp['mid']:,.0f} 万円")
        j3.metric("判定", b["verdict"] or "—")
        if b["suggested_discount_man"] and b["suggested_discount_man"] > 0:
            st.warning(
                f"💴 このままでは目標NOI{b['target_noi_yield']*100:.0f}%に届きません。"
                f"**約{b['suggested_discount_man']:,.0f}万円の指値（{fp['mid']:,.0f}万円での買付）**が通れば検討余地あり。"
            )
        elif b["asking_price_man"] <= fp["min"]:
            st.success("🟢 超割安！適正価格レンジの下限より安い水準です。")
        else:
            st.info("🟡 概ね妥当〜割安な水準です。")
    else:
        st.info("『前提を調整』で **販売価格** を入れると、指値要否まで判定します。")
    st.caption("※ すべて前提明示型の試算。初期費用・ADR・目標利回りは前提次第で大きく動きます。")


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

    sub_back, sub_sum, sub5, sub10, sub_sens = st.tabs(
        ["🎯 適正価格(逆算)", "📌 価値・サマリ", "📅 5年CF", "📅 10年CF", "🎚️ 感度"]
    )
    with sub_back:
        _render_backward(res, report)
        ui_chat.chat_panel("backward", report, res)

    with sub_sum:
        v = res.get("valuation")
        if not v:
            st.info("価格判定（割安/割高）は再デプロイ後に表示されます。アプリを Reboot してください。")
            v = {"market_value_man": {"min": 0, "mid": 0, "max": 0}, "asking_price_man": None,
                 "income_value_mid_man": 0, "cost_value_man": None, "price_verdict": None,
                 "gap_pct": None, "basis": ""}
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
            f"／残存耐用:{res['remaining_useful_life_years']}年／RevPAR:{res.get('revpar_source','—')}／設定:{res['config_version']}"
        )
        st.divider()
        st.markdown("### ③ シナリオ比較（弱気・標準・強気）")
        st.altair_chart(charts.scenario_chart(res))
        st.caption(
            "弱気＝低ADR×低稼働×高cap、強気＝高ADR×高稼働×低cap。"
            "3つが極端に開く場合、前提の不確実性が大きい＝意思決定は弱気側で行うのが安全です。"
        )

        st.divider()
        st.markdown("### ④ ADRからNOIまでの導出（どう計算しているか）")
        st.table(charts.adr_derivation_rows(res))
        st.caption(
            "GPIは『満室だったらいくらか』、EGIは『稼働率をかけた実際の収入』です。"
            "清掃は1泊ごとではなく**1組ごと**に発生する前提で計算しています（平均宿泊日数で割る）。"
        )

        st.markdown("##### NOIの落ち方")
        st.altair_chart(charts.noi_waterfall_chart(res["noi_breakdown_mid"]))

        st.divider()
        st.markdown("### ⑤ 月別の売上と稼働（季節性・開業立ち上がり）")
        _m = res.get("monthly") or {}
        if _m.get("stabilized"):
            st.altair_chart(charts.monthly_chart(_m["stabilized"], _m.get("year1")))
            st.caption(
                "🔵安定稼働＝季節係数のみ反映 ／ 🟠初年度＝開業立ち上がりも反映。"
                f"初年度NOI {res.get('noi_year1_man', 0):,.0f}万円 vs 安定稼働NOI {res['noi']['mid']:,.0f}万円。"
                "初年度の資金繰りはこの差を見込んでおく必要があります。"
            )

        with st.expander("📊 NOI内訳（USALI階層・mid）を数値で見る"):
            nb = res["noi_breakdown_mid"]
            st.table({
                "項目": ["GPI(満室潜在収入)", "EGI(実効総収入)", "−変動費", "GOP前", "−固定費", "GOP", "−FF&E積立", "= NOI"],
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
        ui_chat.chat_panel("profit", report, res, instance="summary")

    with sub5:
        _render_cf_projection(res["projection"]["5y"], "5年")
        ui_chat.chat_panel("cf", report, res, instance="5y")
    with sub10:
        _render_cf_projection(res["projection"]["10y"], "10年")
        ui_chat.chat_panel("cf", report, res, instance="10y")

    with sub_sens:
        st.markdown("#### 感度（どの前提が結論を動かすか）")
        _tor = charts.tornado_chart(res["tornado"])
        if _tor is not None:
            st.altair_chart(_tor)
            st.caption(
                "棒が長いドライバーほど結論を左右します。"
                "＝そこを実額で詰める（相場を調べる・見積を取る・金利を確認する）のが最優先です。"
            )
        with st.expander("📋 数値で見る"):
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
        ui_chat.chat_panel("profit", report, res, instance="sensitivity")


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

    st.altair_chart(charts.dscr_gauge_chart(fin))
    st.caption(
        "DSCR＝NOI÷年間返済。1.0を切ると返済が家賃収入だけでは回りません。"
        "銀行は概ね1.2以上を求めます。弱気シナリオでも1.0を超えているかが実務的な安全ラインです。"
    )

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
    ui_chat.chat_panel("finance", report, res)


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

### 0.6 ADR（1泊単価）と稼働率の決め方 ← v2026.08.1 で強化
ADRは次の優先順位で決まります。**下に行くほど根拠が弱い**ので、上位の入力があるほど試算の精度が上がります。

| 優先 | 入力 | レンジ(min/max)の作り方 |
|---|---|---|
| 1 | RevPAR直接指定 | ±15% |
| 2 | ADRを手入力（単一値） | ±15%（あくまで仮置き） |
| 3 | **近隣コンプを数件入力** | **中央値=mid、四分位(25%/75%)=min/max** |
| 4 | エリア相場（自動） | エリアティアのmin/mid/max |

**近隣コンプ**は「⚙️前提を調整」の表に、AirDNA等で調べた類似物件のADR・稼働率を数件入れる方式です。
単一値の±15%より実勢の分布に近いレンジになります。データ取得元は差し替え可能な設計（`core/market_data.py`）で、
将来AirDNA等の正規APIに載せ替えても計算側は変更不要です。Airbnb本体のスクレイピングは規約違反のため行いません。

**一棟貸しのADR**は定員に対して**逓減**させます（10人だから10倍にはならない）。
```
一棟ADR = 1人あたり単価 × 定員^0.85     （定員は実務上限16名でクランプ）
```

### 0.65 季節性と開業立ち上がり ← v2026.08.1 で追加
月別の季節係数（年平均1.0に正規化）と、開業からの立ち上がり（既定：6ヶ月かけて稼働40%→100%）を月次で反映し、
**初年度NOI**と**安定稼働NOI**を分けて算出します。
- 物件価値（収益還元）＝**安定稼働NOI**を使う（立ち上がりで資産価値を割り引くのは不適切）
- 複数年CFの**1年目だけ初年度NOI**を使う（初年度の資金繰りが甘くならないように）

### 0.7 適正価格の逆算（プロの5ステップ思考）
「目標NOI利回り（既定15%）を満たすには、いくらまで払っていいか」を逆算します。
```
STEP1 瞬殺フィルター：用途地域/旧耐震(1981以前)/接道で旅館業が可能か（不可なら即除外）
STEP2 売上ポテンシャル：最大定員(=専有面積÷1人あたり面積) → ADR×稼働×営業日数 = 年間売上(GPI)
STEP3 投資上限の逆算：NOI ÷ 目標NOI利回り = 総投資の上限
STEP4 初期費用を引く：適正価格 = (総投資上限 − リノベ・消防許可費) ÷ (1＋取得諸経費率)
STEP5 最終ジャッジ：適正価格 vs 売出価格 → 割安/妥当/割高（割高なら必要指値額を提示）
```
課金モデルは「一棟貸し（ADR=建物1棟/泊）」と「客室ごと（ADR×客室数）」を切替可能。
NOIは精緻版（USALI）と簡易版（売上×50%）の両方を表示し、感覚値とも突き合わせられます。

### 1. 収益（USALI階層でNOIを作る）
運営費を「率1本」で引かず、ホテル会計（USALI）の費目で積み上げます。

```
GPI（満室時潜在収入） = ADR × 収益計算室数 × 営業日数
              （客室数 = 延床 × 客室占有率 ÷ 1室面積。1室面積は法令下限でクランプ）
              （一棟貸しは室数=1／客室ごとは室数=客室数）
              （業態が民泊なら営業日数は最大180日）
客室収入 = GPI × 稼働率            （= RevPAR × 室数 × 営業日数）
清掃料金収入 = 1組あたり請求額 × 清掃組数
EGI（実効総収入） = 客室収入 ＋ 清掃料金収入
 − 変動費：OTA手数料・清掃原価×組数・リネン・水光熱変動
 = GOP前
 − 固定費：人件費・運営委託料・損害保険・固定資産税都市計画税・水光熱基本
 = GOP
 − FF&E更新積立（売上の3〜5%）
 = NOI（営業純利益）

清掃組数 = 稼働室夜 ÷ 平均宿泊日数(LOS)
```

> **v2026.08.1 での是正（重要）**
> 1. **GPIの定義を修正**：以前は GPI に RevPAR（＝ADR×稼働）を使ったうえで空室率3%を重ねており、
>    空室を二重に引いていました。現在は GPI＝満室潜在収入、稼働率は EGI で1回だけ掛けます。
> 2. **清掃を「1泊ごと」から「1組ごと」へ**：以前は稼働室夜ぶんの清掃費が発生する計算で、
>    平均3泊なら清掃費が約3倍でした。現在は稼働室夜÷LOSの「組数」で計上します。
>    あわせて1組あたりの単価を実勢（一棟12,000円／客室4,000円）に補正しています。
> 3. **清掃料金収入を計上可能に**：既定は0（＝ADRに清掃費が含まれている保守側の想定）。
>    ADRとは別にゲスト請求している場合のみ入力してください（二重計上防止）。

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

