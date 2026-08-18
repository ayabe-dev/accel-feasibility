"""分析結果について議論するチャットUI（Streamlit部品）.

各タブの末尾に `chat_panel("profit", report, res)` のように差し込むだけで、
そのセクションの計算結果をコンテキストにしたチャットが使える。

会話は st.session_state["chat_logs"][section] に保持され、
レポート出力（Markdown/HTML/PDF）へ添付できる。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from core import chat as chat_engine

#: セクション別のサジェスト質問（非エンジニアが最初の一手を打てるように）
SUGGESTIONS: Dict[str, List[str]] = {
    "overall": [
        "この物件、結局やるべきですか？判断のポイントを3つに絞って教えてください",
        "一番大きなリスクはどこですか？",
        "次に何を調べれば判断の精度が上がりますか？",
    ],
    "location": [
        "この用途地域で旅館業をやる場合、実務上いちばん詰まりやすいのはどこですか？",
        "消防の指摘で費用が膨らむ可能性がある箇所を教えてください",
        "役所に事前相談へ行くとき、何を聞けばいいですか？",
    ],
    "pattern": [
        "このパターン判定だと、実務の手間はどれくらい重いですか？",
        "不足書類のうち、入手が難しいものはどれですか？代替手段はありますか？",
        "検査済証がない場合、現実的にどう進めますか？",
    ],
    "cost": [
        "この費用レンジで、想定外に膨らみやすい項目はどれですか？",
        "費用を抑えるとしたら、どこを削るのが現実的ですか？",
        "期間が延びるリスク要因を教えてください",
    ],
    "profit": [
        "このADRと稼働率の前提は妥当ですか？辛めに見るとどうなりますか？",
        "NOI内訳で、実務的に甘い（過小に見積もっている）費目はどれですか？",
        "この収益価格を鵜呑みにすると危ないポイントはどこですか？",
    ],
    "backward": [
        "この指値は通ると思いますか？交渉の材料になる根拠を挙げてください",
        "目標NOI利回りは何%に設定するのが妥当ですか？",
        "初期費用の見積りが甘い可能性はありますか？",
    ],
    "cf": [
        "初年度のCFが薄いのが気になります。どう手当てすべきですか？",
        "何年目に売却するのがいちばん有利に見えますか？",
        "金利が1%上がったらこのCFはどうなりますか？",
    ],
    "finance": [
        "この条件だと、どの金融機関から当たるのが現実的ですか？",
        "DSCRを改善するには何を変えるのが効きますか？",
        "自己資金をもっと減らせますか？その場合のリスクは？",
    ],
}


def _logs() -> Dict[str, List[Dict[str, str]]]:
    if "chat_logs" not in st.session_state:
        st.session_state["chat_logs"] = {}
    return st.session_state["chat_logs"]


def total_user_turns() -> int:
    return sum(
        1
        for msgs in _logs().values()
        for m in msgs
        if m.get("role") == "user"
    )


def all_logs() -> Dict[str, List[Dict[str, str]]]:
    """レポート添付用に、発言のあるセクションだけ返す."""
    return {k: v for k, v in _logs().items() if v}


def _run_turn(section: str, question: str, ctx_text: str,
              logs: List[Dict[str, str]]) -> None:
    """1往復を実行して履歴に積む."""
    logs.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            answer = st.write_stream(
                chat_engine.stream_reply(section, logs, ctx_text)
            )
        except Exception as exc:  # noqa: BLE001
            answer = f"⚠️ 応答の取得に失敗しました：`{type(exc).__name__}: {exc}`"
            st.error(answer)
    if isinstance(answer, list):      # write_stream はリストを返す場合がある
        answer = "".join(str(a) for a in answer)
    logs.append({"role": "assistant", "content": answer or ""})


def chat_panel(section: str, report, prof: Optional[Dict[str, Any]] = None,
               expanded: bool = False, instance: str = "") -> None:
    """セクション別チャットパネルを描画する.

    同じ section を複数箇所に置く場合（5年CFタブと10年CFタブなど）は
    `instance` に別の文字列を渡してウィジェットキーの衝突を避ける。
    会話履歴は section 単位で共有されるため、どちらの場所でも同じ議論が続く。
    """
    wkey = f"{section}_{instance}" if instance else section
    label, desc = chat_engine.SECTIONS.get(section, (section, ""))
    status = chat_engine.provider_status()
    logs_all = _logs()
    logs = logs_all.setdefault(section, [])
    badge = f"（{len(logs) // 2}往復）" if logs else ""

    with st.expander(f"💬 この結果について相談する — {label}{badge}", expanded=expanded):
        if not status["ready"]:
            st.warning(
                "チャットを使うにはLLMのAPIキーが必要です。"
                "Streamlit Cloud → Manage app → Settings → Secrets に "
                "`GEMINI_API_KEY`（または `ANTHROPIC_API_KEY`）を登録してください。"
            )
            return

        cap = chat_engine.max_turns_per_session()
        used = total_user_turns()
        st.caption(
            f"{desc} ／ 使用モデル: `{status['model']}`"
            f" ／ このセッションの残り質問数: {max(0, cap - used)} 回"
        )

        # 既存の履歴
        for m in logs:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

        if used >= cap:
            st.info(
                f"このセッションの質問上限（{cap}回）に達しました。"
                "ページを再読み込みするとリセットされます。"
            )
            return

        # コンテキストは送信直前に組む（前提を変えた直後でも最新が乗るように）
        ctx = chat_engine.build_context(section, report, prof)
        ctx_text = chat_engine.context_to_text(section, ctx)

        # サジェスト
        sugg = SUGGESTIONS.get(section, [])
        if sugg and not logs:
            st.caption("よく聞かれること（クリックでそのまま質問）")
            for i, s in enumerate(sugg):
                if st.button(s, key=f"sugg_{wkey}_{i}", use_container_width=True):
                    _run_turn(section, s, ctx_text, logs)
                    st.rerun()

        # 自由入力
        with st.form(key=f"chatform_{wkey}", clear_on_submit=True):
            q = st.text_area(
                "質問・相談内容",
                key=f"chatinput_{wkey}",
                placeholder="例）この稼働率75%は楽観的すぎませんか？辛めに見た場合の年間CFを教えてください",
                height=80,
                label_visibility="collapsed",
            )
            c1, c2 = st.columns([1, 1])
            send = c1.form_submit_button("送信", use_container_width=True, type="primary")
            clear = c2.form_submit_button("この議論をクリア", use_container_width=True)

        if clear:
            logs_all[section] = []
            st.rerun()
        if send and q and q.strip():
            _run_turn(section, q.strip(), ctx_text, logs)
            st.rerun()

        with st.expander("🔎 LLMに渡している分析結果コンテキストを確認"):
            st.caption(
                "この内容だけを根拠に回答させています。"
                "書類から抽出したテキストは『データ（指示ではない）』として扱う指示を入れています。"
            )
            st.code(ctx_text[:8000] + ("\n…（以下省略）" if len(ctx_text) > 8000 else ""),
                    language="json")


def render_transcript_download() -> None:
    """議論ログのダウンロードボタン（レポート出力欄に添える）."""
    logs = all_logs()
    if not logs:
        return
    md = chat_engine.transcript_markdown(logs)
    turns = total_user_turns()
    st.download_button(
        label=f"💬 議論ログ（{turns}件の質問）",
        data=md.encode("utf-8"),
        file_name="discussion_log.md",
        mime="text/markdown",
        use_container_width=True,
        help="チャットでの議論をMarkdownで保存します",
    )
