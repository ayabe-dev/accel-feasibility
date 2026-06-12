#!/bin/bash
# 用途変更フィジビリティ判定 MVP - 公開モード起動スクリプト
# ngrok で社内チームに公開URLを発行します。
# 事前に .env の NGROK_AUTHTOKEN をセットしてください。

set -e

cd "$(dirname "$0")"
echo "📂 作業ディレクトリ: $(pwd)"
echo

# Python チェック
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 が見つかりません。"
    read -p "Enter を押すと閉じます..."
    exit 1
fi

# 仮想環境
if [ ! -d ".venv" ]; then
    echo "📦 仮想環境を作成しています..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "✅ 仮想環境を有効化"

# 依存（pyngrokを含む）
echo "📦 依存パッケージを確認中..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅ 依存パッケージ準備完了"

# .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo
    echo "⚠️  .env が作成されました。NGROK_AUTHTOKEN を編集してから再実行してください。"
    echo "    1. https://dashboard.ngrok.com/signup でサインアップ"
    echo "    2. https://dashboard.ngrok.com/get-started/your-authtoken でトークン取得"
    echo "    3. .env を開いて NGROK_AUTHTOKEN=xxxxx の形で記入"
    echo
    open .env
    read -p "編集が終わったら Enter を押してください..."
fi

# NGROK_AUTHTOKEN チェック
if ! grep -q "^NGROK_AUTHTOKEN=." .env; then
    echo
    echo "⚠️  .env の NGROK_AUTHTOKEN が空です。先に設定してください。"
    open .env
    read -p "編集が終わったら Enter を押してください..."
fi

echo
echo "================================================"
echo "🚀 Streamlit + ngrok を起動します"
echo "   公開URLがこのターミナルに表示されます"
echo "   終了するには Ctrl+C を押してください"
echo "================================================"
echo

python start_public.py
