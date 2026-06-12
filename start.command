#!/bin/bash
# 用途変更フィジビリティ判定 MVP - 起動スクリプト
# Mac でダブルクリックして実行してください

set -e

# このスクリプトのあるディレクトリへ移動
cd "$(dirname "$0")"
echo "📂 作業ディレクトリ: $(pwd)"
echo

echo "================================================"
echo "用途変更フィジビリティ判定 Phase 1 MVP — 起動準備"
echo "================================================"
echo

# Python チェック
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 が見つかりません。"
    echo "https://www.python.org/downloads/ からインストールしてください。"
    echo
    read -p "Enter を押すと閉じます..."
    exit 1
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Python ${PYVER} を検出"

# 仮想環境
if [ ! -d ".venv" ]; then
    echo "📦 仮想環境を作成しています..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "✅ 仮想環境を有効化"

# 依存パッケージ
echo "📦 依存パッケージをインストール中（初回は数分かかります）..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅ 依存パッケージ準備完了"

# .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "ℹ️  .env を .env.example からコピーしました"
    echo "⚠️  書類解析機能を使うには .env の ANTHROPIC_API_KEY を編集してください"
    echo "    （未設定でも立地判定・パターン判定は動作します）"
fi

echo
echo "================================================"
echo "🚀 Streamlit を起動します"
echo "   ブラウザが自動で http://localhost:8501 を開きます"
echo "   終了するには Ctrl+C を押してください"
echo "================================================"
echo

streamlit run app.py
