# 用途変更フィジビリティ判定システム — Phase 1 MVP

住宅 → 旅館・ホテル営業 用途変更案件の **一次スクリーニング** をするWebアプリ。

## 何ができるか

1. **住所＋業態の入力** → 用途地域・防火地域・距離規制から **GO / 条件付き / NO-GO** を判定
2. **書類アップロード（重説・確認済証・検査済証・登記簿等）** → Claude API で自動抽出し、A〜Dの **調査パターン判定**
3. **概算費用・期間を即時提示**（パターン別レンジ）
4. **不足書類リスト** と **次にやるべきTODO** を自動生成

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
│   └── cost_estimates.yaml  パターン別の費用・期間レンジ
├── core/
│   ├── models.py            Pydantic スキーマ
│   ├── zoning.py            用途地域判定エンジン
│   ├── distance.py          距離規制チェック
│   ├── document_parser.py   Claude API による書類抽出
│   ├── pattern_classifier.py A〜D調査パターン判定
│   ├── estimator.py         概算費用・期間
│   ├── todo_generator.py    TODO・追加書類提案
│   └── judgment.py          総合判定オーケストレータ
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
