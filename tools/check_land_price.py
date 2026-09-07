"""住所を入れると、近傍の地価公示・地価調査の標準地を並べて見せる（検算用）.

土地値が朝会判定の土台なので、「この単価はどこから来たのか」を人が確かめられるようにする。

    python3 tools/check_land_price.py --address "東京都新宿区高田馬場2-1-1"
    python3 tools/check_land_price.py --address "..." --zoning 商業地域 --year 2026

REINFOLIB_API_KEY（不動産情報ライブラリ）が必要。未設定なら何が足りないかを表示する。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_ROOT / ".env", override=False)
except ImportError:
    pass

from api import gis_client, land_price  # noqa: E402
from core.zoning_lookup import ZONING_NAMES  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="近傍の地価公示ポイントを見る（土地単価の検算）")
    ap.add_argument("--address", required=True, help="物件所在地")
    ap.add_argument("--zoning", help="用途地域名（商業地域 等）。用途区分の優先に使う")
    ap.add_argument("--year", type=int, help="価格時点の年（既定は当年→前年）")
    ap.add_argument("--all", action="store_true", help="採用点だけでなく取得した全点を出す")
    args = ap.parse_args(argv)

    if not os.getenv("REINFOLIB_API_KEY", "").strip():
        print("❌ REINFOLIB_API_KEY が未設定です（.env に入れてください）", file=sys.stderr)
        print("   取得: https://www.reinfolib.mlit.go.jp/help/apiManual/", file=sys.stderr)
        return 2

    latlng = gis_client.geocode(args.address)
    if latlng is None:
        print(f"❌ 住所から緯度経度が取れませんでした：{args.address}", file=sys.stderr)
        return 3
    lat, lng = latlng
    print(f"📍 {args.address} → 緯度経度 {lat:.5f}, {lng:.5f}")

    zoning_code = ZONING_NAMES.get((args.zoning or "").strip()) if args.zoning else None
    if args.zoning and zoning_code is None:
        print(f"⚠️ 用途地域名が不明：{args.zoning}（用途区分の優先は無効）", file=sys.stderr)

    got = land_price.lookup(lat, lng, zoning_code=zoning_code, year=args.year)
    if not got:
        print("⚠️ 近傍（1.5km以内）に標準地が見つかりませんでした")
        print("   → 試算はエリア既定の代表値で動きます。実勢が分かるなら手入力してください")
        return 1

    rng = got["per_tsubo_man"]
    print()
    print(f"💰 土地坪単価：**{rng['mid']:,.1f} 万円/坪**（{rng['min']:,.1f}〜{rng['max']:,.1f}）")
    print(f"   出所：{got['source']}")
    if not got.get("matched_use_category"):
        print("   ⚠️ 用途区分が一致する標準地が無く、近傍点で代用しています")
    print()
    print("採用した標準地")
    print(f"  {'標準地':<14}{'距離':>7}  {'用途区分':<8}{'円/㎡':>12}{'万円/坪':>10}  所在")
    for p in got["used_points"]:
        print(
            f"  {p.get('standard_lot', '—'):<14}{p['distance_m']:>6}m  "
            f"{p.get('use_category', '—'):<8}{p['price_per_m2']:>12,.0f}"
            f"{p['per_tsubo_man']:>10,.1f}  {p.get('address', '')}"
        )
    print()
    print("※ 標準地は「その地点」の価格。間口・接道・形状・角地で実勢は上下します。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
