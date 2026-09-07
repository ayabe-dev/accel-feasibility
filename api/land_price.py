"""地価公示・都道府県地価調査から土地単価を引く（不動産情報ライブラリ XPT002）.

## なぜ必要か

築古の物件は「建物はタダ、土地をいくらで買うか」に収斂する。朝会の判定も
**売出価格 ÷ 土地値** で合否を決める設計にした（`config/screening_rules.yaml`）。
ところが土地単価は `revenue_estimates.yaml` のエリア4区分の代表値
（都心500／23区300／京都280／その他120 万円/坪）しか無く、同じ23区内が一律だった。
＝判定の土台がいちばん粗いという状態だったので、公的な実データを引く。

## 使うAPI

`XPT002 地価公示・都道府県地価調査のポイント`（GeoJSONタイル・z=13〜15）
  - `u_current_years_price_ja` … 当年価格（円/㎡）
  - `standard_lot_number_ja` / `residence_display_name_ja` … 標準地番号・所在
  - `use_category_name_ja` … 用途区分（住宅地・商業地 等）
  - `regulations_use_category_name_ja` … 用途地域（**用途地域の裏取りにも使える**）

## 設計方針

- **失敗しても止めない**。キーが無い・APIが落ちている・点が無い → None を返し、
  呼び出し側はエリア既定値で続行する（判定は動き続ける）。
- **出所を必ず持ち帰る**（標準地番号・年次・距離）。朝会1枚に印字して人が検算できる形にする。
- 近い順に採る。**用途区分が物件の用途地域と噛み合う点を優先**する
  （商業地域の物件に住宅地の標準地を当てると単価がずれる）。
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from .gis_client import REINFOLIB_BASE, _fetch_geojson, haversine_m, lonlat_to_tile

logger = logging.getLogger(__name__)

TSUBO_M2 = 3.30578
API_PATH = "XPT002"
TILE_ZOOM = 15  # 13〜15。15 が最も細かい

# 物件の用途地域 → 相性の良い標準地の用途区分
_USE_CATEGORY_PREFERENCE = {
    "commercial": ("商業地",),
    "neighborhood_commercial": ("商業地", "住宅地"),
    "quasi_industrial": ("工業地", "住宅地"),
    "industrial": ("工業地",),
    "exclusive_industrial": ("工業地",),
}


def _to_float(value: Any) -> Optional[float]:
    """「123,000」「123000円」なども数値にする."""
    if value is None:
        return None
    s = str(value).replace(",", "").replace("円", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def per_m2_to_per_tsubo_man(price_per_m2: float) -> float:
    """円/㎡ → 万円/坪."""
    return price_per_m2 * TSUBO_M2 / 10000.0


def parse_features(
    geojson: Optional[dict], lat: float, lng: float
) -> List[Dict[str, Any]]:
    """XPT002 の GeoJSON を、距離つきの標準地リストにする（純粋関数）."""
    if not geojson:
        return []
    out: List[Dict[str, Any]] = []
    for feat in geojson.get("features") or []:
        props = feat.get("properties") or {}
        price_m2 = _to_float(props.get("u_current_years_price_ja"))
        if not price_m2 or price_m2 <= 0:
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        p_lng, p_lat = float(coords[0]), float(coords[1])
        out.append(
            {
                "price_per_m2": price_m2,
                "per_tsubo_man": round(per_m2_to_per_tsubo_man(price_m2), 1),
                "distance_m": round(haversine_m(lat, lng, p_lat, p_lng)),
                "use_category": props.get("use_category_name_ja") or "",
                "standard_lot": props.get("standard_lot_number_ja") or "",
                "address": (
                    props.get("residence_display_name_ja")
                    or props.get("place_name_ja")
                    or ""
                ),
                "year": props.get("target_year_name_ja") or "",
                "price_type": props.get("land_price_type") or "",
                "zoning": props.get("regulations_use_category_name_ja") or "",
                "nearest_station": props.get("nearest_station_name_ja") or "",
            }
        )
    out.sort(key=lambda p: p["distance_m"])
    return out


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def summarize(
    points: List[Dict[str, Any]],
    zoning_code: Optional[str] = None,
    use_points: int = 3,
    max_distance_m: int = 1500,
) -> Optional[Dict[str, Any]]:
    """近傍の標準地から土地単価（万円/坪）の min/mid/max を作る（純粋関数）.

    - 用途地域と相性の良い用途区分があれば、そちらを優先して採る
    - 採用した標準地は出所として全部返す（人が検算できるように）
    """
    near = [p for p in points if p["distance_m"] <= max_distance_m]
    if not near:
        return None

    preferred = _USE_CATEGORY_PREFERENCE.get(zoning_code or "", ("住宅地",))
    matched = [p for p in near if any(k in p["use_category"] for k in preferred)]
    used = (matched or near)[:use_points]
    if not used:
        return None

    prices = [p["per_tsubo_man"] for p in used]
    mid = _median(prices)
    return {
        "per_tsubo_man": {
            "min": round(min(prices), 1),
            "mid": round(mid, 1),
            "max": round(max(prices), 1),
        },
        "used_points": used,
        "matched_use_category": bool(matched),
        "source": (
            "地価公示・地価調査（不動産情報ライブラリ XPT002）"
            f"／{used[0].get('year') or '年次不明'}"
            f"／標準地{len(used)}点の中央値"
            f"（最寄り {used[0]['distance_m']}m・{used[0].get('standard_lot') or '番号不明'}）"
            + ("" if matched else "／⚠️用途区分が一致する標準地が無く近傍点で代用")
        ),
    }


@lru_cache(maxsize=256)
def _fetch_points_cached(
    lat_r: float, lng_r: float, year: int, api_key: str
) -> Tuple[Dict[str, Any], ...]:
    """タイルを取って標準地リストを返す（座標を丸めてキャッシュ）.

    Streamlit は操作ごとに再実行されるため、キャッシュ無しでは毎回APIを叩いてしまう。
    """
    x, y = lonlat_to_tile(lat_r, lng_r, TILE_ZOOM)
    points: List[Dict[str, Any]] = []
    # 中心タイル → 足りなければ周囲8タイル（標準地はタイル境界の外にあることが多い）
    for dx, dy in [(0, 0)] + [
        (i, j) for i in (-1, 0, 1) for j in (-1, 0, 1) if (i, j) != (0, 0)
    ]:
        geo = _fetch_geojson(
            API_PATH, TILE_ZOOM, x + dx, y + dy, api_key, params={"year": year}
        )
        points.extend(parse_features(geo, lat_r, lng_r))
        if (dx, dy) == (0, 0) and len(points) >= 3:
            break
    points.sort(key=lambda p: p["distance_m"])
    return tuple(points)


def lookup(
    lat: Optional[float],
    lng: Optional[float],
    zoning_code: Optional[str] = None,
    year: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """緯度経度から土地単価（万円/坪）を引く。取れなければ None.

    公示地価は1月1日時点を3月に公表するため、当年で空振りしたら前年を見る。
    """
    api_key = os.getenv("REINFOLIB_API_KEY", "").strip()
    if not api_key or lat is None or lng is None:
        return None

    from datetime import date

    this_year = year or date.today().year
    lat_r, lng_r = round(float(lat), 4), round(float(lng), 4)  # 約11m単位でキャッシュ

    for y in (this_year, this_year - 1):
        try:
            points = list(_fetch_points_cached(lat_r, lng_r, y, api_key))
        except Exception as exc:  # noqa: BLE001 - APIの不調で判定を止めない
            logger.warning(f"地価公示の取得に失敗（エリア既定値で続行）: {exc}")
            return None
        got = summarize(points, zoning_code=zoning_code)
        if got:
            got["queried_year"] = y
            return got
    return None
