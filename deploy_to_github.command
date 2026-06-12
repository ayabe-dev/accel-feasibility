#!/bin/bash
# === GitHub への初回 push スクリプト（v2: .git クリーンアップ強化版） ===
# ダブルクリックで起動。

set -e

cd "$(dirname "$0")"
echo "=========================================="
echo "  accel-feasibility → GitHub 初回 push"
echo "  作業ディレクトリ: $(pwd)"
echo "=========================================="
echo ""

# 1. .gitignore に .env があるか確認
if grep -q "^.env$" .gitignore 2>/dev/null; then
  echo "✅ .gitignore に .env が含まれています"
else
  echo "⚠️  .gitignore に .env がない！中断します"
  read -n 1 -s -r -p "Press any key to exit..."
  exit 1
fi

# 2. 以前の .git が壊れている可能性があるので、いったん削除して fresh init
if [ -d .git ]; then
  echo ""
  echo "▶ 既存の .git フォルダを削除して再初期化（サンドボックスの残骸対策）..."
  # sudo を避けるため、Mac の現在ユーザーで操作。書き込めない場合のために chmod も入れる
  chmod -R u+w .git 2>/dev/null || true
  rm -rf .git
fi

echo ""
echo "▶ git init を実行..."
git init
git branch -M main

# 3. 全ファイル add
echo ""
echo "▶ git add ..."
git add .

# 4. ステージ状況確認
echo ""
echo "▶ ステージされたファイル一覧（.env が含まれていないことを確認）:"
git status --short | head -50
echo ""

if git status --short | grep -E '^A\s+\.env$' > /dev/null; then
  echo "❌ 危険：.env がステージされています。中断します。"
  read -n 1 -s -r -p "Press any key to exit..."
  exit 1
fi

# 5. コミット
echo ""
echo "▶ git commit ..."
git commit -m "Initial commit: 用途変更フィジビリティ判定 MVP"

# 6. リモート設定
EXPECTED_REMOTE="https://github.com/ayabe-dev/accel-feasibility.git"
git remote add origin "$EXPECTED_REMOTE"

# 7. push
echo ""
echo "▶ git push -u origin main ..."
echo ""
echo "次に GitHub の認証を聞かれます："
echo "  - Username: ayabe-dev"
echo "  - Password: 先ほどコピーした Personal Access Token を Cmd+V で貼り付け"
echo "  （Password は画面に表示されません。空欄に見えても入力できています）"
echo ""

git push -u origin main

echo ""
echo "=========================================="
echo "  ✅ push 完了！"
echo "  リポジトリ: https://github.com/ayabe-dev/accel-feasibility"
echo "=========================================="
echo ""
echo "次は Chrome で Streamlit Cloud のサインインタブに切り替えて、"
echo "デプロイ作業を続けます。Claude に「push できた」と伝えてください。"
echo ""
read -n 1 -s -r -p "このウィンドウを閉じるには何かキーを押してください..."
