"""判定ルールブックの出力.

「いまこのシステムはどんなルールで判定しているのか」を1ファイルに吐き出す。
基準をブラッシュアップするための土台（現状を読める形にしないと、どこを直すか決められない）。

出力に含めるもの:
  - `config/*.yaml`（基準の正本）の中身と version / as_of
  - 旅館業許可の12ゲートと、各ゲートが引いている一次情報（法令・条文・URL）
  - 調査パターンA〜Dの判定ロジック（**コードを正本として原文のまま抜粋**）
  - 収益試算の前提（エリア別ADR・稼働率・cap rate・USALI経費率・融資条件・工事費）
  - スコア重み、朝会スクリーニング閾値
  - コード側に埋まっている定数（＝YAML化されていない＝改善候補）

CLI:
    python -m core.rulebook                # 標準出力
    python -m core.rulebook -o ルールブック.md
"""
from __future__ import annotations

import inspect
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# 基準ファイルと「何の基準か」。CLAUDE.md §基準の正本と対応させる
CONFIG_FILES: List[Tuple[str, str]] = [
    ("zoning_rules.yaml", "用途地域 × 業態の可否（建基法48条・別表第二）"),
    ("distance_rules.yaml", "距離規制（旅館業法3条3項・自治体条例）"),
    ("building_code_rules.yaml", "建基法の技術基準（法27条・令119/120/121 等）"),
    ("municipality_rules.yaml", "自治体の上乗せ条例（客室面積・玄関帳場・距離）"),
    ("cost_estimates.yaml", "調査パターン別の概算費用・期間"),
    ("revenue_estimates.yaml", "収益の前提（ADR・稼働率・cap rate・経費率・融資）"),
    ("screening_rules.yaml", "朝会スクリーニングの判定閾値"),
]


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def _load(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _yaml_block(data: Any) -> str:
    dumped = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    return f"```yaml\n{dumped.rstrip()}\n```"


def _git_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(CONFIG_DIR.parent),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "—"
    except Exception:  # noqa: BLE001
        return "—"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (list, tuple)):
        return "・".join(str(x) for x in v)
    return str(v)


def _rng(d: Any, mult: float = 1.0, suffix: str = "") -> str:
    if not isinstance(d, dict):
        return _fmt(d)
    return (
        f"{d.get('min', 0) * mult:g}／{d.get('mid', 0) * mult:g}／"
        f"{d.get('max', 0) * mult:g}{suffix}"
    )


# ---------------------------------------------------------------------------
# 許可ゲートのカタログ（license_gate.py から抽出）
# ---------------------------------------------------------------------------


def gate_catalog() -> List[Dict[str, Any]]:
    """`_gate_*` 関数からゲート定義を抽出する.

    ハンドメイドの一覧を別に持つと必ずコードとズレるので、ソースから読む。
    """
    from . import license_gate

    src = inspect.getsource(license_gate)
    parts = re.split(r"\ndef (_gate_[a-z0-9_]+)\(", src)
    out: List[Dict[str, Any]] = []
    for fname, body in zip(parts[1::2], parts[2::2]):
        # 次のトップレベル def までで切る（切らないと最後のゲートが以降の全定義を拾う）
        body = re.split(r"\ndef ", body)[0]
        doc = re.search(r'"""(.+?)(?:\.|\n)', body, re.S)
        cites: List[str] = []
        for call in re.findall(r"cite\(([^)]*)\)", body):
            cites.extend(re.findall(r'"([^"]+)"', call))
        out.append(
            {
                "func": fname,
                "gate_ids": re.findall(r'gate_id="([^"]+)"', body),
                "categories": sorted(set(re.findall(r'category="([^"]+)"', body))),
                "titles": sorted(set(re.findall(r'title="([^"]+)"', body))),
                "doc": (doc.group(1).strip() if doc else ""),
                "cites": sorted(set(cites)),
            }
        )
    return out


def _evidence_label(key: str) -> str:
    from .evidence import PRIMARY_SOURCES

    ev = PRIMARY_SOURCES.get(key)
    if ev is None:
        return f"`{key}`（レジストリ未登録）"
    return f"{ev.law_name} {ev.article}".strip()


# ---------------------------------------------------------------------------
# セクション
# ---------------------------------------------------------------------------


def _s_header() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            "# 判定ルールブック（現状のルール全量）",
            "",
            f"**生成**: {now}／**コード**: `{_git_rev()}`",
            "",
            "> **これは何**: 用途変更フィジビリティ判定システムが「いま」使っているルールの全量。"
            "基準をブラッシュアップするときに、**どこを直せばどの判定が動くか**を1枚で見るための資料。",
            ">",
            "> **ルールの正本は2箇所しかない**",
            "> 1. `config/*.yaml` … 数値基準・費用・収益前提・閾値（**ここを直すのがブラッシュアップ**）",
            "> 2. `core/*.py` … 法令の当てはめロジックそのもの（条件分岐）。"
            "数値を直したいだけならコードは触らない",
            ">",
            "> **直すときの約束**（`CLAUDE.md §基準`）：値を動かしたらその行に "
            "**①出典（誰の・いつの一次情報か） ②適用範囲** をコメントで残し、"
            "ファイル冒頭の `version` と `as_of` を上げる。",
        ]
    )


def _s_config_index() -> str:
    L = ["## 1. 基準ファイル一覧（正本の場所）", "", "| ファイル | 何の基準か | version | as_of |", "|---|---|---|---|"]
    for name, what in CONFIG_FILES:
        d = _load(name)
        L.append(
            f"| `config/{name}` | {what} | {_fmt(d.get('version'))} | {_fmt(d.get('as_of'))} |"
        )
    L.append("")
    L.append(
        "> `version` / `as_of` が空のファイルは、**いつ・誰の情報で作った値なのか追えない状態**。"
        "改善の初手はここを埋めること。"
    )
    return "\n".join(L)


def _s_pipeline() -> str:
    return "\n".join(
        [
            "## 2. 判定の流れ（どの順で何が決まるか）",
            "",
            "```",
            "住所 ─→ ①用途地域・防火地域・学校距離（api/gis_client.py：不動産情報ライブラリ）",
            "          │",
            "          ├─→ ②旅館業許可ゲート12（core/license_gate.py）→ 🟢🟡🟠🔴⛔【主判定】",
            "          │      A立地 A1用途地域 A2都市計画 A3学校100m A4公衆衛生",
            "          │      B建物 B1用途変更確認申請 B2法27条耐火 B3避難 B4消防",
            "          │      C構造設備 C1客室面積 C2玄関帳場 C3入浴・便所",
            "          │      D申請者 D1欠格事由",
            "          │      ↑ 自治体条例の上乗せは config/municipality_rules.yaml と",
            "          │        core/legal_research.py（LLM調査・検証済みのみ採用）",
            "          │",
            "          ├─→ ③調査パターンA〜D（core/pattern_classifier.py）",
            "          │      検査済証 × 築年数 × 改修履歴 → 調査の深さが決まる",
            "          │      → ④概算費用・期間（config/cost_estimates.yaml）",
            "          │",
            "          ├─→ ⑤収益試算（core/profitability.py＋config/revenue_estimates.yaml）",
            "          │      ADR×稼働率×室数 → NOI → 収益価格[A]／原価法[B] → 割安・割高",
            "          │",
            "          ├─→ ⑥スコア（core/scoring.py：重み付き）",
            "          │",
            "          └─→ ⑦朝会スクリーニング（core/morning_brief.py＋config/screening_rules.yaml）",
            "                 → 🟢載せる／🟡保留／🔴見送り",
            "```",
            "",
            "**設計の原則**：数値基準と法令の当てはめは**コードで決定的に**、"
            "条例の探索と引用は**LLMで**。LLMの所見が判定を動かせるのは"
            "「原文引用あり＋公的ドメインのURLあり」の両方を満たすときだけ。",
        ]
    )


def _s_gates() -> str:
    L = [
        "## 3. 旅館業許可ゲート（主判定・core/license_gate.py）",
        "",
        "各ゲートは `pass / conditional / consult / fail / unknown / not_applicable` のいずれかを返す。"
        "`blocking=True` のゲートだけが総合判定を動かす。`hard=True` の FAIL は物件内に回避策がない（⛔）。",
        "",
        "| ゲート | 区分 | 見ているもの | 引いている一次情報 |",
        "|---|---|---|---|",
    ]
    for g in gate_catalog():
        gate_id = g["gate_ids"][0] if g["gate_ids"] else g["func"]
        cat = "・".join(g["categories"]) or "—"
        cites = "<br>".join(_evidence_label(k) for k in g["cites"]) or "—"
        L.append(f"| `{gate_id}` | {cat} | {g['doc'] or '—'} | {cites} |")
    L.append("")
    L.append("### 総合判定の集約ルール（コード原文）")
    L.append("")
    L.append("ゲートの組み合わせから 🟢🟡🟠🔴⛔ をどう決めているか。**ここが主判定の心臓部**。")
    L.append("")
    try:
        from . import license_gate

        L.append("```python")
        L.append(inspect.getsource(license_gate._aggregate).rstrip())
        L.append("```")
    except Exception as exc:  # noqa: BLE001
        L.append(f"（抽出できませんでした: {exc}）")
    return "\n".join(L)


def _s_evidence() -> str:
    from .evidence import PRIMARY_SOURCES, TIER_LABEL

    L = [
        "## 4. 一次情報レジストリ（人手で確認した正本・LLM非関与）",
        "",
        f"全{len(PRIMARY_SOURCES)}件。判定が参照してよいのは、URLと原文引用が揃っているものだけ。",
        "",
        "| キー | 法令・条文 | 出典階層 | 確認日 | 検証済 | URL |",
        "|---|---|---|---|---|---|",
    ]
    for key, ev in PRIMARY_SOURCES.items():
        L.append(
            f"| `{key}` | {ev.law_name} {ev.article} | "
            f"{TIER_LABEL.get(ev.source_tier, '—')} | {ev.checked_on or '—'} | "
            f"{'✅' if ev.verified else '⚠️'} | {ev.url or '—'} |"
        )
    L.append("")
    L.append(
        "> ⚠️ が付いているものは判定に使われない（表示のみ）。"
        "自治体条例は `core/legal_research.py` が調査し、"
        "`config/municipality_research_cache.yaml` に蓄積される。"
    )
    return "\n".join(L)


def _s_zoning() -> str:
    d = _load("zoning_rules.yaml")
    areas = d.get("zoning_areas", {}) or {}
    L = [
        "## 5. 用途地域 × 業態（config/zoning_rules.yaml）",
        "",
        "根拠：建築基準法48条／別表第二。`prohibited` は本則不可、"
        "`special_only` は特定行政庁の許可（48条ただし書き）があれば可。",
        "",
        "**業態の当てはめ**（`core/zoning.py _business_key`）",
        "",
        "- 旅館・ホテル営業／**簡易宿所営業** … どちらも下表の `hotel` を引く"
        "（建基法上の用途区分が同じ）",
        "- **民泊（住宅宿泊事業）** … 建基法上は「住宅」扱いのため用途地域の制限を受けない"
        "（下表は適用されない。代わりに自治体条例の営業日数・区域制限で判定する）",
        "",
        "| 用途地域 | コード | 旅館・ホテル／簡易宿所 | 理由（別表第二の項） |",
        "|---|---|---|---|",
    ]
    for _key, a in areas.items():
        hotel = a.get("hotel") or {}
        L.append(
            f"| {a.get('name', _key)} | {a.get('code', '—')} | "
            f"`{_fmt(hotel.get('status'))}` | {hotel.get('reason', '—')} |"
        )
    other = {k: v for k, v in d.items() if k not in ("zoning_areas",)}
    if other:
        L += ["", "### そのほかの規定", "", _yaml_block(other)]
    return "\n".join(L)


def _s_distance() -> str:
    d = _load("distance_rules.yaml")
    return "\n".join(
        [
            "## 6. 距離規制（config/distance_rules.yaml）",
            "",
            "根拠：旅館業法3条3項・4項。学校等から一定距離内は都道府県知事への意見聴取が必要になり得る。",
            "",
            _yaml_block(d),
        ]
    )


def _s_building_code() -> str:
    d = _load("building_code_rules.yaml")
    rules = d.get("rules", {}) or {}
    L = [
        "## 7. 建築基準法の技術基準（config/building_code_rules.yaml）",
        "",
        "| ルール | 条文 | 影響度 | 中身 |",
        "|---|---|---|---|",
    ]
    for key, r in rules.items():
        detail_keys = [k for k in r if k not in ("rule_id", "rule_name", "article", "impact")]
        detail = "<br>".join(
            f"{k}: {_fmt(r[k]) if not isinstance(r[k], dict) else '／'.join(f'{kk}={vv}' for kk, vv in r[k].items())}"
            for k in detail_keys
        )
        L.append(
            f"| {r.get('rule_name', key)} | {r.get('article', '—')} | "
            f"{_fmt(r.get('impact'))} | {detail or '—'} |"
        )
    other = {k: v for k, v in d.items() if k != "rules"}
    if other:
        L += ["", "### そのほか", "", _yaml_block(other)]
    return "\n".join(L)


def _s_municipality() -> str:
    d = _load("municipality_rules.yaml")
    munis = d.get("municipalities", {}) or {}
    L = [
        "## 8. 自治体の上乗せ条例（config/municipality_rules.yaml）",
        "",
        f"登録 {len(munis)} 自治体。**ここに無い自治体は全国共通の基準だけで判定される**"
        "（＝上乗せを見落とす可能性がある）。住所の前方一致で当てている。",
        "",
        "| 自治体 | 客室面積 最低 | ベッドのみ | 学校距離 | 玄関帳場・無人運営 |",
        "|---|---|---|---|---|",
    ]
    for key, m in munis.items():
        ra = m.get("room_area") or {}
        dist = m.get("distance_to_school") or {}
        fd = m.get("front_desk") or {}
        L.append(
            f"| {m.get('name', key)} | {_fmt(ra.get('min_m2'))}㎡ | "
            f"{_fmt(ra.get('bed_only_min_m2'))}㎡ | {_fmt(dist.get('distance_m'))}m | "
            f"{fd.get('note', '—')} |"
        )
    L.append("")
    L.append("> 無人運営の可否は区によって違う（例：目黒区は不可＝人件費が乗る）。"
             "この列が空の自治体は**未確認**であって「可」ではない。")
    return "\n".join(L)


def _s_pattern_and_cost() -> str:
    L = [
        "## 9. 調査パターンA〜Dと概算費用・期間",
        "",
        "### 9-1. パターン判定ロジック（core/pattern_classifier.py・コード原文）",
        "",
        "検査済証 × 築年数 × 改修履歴の3軸。**コードが正本**なので原文を貼る。",
        "",
    ]
    try:
        from . import pattern_classifier

        L.append("```python")
        L.append(inspect.getsource(pattern_classifier.classify_pattern).rstrip())
        L.append("```")
        L.append("")
        L.append(
            f"法改正の節目：新耐震 {pattern_classifier.SEISMIC_1981} 年／"
            f"木造 {pattern_classifier.WOODEN_2000} 年基準／"
            f"構造計算厳格化 {pattern_classifier.STRUCT_CALC_2007} 年。"
            "またぐと構造調査が必須になる。"
        )
    except Exception as exc:  # noqa: BLE001
        L.append(f"（抽出できませんでした: {exc}）")

    d = _load("cost_estimates.yaml")
    patterns = d.get("patterns", {}) or {}
    L += [
        "",
        "### 9-2. パターン別の費用・期間（config/cost_estimates.yaml・単位:万円／月）",
        "",
        "| パターン | 内容 | 調査 | 申請 | 工事 | 合計期間 | 確度 |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, p in patterns.items():
        def _c(sec: str) -> str:
            s = p.get(sec) or {}
            if not s:
                return "—"
            return f"{_fmt(s.get('cost_min'))}〜{_fmt(s.get('cost_max'))}万"

        total = p.get("total") or {}
        L.append(
            f"| **{key}** | {p.get('name', '—')} | {_c('investigation')} | "
            f"{_c('application')} | {_c('renovation')} | "
            f"{_fmt(total.get('months_min'))}〜{_fmt(total.get('months_max'))}ヶ月 | "
            f"{_fmt(total.get('confidence'))} |"
        )
    other = {k: v for k, v in d.items() if k != "patterns"}
    if other:
        L += ["", "### 9-3. そのほかの費用基準", "", _yaml_block(other)]
    return "\n".join(L)


def _s_revenue() -> str:
    d = _load("revenue_estimates.yaml")
    L = [
        "## 10. 収益試算の前提（config/revenue_estimates.yaml）",
        "",
        f"**version {_fmt(d.get('version'))}（{_fmt(d.get('as_of'))} 時点）**"
        "／⚠️ 案件固有の実績値を全国既定にしないこと（`CLAUDE.md`）。",
        "",
        "### 10-1. エリア区分（min／mid／max）",
        "",
        "| エリア | RevPAR(円) | 稼働率 | cap rate | 土地坪単価(万円) | 1人単価(円) | 判定キーワード |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, t in (d.get("area_tiers", {}) or {}).items():
        L.append(
            f"| {t.get('label', key)} | {_rng(t.get('revpar_yen'))} | "
            f"{_rng(t.get('occupancy'), 100, '%')} | {_rng(t.get('cap_rate'), 100, '%')} | "
            f"{_rng(t.get('land_price_per_tsubo_man'))} | "
            f"{_rng(t.get('adr_per_guest_yen'))} | "
            f"{'・'.join((t.get('keywords') or [])[:6])} |"
        )

    L += [
        "",
        "### 10-2. 構造別（建築費・耐用年数）",
        "",
        "| 構造 | 建築費(万円/坪) | 解体費(万円/坪) | 法定耐用年数 |",
        "|---|---|---|---|",
    ]
    for key, s in (d.get("structure", {}) or {}).items():
        L.append(
            f"| {key} | {_fmt(s.get('build_cost_per_tsubo_man'))} | "
            f"{_fmt(s.get('demo_cost_per_tsubo_man'))} | "
            f"{_fmt(s.get('useful_life_years'))}年 |"
        )

    for section, title in [
        ("constants", "10-3. 定数（面積・定員・LOS 等）"),
        ("usali", "10-4. USALI 経費率"),
        ("finance", "10-5. 融資の既定条件"),
        ("works_defaults", "10-6. 初期工事費・目標利回りの既定"),
        ("rampup", "10-7. 開業立ち上がり"),
        ("seasonality", "10-8. 季節性"),
        ("presets", "10-9. 案件別プリセット"),
        ("lenders", "10-10. 金融機関マッチング"),
    ]:
        if d.get(section):
            L += ["", f"### {title}", "", _yaml_block({section: d[section]})]

    known = {
        "version", "as_of", "area_tiers", "structure", "constants", "usali",
        "finance", "works_defaults", "rampup", "seasonality", "presets", "lenders",
    }
    rest = {k: v for k, v in d.items() if k not in known}
    if rest:
        L += ["", "### 10-11. そのほか", "", _yaml_block(rest)]
    return "\n".join(L)


def _s_scoring() -> str:
    from .scoring import DEFAULT_WEIGHTS, WEIGHT_METADATA

    meta = {m["key"]: m for m in WEIGHT_METADATA}
    total = sum(DEFAULT_WEIGHTS.values())
    L = [
        "## 11. スコアの重み（core/scoring.py）",
        "",
        f"合計 {total:g}。⛔許可が下りない物件はスコア自体をブロックする（0点・✕）。",
        "",
        "| 項目 | 区分 | 重み | シェア | 中身 |",
        "|---|---|---|---|---|",
    ]
    for key, w in sorted(DEFAULT_WEIGHTS.items(), key=lambda kv: -kv[1]):
        m = meta.get(key, {})
        L.append(
            f"| {m.get('label', key)} | {m.get('category', '—')} | {w:g} | "
            f"{w / total * 100:.1f}% | {m.get('description', '—')} |"
        )
    return "\n".join(L)


def _s_screening() -> str:
    d = _load("screening_rules.yaml")
    L = [
        "## 12. 朝会スクリーニング（config/screening_rules.yaml）",
        "",
        f"**version {_fmt(d.get('version'))}（{_fmt(d.get('as_of'))} 時点）**"
        "／「購入検討物件にまとめるか」の判定閾値。`core/morning_brief.py` が使う。",
        "",
        _yaml_block(d),
        "",
        "**判定の順序**（`morning_brief.screen`）",
        "",
        "1. 落とす … 用途変更が🔴/⛔ ／ 価格が割高水準 ／ 利回りが下限未満・DSCR<1",
        "2. 保留 … 必須入力が空 ／ 行政協議待ち ／ 検査済証なし ／ 期間超過 ／ 目標利回り未達",
        "3. 載せる … 上のどれにも当たらない",
    ]
    return "\n".join(L)


def _s_code_constants() -> str:
    L = [
        "## 13. コード側に埋まっている基準（＝YAML化されていない改善候補）",
        "",
        "ここにある値は**YAMLを直しても変わらない**。動かすにはコードを触る必要がある。",
        "",
        "| 場所 | 値 | 何に効くか |",
        "|---|---|---|",
    ]
    rows: List[Tuple[str, str, str]] = []
    try:
        from . import pattern_classifier as pc

        rows += [
            ("core/pattern_classifier.py", f"新耐震 {pc.SEISMIC_1981}年", "構造調査の要否・スコア"),
            ("core/pattern_classifier.py", f"木造2000年基準 {pc.WOODEN_2000}年", "同上"),
            ("core/pattern_classifier.py", f"構造計算厳格化 {pc.STRUCT_CALC_2007}年", "同上"),
            ("core/pattern_classifier.py", "築15年以下→A／30年以上→C", "調査パターン"),
        ]
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import profitability as pf

        rows.append(("core/profitability.py", f"1坪 = {pf.TSUBO}㎡", "坪単価換算全般"))
    except Exception:  # noqa: BLE001
        pass
    rows += [
        ("core/profitability.py", "良物件判定 DSCR≧1.2・返済比率≦50%・CF>0", "融資の回り方の3段階判定"),
        ("core/license_gate.py", "用途変更確認申請の200㎡ライン", "B1ゲート（建基法87条）"),
        ("core/license_gate.py", "信頼度：情報充足率85%以上=高／60%以上=中", "判定の確度表示"),
        ("core/report_generator.py", "レポートのセクション構成・並び", "詳細レポートの体裁"),
    ]
    for where, val, effect in rows:
        L.append(f"| `{where}` | {val} | {effect} |")
    L.append("")
    L.append(
        "> **改善の考え方**：数値の見直しが繰り返し起きる項目はYAMLへ出す。"
        "法令由来で動かないもの（200㎡ライン等）はコードのままでよい。"
    )
    return "\n".join(L)


def _s_howto() -> str:
    return "\n".join(
        [
            "## 14. ブラッシュアップの手順",
            "",
            "1. **どの判定を変えたいか決める**（例：東京23区のADRが実勢と合っていない）",
            "2. §1の表で**該当ファイルを特定**する（例：`config/revenue_estimates.yaml`）",
            "3. 値を直し、その行に **出典（誰の・いつの一次情報か）と適用範囲**をコメントで書く",
            "4. ファイル冒頭の `version` と `as_of` を上げる",
            "5. `python tests/test_profitability.py` などで回帰を確認する",
            "6. コミットメッセージに出典を1行入れる"
            "（例：`chore(基準): 運営代行を5%→15%へ（宮沢さん実績25物件・2026-09-03）`）",
            "",
            "**実データの在り処**：民泊チームの実績（ADR前提・運営コスト・融資条件）は"
            "隣の `../minpaku/projects/` にある（読み取り専用）。"
            "学び・新事実は Notion「ナレッジInbox」へ投函する。",
        ]
    )


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------


def generate_rulebook_markdown() -> str:
    """ルールブック全文（Markdown）."""
    sections = [
        _s_header(),
        _s_config_index(),
        _s_pipeline(),
        _s_gates(),
        _s_evidence(),
        _s_zoning(),
        _s_distance(),
        _s_building_code(),
        _s_municipality(),
        _s_pattern_and_cost(),
        _s_revenue(),
        _s_scoring(),
        _s_screening(),
        _s_code_constants(),
        _s_howto(),
    ]
    return "\n\n".join(s for s in sections if s)


def generate_rulebook_html() -> str:
    from .md_document import markdown_to_html_document

    return markdown_to_html_document(
        title="判定ルールブック — 用途変更フィジビリティ判定システム",
        markdown_text=generate_rulebook_markdown(),
    )


def generate_rulebook_pdf() -> Optional[bytes]:
    from .md_document import html_to_pdf

    return html_to_pdf(generate_rulebook_html())


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="判定ルールブックを出力する")
    ap.add_argument("-o", "--output", help="出力先ファイル（.md / .html）")
    args = ap.parse_args()

    if args.output and args.output.endswith(".html"):
        text = generate_rulebook_html()
    else:
        text = generate_rulebook_markdown()

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"✅ {args.output} に書き出しました（{len(text):,} 文字）")
    else:
        print(text)


if __name__ == "__main__":
    _main()
