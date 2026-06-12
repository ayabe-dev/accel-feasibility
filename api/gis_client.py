"""GIS データ取得クライアント.

優先順位:
  1. ユーザーが手動で用途地域・防火地域を入力 → そのまま使う
  2. 不動産情報ライブラリ API（国交省 XKT002/XKT014）→ ジオコード+タイル取得
  3. デモモード → data/demo_addresses.json
  4. 全部 NG → zoning_code=None で返す（UI 側で手動入力誘導）

実装メモ:
  - 住所→緯度経度: 国土地理院 Geocoder（無料・無認証）
  - 緯度経度→XYZタイル座標: ズーム14
  - XKT002（用途地域）GeoJSONを取得
  - 点-in-多角形判定で該当用途地域を特定
  - XKT014（防火地域）も同様
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.models import GeoLookupResult, NearbyFacility

_DEMO_DATA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "demo_addresses.json"
)

REINFOLIB_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
GSI_GEOCODER = "https://msearch.gsi.go.jp/address-search/AddressSearch"
TILE_ZOOM = 14  # 用途地域・防火地域・地区計画 等
TILE_ZOOM_POI = 13  # 学校・保育園 等のPOI（広めにとって近接施設を漏れなく検索）
NEARBY_DISTANCE_M = 100  # 旅館業法・条例の距離規制（学校等100m以内）


# ----------------------------------------------------------------------
# 用途地域名 → 内部コード のマッピング
# ----------------------------------------------------------------------

ZONING_NAME_TO_CODE: Dict[str, str] = {
    "第一種低層住居専用地域": "first_low_residential",
    "第二種低層住居専用地域": "second_low_residential",
    "第一種中高層住居専用地域": "first_mid_residential",
    "第二種中高層住居専用地域": "second_mid_residential",
    "第一種住居地域": "first_residential",
    "第二種住居地域": "second_residential",
    "準住居地域": "quasi_residential",
    "田園住居地域": "rural_residential",
    "近隣商業地域": "neighborhood_commercial",
    "商業地域": "commercial",
    "準工業地域": "quasi_industrial",
    "工業地域": "industrial",
    "工業専用地域": "exclusive_industrial",
}

FIRE_NAME_TO_CODE: Dict[str, str] = {
    "防火地域": "fire_district",
    "準防火地域": "quasi_fire_district",
}


# ----------------------------------------------------------------------
# デモデータ
# ----------------------------------------------------------------------


def _load_demo_data() -> Dict[str, Any]:
    if not _DEMO_DATA_PATH.exists():
        return {}
    with _DEMO_DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


_DEMO_DATA = _load_demo_data()


def from_demo(address: str) -> Optional[GeoLookupResult]:
    if not _DEMO_DATA:
        return None
    if address in _DEMO_DATA:
        return _build_from_demo(address, _DEMO_DATA[address])
    for key, value in _DEMO_DATA.items():
        if key.startswith(address) or address.startswith(key):
            return _build_from_demo(key, value)
    return None


def _build_from_demo(address: str, value: Dict[str, Any]) -> GeoLookupResult:
    notes = list(value.get("notes", [])) + [
        "※ デモデータです。REINFOLIB API キーを設定し DEMO_MODE=false にして本番に切替えてください。"
    ]
    return GeoLookupResult(
        address=address,
        latitude=value.get("latitude"),
        longitude=value.get("longitude"),
        zoning_code=value.get("zoning_code"),
        zoning_name=value.get("zoning_name"),
        fire_district=value.get("fire_district"),
        coverage_ratio_pct=value.get("coverage_ratio_pct"),
        floor_area_ratio_pct=value.get("floor_area_ratio_pct"),
        source="demo",
        notes=notes,
    )


# ----------------------------------------------------------------------
# 手動入力
# ----------------------------------------------------------------------


def from_manual_input(
    address: str,
    zoning_code: Optional[str],
    fire_district: Optional[str] = None,
    coverage_ratio_pct: Optional[float] = None,
    floor_area_ratio_pct: Optional[float] = None,
    zoning_name: Optional[str] = None,
) -> GeoLookupResult:
    return GeoLookupResult(
        address=address,
        zoning_code=zoning_code,
        zoning_name=zoning_name,
        fire_district=fire_district,
        coverage_ratio_pct=coverage_ratio_pct,
        floor_area_ratio_pct=floor_area_ratio_pct,
        source="manual",
    )


# ----------------------------------------------------------------------
# ジオコーディング（国土地理院 - 無料・無認証）
# ----------------------------------------------------------------------


def geocode(address: str, timeout: float = 10.0) -> Optional[Tuple[float, float]]:
    """住所→(lat, lng). 国土地理院 AddressSearch を使用."""
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(GSI_GEOCODER, params={"q": address})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    if not data:
        return None
    coords = data[0].get("geometry", {}).get("coordinates", [])
    if len(coords) < 2:
        return None
    return float(coords[1]), float(coords[0])  # (lat, lng)


# ----------------------------------------------------------------------
# XYZ タイル座標
# ----------------------------------------------------------------------


def lonlat_to_tile(lat: float, lng: float, z: int = TILE_ZOOM) -> Tuple[int, int]:
    n = 2.0 ** z
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


# ----------------------------------------------------------------------
# 点-多角形判定（ray-casting, 依存なし）
# ----------------------------------------------------------------------


def _point_in_ring(lng: float, lat: float, ring: List[List[float]]) -> bool:
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_intersect = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lng < x_intersect:
                inside = not inside
        j = i
    return inside


def _point_in_polygon(lng: float, lat: float, coords: List[Any]) -> bool:
    """coords: GeoJSON Polygon coordinates [[outer], [hole], ...]."""
    if not coords:
        return False
    if not _point_in_ring(lng, lat, coords[0]):
        return False
    for hole in coords[1:]:
        if _point_in_ring(lng, lat, hole):
            return False
    return True


def find_feature_at(geojson: dict, lat: float, lng: float) -> Optional[dict]:
    if not geojson:
        return None
    for feature in geojson.get("features", []) or []:
        geom = feature.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "Polygon":
            if _point_in_polygon(lng, lat, coords):
                return feature
        elif gtype == "MultiPolygon":
            for polygon in coords:
                if _point_in_polygon(lng, lat, polygon):
                    return feature
    return None


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の距離（メートル）. Haversine 公式."""
    R = 6371000.0  # 地球半径 m
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_nearby_points(
    geojson: dict, lat: float, lng: float, max_distance_m: float
) -> List[Tuple[dict, float]]:
    """Point features の中で max_distance_m 以内のものを (feature, distance) で返す."""
    results: List[Tuple[dict, float]] = []
    if not geojson:
        return results
    for feature in geojson.get("features", []) or []:
        geom = feature.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        feat_lng, feat_lat = coords[0], coords[1]
        d = haversine_m(lat, lng, feat_lat, feat_lng)
        if d <= max_distance_m:
            results.append((feature, d))
    return sorted(results, key=lambda x: x[1])


# ----------------------------------------------------------------------
# REINFOLIB API 呼び出し
# ----------------------------------------------------------------------


def _fetch_geojson(
    api_path: str,
    z: int,
    x: int,
    y: int,
    api_key: str,
    timeout: float = 20.0,
) -> Optional[dict]:
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(
                f"{REINFOLIB_BASE}/{api_path}",
                params={"response_format": "geojson", "z": z, "x": x, "y": y},
                headers={"Ocp-Apim-Subscription-Key": api_key},
            )
            if r.status_code == 404:
                return {"type": "FeatureCollection", "features": []}
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def _parse_pct(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).replace("%", "").replace("％", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def from_reinfolib(address: str) -> Optional[GeoLookupResult]:
    """REINFOLIB の本番API. 失敗時 None."""
    api_key = os.getenv("REINFOLIB_API_KEY", "").strip()
    if not api_key:
        return None

    latlng = geocode(address)
    if latlng is None:
        return GeoLookupResult(
            address=address,
            source="reinfolib",
            notes=["住所→緯度経度の変換に失敗（国土地理院Geocoder）。住所の表記をご確認ください。"],
        )
    lat, lng = latlng
    x, y = lonlat_to_tile(lat, lng, TILE_ZOOM)

    notes: List[str] = []

    # 用途地域
    zoning_geojson = _fetch_geojson("XKT002", TILE_ZOOM, x, y, api_key)
    zoning_feature = find_feature_at(zoning_geojson, lat, lng) if zoning_geojson else None

    zoning_code: Optional[str] = None
    zoning_name: Optional[str] = None
    coverage: Optional[float] = None
    far: Optional[float] = None

    if zoning_feature:
        props = zoning_feature.get("properties", {})
        zoning_name = props.get("use_area_ja")
        zoning_code = ZONING_NAME_TO_CODE.get(zoning_name) if zoning_name else None
        coverage = _parse_pct(props.get("u_building_coverage_ratio_ja"))
        far = _parse_pct(props.get("u_floor_area_ratio_ja"))
        if zoning_name and not zoning_code:
            notes.append(f"用途地域名「{zoning_name}」を内部コードにマップできませんでした。")
    else:
        notes.append(
            "XKT002から該当地点の用途地域が見つかりません。"
            "市街化調整区域・非線引き区域などの可能性があります。"
        )

    # 防火地域 (XKT014)
    fire_geojson = _fetch_geojson("XKT014", TILE_ZOOM, x, y, api_key)
    fire_feature = find_feature_at(fire_geojson, lat, lng) if fire_geojson else None
    fire_district = "no_district"
    if fire_feature:
        props = fire_feature.get("properties", {})
        fire_name = props.get("fire_prevention_ja")
        if fire_name:
            fire_district = FIRE_NAME_TO_CODE.get(fire_name, "no_district")

    # 都市計画区域・区域区分 (XKT001)
    city_planning_classification: Optional[str] = None
    cp_geojson = _fetch_geojson("XKT001", TILE_ZOOM, x, y, api_key)
    cp_feature = find_feature_at(cp_geojson, lat, lng) if cp_geojson else None
    if cp_feature:
        city_planning_classification = cp_feature.get("properties", {}).get(
            "area_classification_ja"
        )

    # 地区計画 (XKT023)
    district_plan_name: Optional[str] = None
    dp_geojson = _fetch_geojson("XKT023", TILE_ZOOM, x, y, api_key)
    dp_feature = find_feature_at(dp_geojson, lat, lng) if dp_geojson else None
    if dp_feature:
        district_plan_name = dp_feature.get("properties", {}).get("plan_name")
        if district_plan_name:
            notes.append(
                f"地区計画「{district_plan_name}」の指定区域内。"
                "用途・容積等の上乗せ規制の可能性があるため、自治体に照会推奨。"
            )

    # 高度利用地区 (XKT024)
    high_use_district_name: Optional[str] = None
    hu_geojson = _fetch_geojson("XKT024", TILE_ZOOM, x, y, api_key)
    hu_feature = find_feature_at(hu_geojson, lat, lng) if hu_geojson else None
    if hu_feature:
        high_use_district_name = hu_feature.get("properties", {}).get("advanced_name")

    # 近接施設 (XKT006 学校 + XKT007 保育園・幼稚園)
    nearby_facilities: List[NearbyFacility] = []
    nearby_facilities.extend(_fetch_nearby_schools(lat, lng, api_key))
    nearby_facilities.extend(_fetch_nearby_preschools(lat, lng, api_key))
    nearby_facilities.sort(key=lambda f: f.distance_m)

    return GeoLookupResult(
        address=address,
        latitude=lat,
        longitude=lng,
        zoning_code=zoning_code,
        zoning_name=zoning_name,
        fire_district=fire_district,
        coverage_ratio_pct=coverage,
        floor_area_ratio_pct=far,
        city_planning_classification=city_planning_classification,
        district_plan_name=district_plan_name,
        high_use_district_name=high_use_district_name,
        nearby_facilities=nearby_facilities,
        source="reinfolib",
        notes=notes,
    )


def _fetch_nearby_schools(
    lat: float, lng: float, api_key: str
) -> List[NearbyFacility]:
    """XKT006 学校を取得し、半径NEARBY_DISTANCE_M内のものを返す."""
    px, py = lonlat_to_tile(lat, lng, TILE_ZOOM_POI)
    gj = _fetch_geojson("XKT006", TILE_ZOOM_POI, px, py, api_key)
    if not gj:
        return []
    results: List[NearbyFacility] = []
    for feature, dist in find_nearby_points(gj, lat, lng, NEARBY_DISTANCE_M):
        props = feature.get("properties", {})
        results.append(
            NearbyFacility(
                name=props.get("P29_004_ja") or "学校",
                facility_type=props.get("P29_003_name_ja") or "学校",
                address=props.get("P29_005_ja") or "",
                distance_m=dist,
            )
        )
    return results


def _fetch_nearby_preschools(
    lat: float, lng: float, api_key: str
) -> List[NearbyFacility]:
    """XKT007 保育園・幼稚園を取得し、半径NEARBY_DISTANCE_M内のものを返す."""
    px, py = lonlat_to_tile(lat, lng, TILE_ZOOM_POI)
    gj = _fetch_geojson("XKT007", TILE_ZOOM_POI, px, py, api_key)
    if not gj:
        return []
    results: List[NearbyFacility] = []
    for feature, dist in find_nearby_points(gj, lat, lng, NEARBY_DISTANCE_M):
        props = feature.get("properties", {})
        name = props.get("preSchoolName_ja") or "保育園・幼稚園"
        # 幼稚園/こども園は schoolClassCode_name_ja, 保育園は無い → 福祉施設で推定
        f_type = props.get("schoolClassCode_name_ja")
        if not f_type:
            f_type = "保育園"
        results.append(
            NearbyFacility(
                name=name,
                facility_type=f_type,
                address=props.get("location_ja") or "",
                distance_m=dist,
            )
        )
    return results


# ----------------------------------------------------------------------
# ルーティング
# ----------------------------------------------------------------------


def lookup(address: str) -> GeoLookupResult:
    """住所→GIS情報. 本番API → デモ → 空, の順に試す."""
    # 1. 本番API（APIキーあれば）
    result = from_reinfolib(address)
    if result is not None and result.zoning_code is not None:
        return result

    # 2. デモモード
    demo_mode = os.getenv("DEMO_MODE", "true").lower() == "true"
    if demo_mode:
        demo_result = from_demo(address)
        if demo_result is not None:
            return demo_result

    # 3. 本番API結果（zoning_code が None でも返す）
    if result is not None:
        return result

    # 4. 空
    return GeoLookupResult(
        address=address,
        source="manual",
        notes=[
            "住所からGIS情報を取得できませんでした。"
            "用途地域・防火地域を手動で選択してください。"
        ],
    )
