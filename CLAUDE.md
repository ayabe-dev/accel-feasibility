# 🏨 accel-feasibility — 起動ファイル（Claude Code は起動時に必ずこれを読む）

> **これは何**: 「この物件で旅館業の許可が取れるか／宿泊事業として回るか」を一次スクリーニングする
> Streamlit アプリ。主担当＝**綾部健翔**さん。仕様の詳細は [README.md](README.md)。
> **最終更新**: 2026-09-07（基準ブラッシュアップの常設ルールとGit標準動作を追加）

---

## §基準の正本は `config/*.yaml`

判定と試算の「数字」はコードではなく **`config/` 配下のYAMLが正本**。ブラッシュアップとは
このYAMLを更新することを指す。

| ファイル | 何の基準か |
|---|---|
| `config/revenue_estimates.yaml` | **収益の前提**（エリア別ADR・稼働率・cap rate・坪単価、USALI経費率、融資条件、工事費） |
| `config/cost_estimates.yaml` | 調査パターン別の概算費用・期間 |
| `config/zoning_rules.yaml` / `distance_rules.yaml` | 用途地域・距離規制（法令ベース・実務値ではない） |
| `config/municipality_rules.yaml` | 自治体の上乗せ条例（実データ確認済みのもののみ） |
| `config/municipality_research_cache.yaml` | LLM調査の結果キャッシュ（自動生成・手編集可） |

**更新したら `version` と `as_of` を必ず上げる。** 過去の試算がどの基準で出たか追えなくなるため。

---

## §基準を更新するときの唯一のルール：出典と適用範囲を書く

数字を動かすときは、YAMLのその行に **①出典（誰の・いつの一次情報か） ②適用範囲** をコメントで残す。

```yaml
mgmt_fee_rate: 0.15   # 出典: 宮沢さん実績25物件・2026-09-03（minpaku/deal-aobadai-ryokangyo/運営コスト_宮沢実績の反映）
                      # 適用: 自社運営・首都圏。地方・外注運営は別値の可能性あり
```

### ⚠️ 案件固有の数字を全国既定にしない

実績値の多くは **青葉台（目黒区・RC・自社運営・サブリース前提）** で得たもの。
そのまま `area_tiers.tokyo_23` や `usali` の既定にすると、他案件の試算が静かに歪む。
**案件固有の値は `presets:` に切って選択式にする**（既定は保守側に置く）。

### 一次情報の在り処 — 隣の `../minpaku` リポジトリ

民泊チームの実データ（ADR前提・運営コスト実績・融資条件）は**このリポジトリには無い**。
隣の `../minpaku/projects/` にある。**読むのは自由、書き込みは不可**（あちらの正本を壊さない）。

| 何の数字がほしいか | 読む場所 |
|---|---|
| ADR・稼働率の実勢、経費の費目別見直し | `../minpaku/projects/deal-aobadai-ryokangyo/引き継ぎ_2026-09-03_経費見直しとADR前提_v1.md` |
| 運営代行の実コスト率（宮沢さん25物件） | `../minpaku/projects/deal-aobadai-ryokangyo/運営コスト_宮沢実績の反映_2026-09-03_v1.md` |
| 融資条件・K%・フルデット成立線 | `../minpaku/projects/deal-aobadai-ryokangyo/フルデット_金利3%_価格別_イールドギャップ検証_v1.md` |
| どの資料が正本か迷ったら | `../minpaku/projects/引き継ぎ索引.md` |

**前提**: `accel-feasibility` と `minpaku` がフォルダ横並びであること。

---

## §Git の標準動作（AIが自動で回す・毎回の指示は不要）

- **区切りごとに `git commit` → 直後に `git push`**。`.claude/settings.json` で許可済みなので
  **綾部さんが「pushして」と言う必要はない**。
- 基準（`config/*.yaml`）を触ったコミットは、メッセージに**出典を1行**入れる。
  例：`chore(基準): 運営代行を5%→15%へ（宮沢さん実績25物件・2026-09-03）`
- 作業前に `git pull --ff-only`。

> ⚠️ **push はGitHubアカウントの切替が要る。** このリポジトリの持ち主は `ayabe-dev` だが、
> `gh` の既定アクティブは `ayabe-shota-accel`（minpaku 側）。そのまま push すると 403 で弾かれる。
> AIは push 前後で次を自動実行すること（人が指示する必要はない）:
> ```
> gh auth switch --hostname github.com --user ayabe-dev
> git push origin <branch>
> gh auth switch --hostname github.com --user ayabe-shota-accel   # 必ず戻す
> ```

---

## §確認フロー

| アクション | 判断 |
|---|---|
| 調べる・要約する／このリポジトリ内のファイル作成・編集／`git commit` / `git push` | ✅ 確認不要 |
| **基準YAMLの数値変更** | ✅ 確認不要。ただし**出典・適用範囲・変更前後の影響**を報告に書く |
| ファイルの削除・移動・リネーム／外部送信 | ⚠️ 事前確認必須 |
| 対外的に効力が出る一手（社外送付・契約） | 🚫 明示承認が必須 |

### 事業固有の注意
- **ゲストの個人情報・認証情報はコミットしない**（`.env` は gitignore 済み）。
- 法令（旅館業法・住宅宿泊事業法・自治体条例）は**AIの記憶で断定しない**。
  一次情報のURLと時点を必ず添える（`core/evidence.py` の検証ガードが正本）。
