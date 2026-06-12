"""Streamlit + ngrok でアプリを公開する起動スクリプト.

使い方:
  1. .env に NGROK_AUTHTOKEN をセット（必須）
  2. オプション：NGROK_BASIC_AUTH=user:password でパスワード保護
  3. python start_public.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env")


def main() -> int:
    authtoken = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if not authtoken:
        print("=" * 60)
        print("❌ NGROK_AUTHTOKEN が .env に設定されていません")
        print("=" * 60)
        print()
        print("セットアップ手順:")
        print("  1. https://dashboard.ngrok.com/signup")
        print("     で無料アカウント作成（30秒）")
        print("  2. https://dashboard.ngrok.com/get-started/your-authtoken")
        print("     でトークンをコピー")
        print("  3. .env を編集して NGROK_AUTHTOKEN=コピーしたトークン と記入")
        print("  4. このスクリプトを再実行")
        print()
        return 1

    basic_auth_raw = os.getenv("NGROK_BASIC_AUTH", "").strip()
    custom_domain = os.getenv("NGROK_DOMAIN", "").strip()

    # pyngrok を遅延importしてエラーメッセージを優先
    try:
        from pyngrok import conf, ngrok
    except ImportError:
        print("⚠️ pyngrok がインストールされていません。")
        print("   pip install pyngrok を実行してください。")
        return 1

    conf.get_default().auth_token = authtoken

    # 1. Streamlit をバックグラウンド起動
    print("📦 Streamlit を起動中...")
    streamlit_proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "app.py",
            "--server.headless=true",
            "--server.port=8501",
            "--browser.gatherUsageStats=false",
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ},
    )

    # Streamlitの起動を待つ
    print("⏳ サーバ起動を待っています...")
    time.sleep(5)

    # 2. ngrok トンネル開設
    print("🌐 ngrokトンネルを開いています...")
    tunnel_kwargs = {}
    if basic_auth_raw:
        tunnel_kwargs["auth"] = basic_auth_raw  # ngrok v3: "user:password"
    if custom_domain:
        tunnel_kwargs["domain"] = custom_domain  # 固定ドメイン
        print(f"   固定ドメインを使用: {custom_domain}")

    try:
        tunnel = ngrok.connect(8501, "http", **tunnel_kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ngrok接続失敗: {exc}")
        streamlit_proc.terminate()
        return 1

    public_url = tunnel.public_url
    https_url = public_url.replace("http://", "https://")

    print()
    print("=" * 60)
    print("🎉 公開URL を発行しました")
    print("=" * 60)
    print(f"  👉 {https_url}")
    if basic_auth_raw and ":" in basic_auth_raw:
        user, _, pw = basic_auth_raw.partition(":")
        print(f"  🔒 Basic認証:")
        print(f"     username: {user}")
        print(f"     password: {pw}")
    else:
        print("  ⚠️ Basic認証なし。URLを知っている人は誰でもアクセス可能。")
        print("     .env の NGROK_BASIC_AUTH=user:pass で保護できます。")
    print()
    print("チームメンバーに上記URLを共有してください。")
    print("Ctrl+C で終了します。")
    print("=" * 60)
    print()

    # Ctrl+C ハンドリング
    def shutdown(signum=None, frame=None):
        print("\n🛑 終了処理中...")
        try:
            ngrok.disconnect(public_url)
        except Exception:  # noqa: BLE001
            pass
        try:
            ngrok.kill()
        except Exception:  # noqa: BLE001
            pass
        streamlit_proc.terminate()
        try:
            streamlit_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            streamlit_proc.kill()
        print("✅ 停止しました")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Streamlitの終了を待つ
    try:
        streamlit_proc.wait()
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
