# 旅館業許可フィジビリティ判定システム

住宅・既存建物を宿泊施設に転用する案件で、**「この物件で旅館業の許可が取れるか」**を
根拠つきで一次スクリーニングするWebアプリ。

## 何ができるか

1. **旅館業許可の可否判定（主判定）** — 旅館業法3条の不許可事由を起点に、
   許可までに越えるゲートを立地・建物・構造設備・申請者の4区分で判定し、
   **🟢取得可 / 🟡条件付き / 🟠協議次第 / 🔴実質困難 / ⛔不可** を返す
2. **判定の根拠を一次情報で提示** — 各ゲートに法令の**原文引用と出典URL**を添付
3. **別ルートの自動評価** — 旅館・ホテル営業がNGなら、簡易宿所・住宅宿泊事業（民泊）
   で成立するかを同じ物件条件で評価
4. **自治体条例の自動調査** — Claude（web検索つき）が自治体の例規集・審査基準を
   実際に開いて確認し、原文引用と公的ドメインのURLが取れたものだけを判定に使う
5. 書類アップロード（重説・確認済証・検査済証・登記簿等）からの自動抽出、
   A〜Dの調査パターン判定、概算費用・期間、収益性試算、TODO生成
6. **朝会1枚の出力**（「🗣️ 朝会1枚」タブ） — 毎日15分の朝会で
   「購入検討リストに載せるか」を決めるためのA4 1枚。**①相場よりどのぐらい安いか
   ②用途変更の可否と難易度 ③利回り** の3論点に絞り、**🟢載せる / 🟡保留 / 🔴見送り**を
   機械判定して、営業がそのまま読める**60秒の台本**と1行サマリーを付ける。
   判定閾値の正本は `config/screening_rules.yaml`
7. **判定ルールブックの出力**（「📘 評価ルール詳細」タブ／CLI） — いま使っているルールの
   全量を1ファイルに吐き出す。基準をブラッシュアップするときの土台
   ```bash
   python -m core.rulebook -o 判定ルールブック.md
   ```

## 判定ロジックの構成

```
                       判定はコード（決定的・再現可能）
  ┌──────────────────────────────────────────────┐
  │ core/license_gate.py   許可可否のゲート判定（主判定）      │
  │   A 立地   A1用途地域 A2都市計画 A3学校100m A4公衆衛生     │
  │   B 建物   B1確認申請・検査済証 B2法27条 B3避難 B4消防     │
  │   C 構造設備 C1客室面積 C2玄関帳場 C3入浴・便所           │
  │   D 申請者  D1欠格事由                                │
  └──────────────────────────────────────────────┘
        ↑ 根拠                          ↑ 自治体の上乗せ
  ┌──────────────┐          ┌──────────────────────┐
  │ core/evidence.py │          │ core/legal_research.py     │
  │ 全国共通の法令   │          │ Claude + web検索で条例を調査 │
  │ 人手で確認した正本│          │ 検証済みのみ採用・YAMLに蓄積 │
  └──────────────┘          └──────────────────────┘
```

**設計の要点：数値基準と法令判定はコードで決定的に、条例の探索と引用はLLMで。**
LLMに数値判定をさせると誤り、事前にYAMLへ全自治体を書き切るのは非現実的なため、
役割を分けている。

### 検証ガード

調査層の所見が判定に使われるのは、次の**両方**を満たすときだけ：

- 出典に**原文の引用**がある（要約ではなく）
- 出典URLが**公的ドメイン**（`.go.jp` / `.lg.jp` / 自治体の例規集ホスティング）

満たさない所見はレポートに ⚠️未検証 と明示して表示するが、ゲートの判定は動かさない。
個人ブログや解説記事を根拠に許可可否を判断しないための仕組み。

### 自治体条例のキャッシュ

調査結果は `config/municipality_research_cache.yaml` に書き戻され、次回以降は即座に
反映される。条例改正に追随したいときはUIから「キャッシュを無視して再調査」を選ぶ。
手で編集した内容もそのまま使われる。

## セットアップ

```bash
# 1. Python 3.10+ を用意（推奨：3.11）
python3 --version

# 2. 仮想環境
python3 -m venv .venv
source .venv/bin/activate

# 3. 依存パッケージ
pip install -r requirements.txt

# 4. 環境変数
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY を入れる（必須）
# REINFOLIB_API_KEY は後回しでよい（デモモードで動く）

# 5. 起動
streamlit run app.py
```

ブラウザが開いて http://localhost:8501 にアクセスできます。

## ディレクトリ構成

```
phase1-mvp/
├── app.py                    Streamlit エントリポイント
├── requirements.txt
├── .env.example
├── README.md
├── config/
│   ├── zoning_rules.yaml    用途地域 × 業態の可否ルール
│   ├── distance_rules.yaml  距離規制（学校等100m）
│   ├── cost_estimates.yaml  パターン別の費用・期間レンジ
│   ├── revenue_estimates.yaml 収益の前提（ADR・稼働率・cap rate・経費率・融資）
│   └── screening_rules.yaml 朝会スクリーニングの判定閾値（載せる/保留/見送り）
├── core/
│   ├── models.py            Pydantic スキーマ
│   ├── zoning.py            用途地域判定エンジン
│   ├── distance.py          距離規制チェック
│   ├── document_parser.py   Claude API による書類抽出
│   ├── pattern_classifier.py A〜D調査パターン判定
│   ├── estimator.py         概算費用・期間
│   ├── todo_generator.py    TODO・追加書類提案
│   ├── judgment.py          総合判定オーケストレータ
│   ├── morning_brief.py     朝会1枚（3論点＋載せる/保留/見送り＋台本）
│   ├── rulebook.py          判定ルールブック（現状のルール全量を出力）
│   └── md_document.py       Markdown → 印刷用HTML / PDF
├── api/
│   └── gis_client.py        不動産情報ライブラリ API クライアント
└── data/
    └── demo_addresses.json  デモモード用サンプル
```

## API キーの取得

### Anthropic Claude API（必須）
- https://console.anthropic.com/ で取得
- `.env` の `ANTHROPIC_API_KEY` にセット
- 書類解析・OCR代替に使用

### 不動産情報ライブラリ（任意・後でOK）
- 国土交通省 https://www.reinfolib.mlit.go.jp/help/apiManual/
- 申請から数日かかるので、まずはデモモードで動かしてOK
- 取得後 `.env` の `REINFOLIB_API_KEY` にセット、`DEMO_MODE=false` に変更

## 社内チームに公開する（ngrok）

社内3〜5人にデモしたいときは `start-public.command` を使ってください。
パスワード付きの公開URL（https://xxxx.ngrok-free.app）が発行されます。

```bash
# 1. ngrok の無料アカウント作成（30秒）
# https://dashboard.ngrok.com/signup

# 2. authtoken を取得
# https://dashboard.ngrok.com/get-started/your-authtoken

# 3. .env を編集
# NGROK_AUTHTOKEN=ここに貼り付け
# NGROK_BASIC_AUTH=team:好きなパスワード   ← パスワード保護したい場合

# 4. 起動（Finderで start-public.command をダブルクリック でもOK）
./start-public.command
```

ターミナルに次のように表示されます：

```
🎉 公開URL を発行しました
  👉 https://abc123.ngrok-free.app
  🔒 Basic認証:
     username: team
     password: 設定したパスワード
```

このURLをチームに共有すれば、ブラウザから直接アクセスできます。
**Mac がスリープすると URL は無効になる**ため、デモ中はスリープしないよう設定してください。

### 注意事項
- ngrok 無料プランは **再起動するごとに URL が変わる**（固定URLは有料）
- API キー（Claude等）は .env に置かれており、外部には漏れません（コードのみ公開）
- 本格運用するなら Streamlit Community Cloud / Render / Fly.io への移行を検討

## 拡張ロードマップ

このMVPは Phase 1（一次スクリーニング）。今後の拡張：
- **拡張1** Phase 2/3：用途変更確認申請要否の自動判定（既存図面OCR込み）
- **拡張2** Phase 4/5：建基法・消防法のチェックリスト自動化（**ここが価値の中心**）
- **拡張3** Phase 6/7/8：現地調査支援アプリ＋改修見積＋申請図書テンプレ
