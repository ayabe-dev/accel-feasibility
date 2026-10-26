"""用途地域・防火地域の「名称 → 内部コード」の**正本**.

## なぜ1ファイルに集めたか

この表は 2026-09-08 まで **3箇所に複製**されていた
（`api/gis_client.py` / `core/zoning_lookup.py` / `core/judgment.py`）。
そのうちAPI層の表が**漢数字**（`第一種住居地域`）だけを持っていたため、
不動産情報ライブラリ XKT002 が実際に返す**全角数字**（`第１種住居地域`）と
一致せず、**住居系6種すべてが `zoning_code=None`** になっていた。

旅館業・住宅宿泊事業が「建てられない／条件が厳しい」用途地域は住居系に集中している
＝**判定の中核が丸ごと効いていなかった**（実データで確認：第１種〜第２種の低層/中高層/
住居の6種が全滅、商業・工業系6種だけ通っていた）。

同じ事故を繰り返さないため、**表はここだけに置き、表記ゆれはここで吸収する**。
重説・マイソクから抽出した名前（全角で書かれていることがある）も同じ関数を通す。
"""
from __future__ import annotations

from typing import Dict, Optional

# 都市計画法の13区分＋指定なし
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
    "用途地域指定なし": "non_zoned",
}

FIRE_NAME_TO_CODE: Dict[str, str] = {
    "防火地域": "fire_district",
    "準防火地域": "quasi_fire_district",
    "指定なし": "no_district",
    "なし": "no_district",
}

# 「第１種」「第1種」「第Ⅰ種」→「第一種」に寄せる（XKT002は全角数字で返す）
_DIGIT_TO_KANJI = str.maketrans(
    {"１": "一", "２": "二", "1": "一", "2": "二", "Ⅰ": "一", "Ⅱ": "二"}
)

# 部分一致は**必ず長い名前から**見る。
# 「商業地域」は「近隣商業地域」の部分文字列で、短い方から見ると
# 近隣商業地域が商業地域に化ける（＝許可判定が反転しうる）。
# 「工業地域」⊂「準工業地域」も同じ。
_BY_LENGTH_DESC = sorted(ZONING_NAME_TO_CODE, key=len, reverse=True)


def normalize_zoning_name(name: Optional[str]) -> Optional[str]:
    """表記ゆれを吸収した用途地域名を返す（全角数字・空白の違いを消す）."""
    if not name:
        return None
    s = str(name).strip().replace(" ", "").replace("　", "")
    return s.translate(_DIGIT_TO_KANJI) or None


def zoning_code_of(name: Optional[str]) -> Optional[str]:
    """用途地域名 → 内部コード。表記ゆれと「東京都新宿区：商業地域」等の装飾を吸収する."""
    s = normalize_zoning_name(name)
    if not s:
        return None
    code = ZONING_NAME_TO_CODE.get(s)
    if code:
        return code
    for known in _BY_LENGTH_DESC:  # 長い名前から（上のコメント参照）
        if known in s:
            return ZONING_NAME_TO_CODE[known]
    return None


def fire_code_of(name: Optional[str]) -> Optional[str]:
    """防火地域名 → 内部コード。取れなければ None（呼び出し側が既定を決める）."""
    if not name:
        return None
    s = str(name).strip().replace(" ", "").replace("　", "")
    if not s:
        return None
    if s in FIRE_NAME_TO_CODE:
        return FIRE_NAME_TO_CODE[s]
    for known in sorted(FIRE_NAME_TO_CODE, key=len, reverse=True):
        if known in s:
            return FIRE_NAME_TO_CODE[known]
    return None
