"""朝会1枚をコマンド1本で作る（ブラウザ不要）.

営業担当が Claude に「朝会用の資料を作って」と頼んだとき、AIがこれを叩いて
1枚（Markdown＋HTML）を書き出すための入口。Streamlit を立ち上げなくてよい。

例:
    python3 tools/brief_cli.py \
      --name "高田馬場ハイツ" --address "東京都新宿区高田馬場2-1-1" \
      --zoning 商業地域 --price 16000 --land 132 --land-unit 420 \
      --floor-area 280 --floors 3 --built 1974 --structure RC造 \
      --inspection yes --rooms 8 --source レインズ --presenter 担当者名 \
      --memo "相続で売り急ぎ" --out ../../projects/minpaku-teirei/朝会

注意:
  - **数字を勝手に補完しない**。分からない項目は渡さない（1枚に「未入力」と出る）。
  - `--zoning` を渡さない場合は不動産情報ライブラリAPIで用途地域を取りに行く
    （REINFOLIB_API_KEY が必要）。取れなければ判定は「情報不足」になる。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# systems/feasibility をパスに載せる（どこから叩かれても動くように）
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# APIキー（GEMINI_API_KEY 等）を読む。--zoning-lookup や PDF抽出で必要
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_ROOT / ".env", override=False)
except ImportError:  # python-dotenv が無くても手入力だけなら動く
    pass

from api import gis_client  # noqa: E402
from core import judgment, profitability  # noqa: E402
from core.models import BusinessType, ProjectInput  # noqa: E402
from core.morning_brief import (  # noqa: E402
    generate_morning_brief_html,
    generate_morning_brief_markdown,
    one_line_summary,
    screen,
    talk_track,
)

ZONING_BY_NAME = {
    "第一種低層住居専用地域": "first_low_residential",
    "第二種低層住居専用地域": "second_low_residential",
    "田園住居地域": "rural_residential",
    "第一種中高層住居専用地域": "first_mid_residential",
    "第二種中高層住居専用地域": "second_mid_residential",
    "第一種住居地域": "first_residential",
    "第二種住居地域": "second_residential",
    "準住居地域": "quasi_residential",
    "近隣商業地域": "neighborhood_commercial",
    "商業地域": "commercial",
    "準工業地域": "quasi_industrial",
    "工業地域": "industrial",
    "工業専用地域": "exclusive_industrial",
    "用途地域指定なし": "non_zoned",
}
FIRE_BY_NAME = {
    "防火地域": "fire_district",
    "準防火地域": "quasi_fire_district",
    "指定なし": "no_district",
}
BUSINESS_BY_NAME = {
    "旅館": BusinessType.HOTEL_RYOKAN,
    "ホテル": BusinessType.HOTEL_RYOKAN,
    "旅館・ホテル": BusinessType.HOTEL_RYOKAN,
    "hotel": BusinessType.HOTEL_RYOKAN,
    "簡易宿所": BusinessType.SIMPLE_LODGING,
    "simple": BusinessType.SIMPLE_LODGING,
    "民泊": BusinessType.MINPAKU,
    "minpaku": BusinessType.MINPAKU,
}


def _tri(value: str | None):
    """yes / no / unknown → True / False / None."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("yes", "y", "true", "あり", "有"):
        return True
    if v in ("no", "n", "false", "なし", "無"):
        return False
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="朝会1枚（購入検討リストに載せるかの判定）を作る",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = p.add_argument_group("物件（分かっているものだけ渡す。推測で埋めない）")
    g.add_argument("--address", required=True, help="所在地")
    g.add_argument("--name", help="物件名（無ければ住所を使う）")
    g.add_argument("--business", default="旅館・ホテル", help="業態：旅館・ホテル / 簡易宿所 / 民泊")
    g.add_argument("--zoning", help=f"用途地域名：{'／'.join(list(ZONING_BY_NAME)[:3])} 等")
    g.add_argument("--zoning-lookup", action="store_true",
                   help="用途地域が分からないとき、Geminiに調べさせて『候補』を表示する"
                        "（判定には使わない。人が確認して --zoning で渡す）")
    g.add_argument("--fire", help="防火地域 / 準防火地域 / 指定なし")
    g.add_argument("--far", type=float, help="容積率(%%)")
    g.add_argument("--coverage", type=float, help="建ぺい率(%%)")
    g.add_argument("--floor-area", type=float, help="延床面積(㎡)")
    g.add_argument("--conversion-area", type=float, help="用途変更する部分の床面積(㎡)")
    g.add_argument("--floors", type=int, help="地上階数")
    g.add_argument("--floors-below", type=int, help="地下階数")
    g.add_argument("--built", type=int, help="建築年（西暦）")
    g.add_argument("--structure", help="構造：RC造 / SRC造 / S造 / 木造")
    g.add_argument("--inspection", help="検査済証：yes / no / unknown")
    g.add_argument("--renovation", help="改修履歴：yes / no / unknown")
    g.add_argument("--rooms", type=int, help="想定客室数")
    g.add_argument("--school-nearby", help="学校等が100m以内：yes / no / unknown")

    m = p.add_argument_group("お金（単位：万円）")
    m.add_argument("--price", type=float, help="売出価格（先方希望額）")
    m.add_argument("--land", type=float, help="土地面積(㎡)")
    m.add_argument("--land-unit", type=float, help="土地坪単価(万円/坪)。路線価・成約事例が分かるとき")
    m.add_argument("--room-area", type=float, help="1室面積(㎡)")
    m.add_argument("--adr", type=float, help="ADR(円/泊)。近隣の実勢が分かるとき")
    m.add_argument("--occupancy", type=float, help="稼働率(%%)")
    m.add_argument("--unit", choices=["per_room", "whole"], default="per_room",
                   help="課金モデル：客室ごと / 一棟貸し")
    m.add_argument("--works", type=float, help="初期費用リノベ＋消防許可(万円)。見積があるとき")

    o = p.add_argument_group("朝会のメタ情報")
    o.add_argument("--source", help="出どころ：レインズ / 仲介紹介 / DM反響 / 訪問営業")
    o.add_argument("--presenter", help="説明する人")
    o.add_argument("--memo", help="良いと思った理由（朝会の依頼事項②）")
    o.add_argument("--out", default=".", help="出力先ディレクトリ")
    o.add_argument("--no-html", action="store_true", help="HTMLを出さない")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    business = BUSINESS_BY_NAME.get((args.business or "").strip(), BusinessType.HOTEL_RYOKAN)
    project = ProjectInput(
        address=args.address,
        business_type=business,
        floor_area_m2=args.floor_area,
        conversion_area_m2=args.conversion_area or args.floor_area,
        floors_above=args.floors,
        floors_below=args.floors_below,
        built_year=args.built,
        structure=args.structure,
        has_inspection_certificate=_tri(args.inspection),
        renovation_history=_tri(args.renovation),
        guest_room_count=args.rooms,
    )

    if args.zoning_lookup:
        from core import zoning_lookup

        found = zoning_lookup.lookup(args.address)
        print("=" * 60)
        print("🔍 用途地域の候補（Gemini調べ・**未確認**）")
        if found.get("error"):
            print(f"  {found['error']}")
        else:
            print(f"  用途地域　：{found.get('zoning_name') or '不明'}")
            print(f"  防火地域　：{found.get('fire_district_name') or '不明'}")
            print(f"  容積率　　：{found.get('floor_area_ratio_pct') or '不明'}")
            print(f"  確度　　　：{found.get('confidence')}"
                  f"（公的ドメイン＋原文引用: {'あり' if found.get('verified') else 'なし'}）")
            print(f"  出典　　　：{found.get('source_url') or 'なし'}")
            if found.get("quote"):
                print(f"  引用　　　：{found['quote'][:120]}")
            if found.get("note"):
                print(f"  注記　　　：{found['note']}")
        print("=" * 60)
        if not args.zoning:
            print("⚠️ これは候補です。重要事項説明書・自治体の都市計画情報で確認し、")
            print("   `--zoning <用途地域名>` を付けて実行し直してください。")
            print("   （用途地域は許可可否の根幹なので、候補のままでは判定に使いません）")
            return 3

    manual_geo = None
    if args.zoning:
        code = ZONING_BY_NAME.get(args.zoning.strip())
        if code is None:
            print(f"❌ 用途地域名が不明です：{args.zoning}", file=sys.stderr)
            print(f"   使える名前：{'／'.join(ZONING_BY_NAME)}", file=sys.stderr)
            return 2
        manual_geo = gis_client.from_manual_input(
            address=args.address,
            zoning_code=code,
            zoning_name=args.zoning.strip(),
            fire_district=FIRE_BY_NAME.get((args.fire or "").strip()),
            coverage_ratio_pct=args.coverage,
            floor_area_ratio_pct=args.far,
        )

    report = judgment.run(
        project=project,
        docs=[],
        manual_geo=manual_geo,
        has_nearby_facility=_tri(args.school_nearby),
    )

    overrides = {
        "purchase_price_man": args.price,
        "land_area_m2": args.land,
        "land_price_per_tsubo_man": args.land_unit,
        "room_area_m2": args.room_area,
        "adr_yen": args.adr,
        "occupancy": (args.occupancy / 100.0) if args.occupancy else None,
        "revenue_unit": args.unit,
        "initial_works_man": args.works,
    }
    report.profitability = profitability.compute(
        report, overrides={k: v for k, v in overrides.items() if v is not None}
    )

    meta = {
        "property_name": args.name or "",
        "source": args.source or "",
        "presenter": args.presenter or "",
        "memo": args.memo or "",
    }
    result = screen(report)
    md = generate_morning_brief_markdown(report, meta)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = (args.name or args.address).replace("/", "_").replace(" ", "_")[:40]
    base = f"{date.today().isoformat()}_朝会1枚_{stem}"
    md_path = out_dir / f"{base}.md"
    md_path.write_text(md, encoding="utf-8")
    written = [md_path]
    if not args.no_html:
        html_path = out_dir / f"{base}.html"
        html_path.write_text(generate_morning_brief_html(report, meta), encoding="utf-8")
        written.append(html_path)

    # ── 標準出力：AIがそのまま人に見せる要約 ──────────────────
    print(f"判定：{result['verdict_label']}" + ("　🔥激アツ" if result["is_hot"] else ""))
    for reason in result["reasons"]:
        print(f"  - {reason}")
    print()
    print("1行サマリー：")
    print(f"  {one_line_summary(report, meta)}")
    print()
    print("60秒の台本：")
    for i, line in enumerate(talk_track(report, meta), 1):
        print(f"  {i}. {line}")
    if result["missing_inputs"]:
        print()
        print("⚠️ 未入力（これが空だと答えられない論点がある）：")
        for miss in result["missing_inputs"]:
            print(f"  - {miss['label']}：{miss.get('why', '')}")
    if not meta["memo"]:
        print()
        print("⚠️ 「良いと思った理由」が空欄です。持ってきた人が1〜3行書いてください。")
    print()
    for path in written:
        print(f"✅ {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
