# Streamlit Cloud デプロイ手順（リンク公開 + 自分だけ編集権限）

## 完成イメージ
- 公開URL（例）: `https://accel-feasibility.streamlit.app/`
- 誰でもアクセス可能、24時間稼働、無料
- コード変更は綾部さんの GitHub アカウントだけが可能（git push でデプロイされる）

---

## 0. 前提
- GitHub アカウント（個人 or accel 組織）
- Mac の Terminal で `git` コマンドが使える（初回のみセットアップ）

## 1. GitHub プライベートリポジトリを作る（5分）
1. ブラウザで https://github.com/new を開く
2. 設定：
   - **Repository name**: `accel-feasibility` （任意の名前）
   - **Visibility**: **Private**（推奨。Public でもOK、コードは見える）
   - 「Add a README file」**チェックしない**（既存があるため）
   - 「Add .gitignore」**しない**
3. 「Create repository」をクリック
4. 表示される `git@github.com:...` の URL をコピー

## 2. Mac のローカルから初回 push（5分）
ターミナルで以下を実行：

```bash
cd "/Users/shotaayabe/Library/Application Support/Claude/local-agent-mode-sessions/d57bcb3f-76cf-4223-ae02-b5dbbb8b7628/2f21c220-3b2d-4521-9537-c7b90b7fc511/local_2371d04d-02df-436f-b423-c4e3aa79e108/outputs/phase1-mvp"

# Git 初期化
git init
git add .
git status   # ← .env が表示されないことを必ず確認

# 初回コミット
git commit -m "Initial commit: 用途変更フィジビリティ判定 MVP"
git branch -M main

# GitHub に push（URLは自分のものに置き換え）
git remote add origin git@github.com:あなたのアカウント名/accel-feasibility.git
git push -u origin main
```

> **重要**：`git status` で `.env` が表示されたら絶対に commit しない。`.gitignore` に入っているので普通は出ない。

## 3. Streamlit Cloud にデプロイ（10分）
1. ブラウザで https://share.streamlit.io にアクセス
2. 「Sign in with GitHub」で GitHub アカウントでログイン
3. 「New app」または「Deploy an app」をクリック
4. 設定：
   - **Repository**: `あなたのアカウント名/accel-feasibility`
   - **Branch**: `main`
   - **Main file path**: `app.py`
   - **App URL (optional)**: `accel-feasibility`（好きな名前。これが `https://<ここ>.streamlit.app` になる）
5. 「Advanced settings」→ Secrets に以下を貼り付け（**重要**）

```toml
LLM_PROVIDER = "gemini"
GEMINI_API_KEY = "AQ.Ab8RN6KSvJ8F1n38sRyRlM9e8uUWln0OPhyGx2Vm8WO8b20ODA"
GEMINI_MODEL = "gemini-flash-latest"
REINFOLIB_API_KEY = "9ebecc0fcdc84f40a6192dc8c7ea0750"
DEMO_MODE = "false"
```

6. 「Deploy」をクリック
7. 3〜5分でデプロイ完了。発行URLが表示される。

## 4. 動作確認
発行されたURLを開いて、以下を確認：
- [ ] サイドバーに「Gemini API: ✅」「REINFOLIB: ✅」と表示される
- [ ] 物件住所を入力 → 「フィジビリティ判定」ボタンで判定が走る
- [ ] HTMLレポートがダウンロードできる
- [ ] PDF出力できる（packages.txt の cairo 等が効いている）

## 5. 以後の更新
コードを修正したら、ターミナルで：
```bash
cd "/Users/shotaayabe/.../phase1-mvp"
git add .
git commit -m "変更内容のメモ"
git push
```
→ Streamlit Cloud が自動で再デプロイ（1〜2分）。

## 6. アクセス制限の考え方
- **デフォルトで全世界に公開**。リンクを知る人なら誰でも使える
- 編集は GitHub リポジトリの collaborator だけ可能（綾部さんのみ）
- もし「特定の人だけアクセス可能」にしたい場合は、Streamlit Cloud の有料プラン（Teams）または Streamlit の `st.secrets["password"]` を使った簡易パスワード認証を追加

## トラブルシュート

### `weasyprint` のインストールでエラーが出る
`packages.txt` に `libcairo2` `libpango-1.0-0` 等を入れているので通常はOK。エラーが続く場合は Streamlit Cloud の Manage app → Logs を確認。

### `GEMINI_API_KEY` が読めない
- Secrets を保存後、アプリを「Reboot app」で再起動
- Secrets に `"`（ダブルクォート）が含まれていないか確認

### 「セッションがタイムアウトしました」
無料枠は1〜2時間の無操作でスリープ。再アクセスで起動（10〜20秒）

### app.py の場所が違うと言われる
GitHub リポジトリ直下に `app.py` がある必要あり。`phase1-mvp/app.py` のような階層になっている場合は Main file path に `phase1-mvp/app.py` を指定

---

## 編集権限について（自分だけ修正できる仕組み）

| 操作 | 必要な権限 | 持っている人 |
|-----|----------|------------|
| アプリを使う | なし（公開URL） | 誰でも |
| Secrets を変える | Streamlit Cloud オーナー | 綾部さんのみ |
| コードを変える | GitHub リポジトリ collaborator | 綾部さんのみ |
| 再デプロイ | `git push` 権限 | 綾部さんのみ |

= 「リンクで誰でも使える、変更は自分だけ」が自動で成立

---

最終更新: 2026-06-09
