"""民泊・宿泊market dataのアダプタ層.

ADR（平均宿泊単価）と稼働率の「相場」をどこから取るかを差し替え可能にする。

現行は ManualCompsAdapter（近隣類似物件を人が数件入力）のみ。
将来 AirDNA / MetroEngine 等の正規APIに載せ替えるときは、
同じ `summarize()` シグネチャのアダプタを追加して呼び出し側を1行変えるだけで済む。

方針（`TODO_バックログ.md` より）:
  - Airbnb本体のスクレイピングは規約違反のため行わない。
  - 連携は正規API / 分析ツール経由に限定する。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# 統計ユーティリティ（numpy非依存）
# ---------------------------------------------------------------------------


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """線形補間パーセンタイル（q は 0.0〜1.0）."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def _spread(values: Sequence[float], min_q: float = 0.25, max_q: float = 0.75) -> Optional[Dict[str, float]]:
    """min/mid/max を四分位で作る。件数が少ないときは実測の最小・最大に寄せる."""
    vals = sorted(float(v) for v in values if v is not None and float(v) > 0)
    if not vals:
        return None
    mid = _percentile(vals, 0.5)
    if len(vals) <= 2:
        # 件数が少なすぎて分位に意味がないので実測の幅をそのまま使う
        return {"min": vals[0], "mid": mid, "max": vals[-1], "n": len(vals)}
    return {
        "min": _percentile(vals, min_q),
        "mid": mid,
        "max": _percentile(vals, max_q),
        "n": len(vals),
    }


# ---------------------------------------------------------------------------
# アダプタ
# ---------------------------------------------------------------------------


class MarketDataAdapter:
    """相場データ取得の共通インターフェース."""

    #: UI・レポートに出す出典ラベル
    source_label: str = "unknown"

    def summarize(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """ADR・稼働率の min/mid/max を返す。取得できないときは None."""
        raise NotImplementedError


class ManualCompsAdapter(MarketDataAdapter):
    """近隣の類似物件（コンプ）を人が入力する方式.

    comps の各要素（dict）:
        name       … 物件名・メモ（任意）
        adr_yen    … 1泊単価（円）。一棟貸しなら建物1棟/泊、客室ごとなら1室/泊
        occupancy  … 稼働率（0〜1、または 0〜100 で入れても正規化する）

    ADRと稼働率はそれぞれ独立に分位を取る（同一物件で高ADR×高稼働が同時に
    起こる保証はないため、min同士・max同士を組み合わせた保守/強気シナリオを作る）。
    """

    source_label = "近隣コンプ手入力"

    def summarize(self, comps: Optional[List[Dict[str, Any]]] = None, **_: Any) -> Optional[Dict[str, Any]]:
        if not comps:
            return None
        adrs: List[float] = []
        occs: List[float] = []
        used = 0
        for c in comps:
            if not isinstance(c, dict):
                continue
            adr = c.get("adr_yen")
            occ = c.get("occupancy")
            counted = False
            if adr is not None:
                try:
                    a = float(adr)
                except (TypeError, ValueError):
                    a = 0.0
                if a > 0:
                    adrs.append(a)
                    counted = True
            if occ is not None:
                try:
                    o = float(occ)
                except (TypeError, ValueError):
                    o = 0.0
                if o > 1.0:      # 「75」のように%で入力された場合
                    o = o / 100.0
                if 0.0 < o <= 1.0:
                    occs.append(o)
                    counted = True
            if counted:
                used += 1
        if not adrs and not occs:
            return None
        return {
            "source": self.source_label,
            "comp_count": used,
            "adr_yen": _spread(adrs),
            "occupancy": _spread(occs),
            "note": (
                f"近隣類似{used}件の中央値をmid、四分位(25%/75%)をmin/maxとして採用。"
                "件数が2件以下のときは実測の最小・最大をそのまま使用。"
            ),
        }


class AirDNAAdapter(MarketDataAdapter):
    """将来用スタブ（AirDNA等の正規API）.

    実装するときは `summarize(lat=..., lng=..., bedrooms=..., capacity=...)` で
    ManualCompsAdapter と同じ形の dict を返すようにする。
    呼び出し側（core/profitability.py）は形だけ見ているので変更不要。
    """

    source_label = "AirDNA(未実装)"

    def summarize(self, **_: Any) -> Optional[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError(
            "AirDNA連携は未実装です。有料APIの契約後に、ManualCompsAdapter と"
            "同じ形（adr_yen / occupancy の min-mid-max）を返す実装を入れてください。"
        )


#: 現行の既定アダプタ
DEFAULT_ADAPTER = ManualCompsAdapter()


def summarize_comps(comps: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """既定アダプタで相場サマリを取得するショートカット."""
    return DEFAULT_ADAPTER.summarize(comps=comps)
