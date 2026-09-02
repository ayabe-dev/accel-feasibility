"""自治体調査層（core/legal_research.py）の配線テスト.

実APIは呼ばない。擬似クライアントで
  ① web検索ループと pause_turn の再開
  ② output_config 非対応SDKへのフォールバック
  ③ JSON抽出（生JSON / ```json フェンス / 前後に説明文）
  ④ 検証ガード（原文引用＋公的ドメインURL）
  ⑤ キャッシュの往復とスキーマ版の無効化
を確認する。

実行： python3 tests/test_legal_research.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import legal_research as lr  # noqa: E402
from core.models import BusinessType  # noqa: E402


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _resp(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(content=[_text_block(text)], stop_reason=stop_reason)


SAMPLE = {
    "municipality_name": "テスト区",
    "permit_authority": "テスト区長／テスト区保健所生活衛生課",
    "summary": "客室面積に上乗せあり。無人運営は不可。",
    "findings": [
        {
            "topic": "front_desk",
            "title": "玄関帳場に従業員の常駐が必要",
            "requirement": "ICT代替を用いる場合も従業員が常駐すること",
            "impact": "conditional",
            "law_name": "テスト区旅館業法施行条例",
            "article": "第5条",
            "quote": "営業者は、玄関帳場等に常時従業者を置かなければならない。",
            "url": "https://www.city.test.lg.jp/reiki/ryokan.html",
            "publisher": "テスト区例規集",
        }
    ],
    "minpaku": {
        "status": "restricted",
        "restriction_summary": "住居専用地域は平日不可",
        "max_days_per_year": 120,
        "url": "https://www.city.test.lg.jp/minpaku.html",
    },
    "unresolved": ["便所の必要数のテーブル"],
}


class FakeClient:
    """messages.create を模した擬似クライアント."""

    def __init__(self, responses, reject_output_config=False):
        self._responses = list(responses)
        self.calls = []
        self.reject_output_config = reject_output_config
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        if self.reject_output_config and "output_config" in kwargs:
            raise TypeError(
                "create() got an unexpected keyword argument 'output_config'"
            )
        self.calls.append(kwargs)
        return self._responses.pop(0)


# ---------------------------------------------------------------------------


def test_extract_plain_json():
    got = lr._extract_json(_resp(json.dumps(SAMPLE, ensure_ascii=False)))
    assert got["municipality_name"] == "テスト区"
    print("✅ 生JSONを抽出できる")


def test_extract_fenced_json():
    body = "調査しました。\n\n```json\n" + json.dumps(SAMPLE, ensure_ascii=False) + "\n```\n"
    got = lr._extract_json(_resp(body))
    assert got["permit_authority"].startswith("テスト区長")
    print("✅ ```json フェンス付きでも抽出できる")


def test_extract_json_with_surrounding_text():
    body = "以下が結果です。" + json.dumps(SAMPLE, ensure_ascii=False) + " 以上です。"
    got = lr._extract_json(_resp(body))
    assert got["minpaku"]["max_days_per_year"] == 120
    print("✅ 前後に説明文があっても抽出できる")


def test_extract_returns_none_on_garbage():
    assert lr._extract_json(_resp("JSONを作れませんでした")) is None
    print("✅ JSONでなければ None を返す（誤読しない）")


def test_pause_turn_is_resumed():
    """server-side tool が上限に達した pause_turn を再開して最終JSONに到達する."""
    client = FakeClient(
        [
            _resp("（検索中）", stop_reason="pause_turn"),
            _resp("（まだ検索中）", stop_reason="pause_turn"),
            _resp(json.dumps(SAMPLE, ensure_ascii=False)),
        ]
    )
    got = lr._run_research(
        client, "claude-opus-5", [{"type": "web_search_20260209", "name": "web_search"}], "調べて"
    )
    assert got is not None and got["municipality_name"] == "テスト区"
    assert len(client.calls) == 3, len(client.calls)
    print("✅ pause_turn を再開して最終応答まで到達する")


def test_output_config_fallback_for_old_sdk():
    """output_config 非対応SDKでは、プロンプトでJSONを指示する経路に落ちる."""
    client = FakeClient(
        [_resp("```json\n" + json.dumps(SAMPLE, ensure_ascii=False) + "\n```")],
        reject_output_config=True,
    )
    got = lr._run_research(
        client, "claude-opus-5", [{"type": "web_search_20260209", "name": "web_search"}], "調べて"
    )
    assert got is not None
    assert "output_config" not in client.calls[0]
    assert "出力形式" in client.calls[0]["system"]
    print("✅ output_config 非対応SDKでもフォールバックしてJSONを得る")


def test_web_search_tool_type_by_model():
    assert lr._web_search_type("claude-opus-5") == "web_search_20260209"
    assert lr._web_search_type("claude-sonnet-5") == "web_search_20260209"
    assert lr._web_search_type("claude-haiku-4-5") == "web_search_20250305"
    print("✅ モデルに応じた web 検索ツールの type を選ぶ")


def test_result_verification_guard():
    r = lr.ResearchResult(
        municipality_key="test", researched_on="2026-08-31", findings=[
            # 検証済み
            SAMPLE["findings"][0],
            # 引用なし
            {**SAMPLE["findings"][0], "quote": "", "impact": "blocking",
             "title": "引用なしの所見"},
            # 民間ドメイン
            {**SAMPLE["findings"][0], "url": "https://note.com/a", "impact": "blocking",
             "title": "民間ドメインの所見"},
        ]
    )
    assert r.blocking_findings() == []
    assert len(r.conditional_findings(["front_desk"])) == 1
    evs = r.evidences(["front_desk"])
    assert len(evs) == 3
    assert [e.verified for e in evs] == [True, False, False]
    assert "未検証" in evs[1].note and "未検証" in evs[2].note
    print("✅ 未検証の所見は判定に使わず、理由つきで表示だけする")


def test_cache_roundtrip(tmp_suffix="__pytest_tmp"):
    """キャッシュの保存と読み出し。既存キャッシュは壊さない."""
    key = "zzz_test_municipality" + tmp_suffix
    original = lr._load_cache()
    try:
        result = lr.ResearchResult(
            municipality_key=key,
            municipality_name="テスト区",
            summary="テスト",
            researched_on="2026-08-31",
            schema_version=lr.SCHEMA_VERSION,
        )
        cache = lr._load_cache()
        cache[lr._cache_key(key, BusinessType.HOTEL_RYOKAN)] = result.model_dump(
            exclude={"from_cache"}
        )
        lr._save_cache(cache)

        hit = lr.cached_result(key, BusinessType.HOTEL_RYOKAN)
        assert hit is not None and hit.from_cache is True
        assert hit.municipality_name == "テスト区"

        # 旅館・ホテルと簡易宿所は同じ条例を見るのでキャッシュを共用する
        assert lr.cached_result(key, BusinessType.SIMPLE_LODGING) is not None
        # 民泊は別スコープ
        assert lr.cached_result(key, BusinessType.MINPAKU) is None

        # スキーマ版が変わると無効化される
        cache[lr._cache_key(key, BusinessType.HOTEL_RYOKAN)]["schema_version"] = "0.0.0"
        lr._save_cache(cache)
        assert lr.cached_result(key, BusinessType.HOTEL_RYOKAN) is None
        print("✅ キャッシュの往復・業態スコープ・スキーマ版の無効化が効く")
    finally:
        lr._save_cache(original)


def test_missing_credentials_degrades_gracefully():
    """資格情報が無いとき、SDKの生エラーではなく人が読める説明を返す.

    SDK は認証の解決を送信時まで遅らせるので、クライアントを構築できたことを
    「使える」と誤判定しないこともここで固定する。
    """
    import os

    saved = {
        k: os.environ.pop(k, None)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
    try:
        assert lr.is_available() is False, "資格情報が無いのに available になっている"
        r = lr.research_municipality(
            "zzz_no_key", "テスト区", "テスト区1-1-1", refresh=True
        )
        assert r.available is False
        assert "ANTHROPIC_API_KEY" in r.error, r.error
        # SDK の生メッセージを露出させない
        assert "Could not resolve authentication" not in r.error, r.error
    finally:
        for k, v in saved.items():
            if v:
                os.environ[k] = v
    print("✅ 資格情報が無くても落ちず、人が読める説明を返して法令のみの判定に退避する")


if __name__ == "__main__":
    test_extract_plain_json()
    test_extract_fenced_json()
    test_extract_json_with_surrounding_text()
    test_extract_returns_none_on_garbage()
    test_pause_turn_is_resumed()
    test_output_config_fallback_for_old_sdk()
    test_web_search_tool_type_by_model()
    test_result_verification_guard()
    test_cache_roundtrip()
    test_missing_credentials_degrades_gracefully()
    print("\n🎉 自治体調査層テスト 全パス")
