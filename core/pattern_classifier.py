"""調査パターン（A〜D）判定.

3軸：検査済証の有無 × 築年数 × 改修履歴

抽出済み書類とユーザー入力から、A〜D のどれに該当するかを判定する。
さらに「法改正の節目またぎ」フラグも返す。
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from .models import (
    DocumentType,
    ExtractedDocument,
    InvestigationPattern,
    ProjectInput,
)


# 法改正の節目
SEISMIC_1981 = 1981  # 新耐震
WOODEN_2000 = 2000  # 木造2000年基準
STRUCT_CALC_2007 = 2007  # 構造計算厳格化


def classify_pattern(
    project: ProjectInput,
    docs: List[ExtractedDocument],
) -> Tuple[InvestigationPattern, str, List[str]]:
    """調査パターン判定.

    Returns:
        (pattern, reason, milestone_flags)
        milestone_flags: 法改正の節目をまたぐかの注意フラグ（人が読むテキスト）
    """
    has_inspection = _has_inspection_certificate(project, docs)
    built_year = _resolve_built_year(project, docs)
    has_renovation = _resolve_renovation_history(project, docs)
    has_drawings = _has_design_drawings(docs)

    age_years = _age_years(built_year)
    milestones = _milestone_flags(built_year, project.structure)

    # D: 検査済証なし
    if has_inspection is False:
        return (
            InvestigationPattern.D,
            "検査済証なし。国交省ガイドラインに沿った法適合状況調査のフル実施が必要。",
            milestones,
        )

    # 検査済証ありか不明の場合
    if has_inspection is None:
        # 書類で確認できないので C 寄りに保守的判定
        return (
            InvestigationPattern.C,
            "検査済証の有無を書類から確認できませんでした。安全側でパターンCで仮置き。"
            "建築計画概要書または自治体の台帳記載事項証明書で確認してください。",
            milestones,
        )

    # 検査済証あり：築年数 × 改修履歴 で A/B/C
    if age_years is None:
        return (
            InvestigationPattern.B,
            "築年数が不明のため、中間（B）で仮置き。建築年が確定すれば再判定可能。",
            milestones,
        )

    if age_years <= 15 and has_renovation is not True and has_drawings is not False:
        return (
            InvestigationPattern.A,
            f"検査済証あり / 築 {age_years} 年 / 改修履歴なし / 図書整備済 → 軽量フロー。",
            milestones,
        )

    if age_years >= 30 or (has_drawings is False):
        return (
            InvestigationPattern.C,
            f"検査済証あり / 築 {age_years} 年（≥30年）または図書散逸 / 改修履歴も加味 → "
            "実質的にガイドライン調査相当の現況調査が必要。",
            milestones,
        )

    return (
        InvestigationPattern.B,
        f"検査済証あり / 築 {age_years} 年（中間）/ "
        f"{'改修履歴あり' if has_renovation else '改修履歴は不明または軽微'} → "
        "既存図面復元・現地実測・既存不適格項目整理が必要。",
        milestones,
    )


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _has_inspection_certificate(
    project: ProjectInput, docs: List[ExtractedDocument]
) -> Optional[bool]:
    if project.has_inspection_certificate is not None:
        return project.has_inspection_certificate
    for d in docs:
        if d.document_type == DocumentType.INSPECTION_CERTIFICATE:
            return True
    return None


def _resolve_built_year(
    project: ProjectInput, docs: List[ExtractedDocument]
) -> Optional[int]:
    if project.built_year is not None:
        return project.built_year
    for d in docs:
        year = d.extracted_fields.get("built_year")
        if isinstance(year, int) and 1900 <= year <= 2100:
            return year
        # 確認年月日から推測
        conf_date = d.extracted_fields.get("confirmation_date") or d.extracted_fields.get(
            "inspection_date"
        )
        if isinstance(conf_date, str) and len(conf_date) >= 4:
            try:
                return int(conf_date[:4])
            except ValueError:
                pass
    return None


def _resolve_renovation_history(
    project: ProjectInput, docs: List[ExtractedDocument]
) -> Optional[bool]:
    if project.renovation_history is not None:
        return project.renovation_history
    for d in docs:
        text = d.extracted_fields.get("renovation_history")
        if isinstance(text, str) and text.strip():
            # 何らかの記述があれば「あり」と仮判定
            return True
    return None


def _has_design_drawings(docs: List[ExtractedDocument]) -> Optional[bool]:
    for d in docs:
        if d.document_type == DocumentType.DESIGN_DRAWING:
            return True
    return None  # 不明


def _age_years(built_year: Optional[int]) -> Optional[int]:
    if built_year is None:
        return None
    return max(0, date.today().year - built_year)


def _milestone_flags(
    built_year: Optional[int], structure: Optional[str]
) -> List[str]:
    flags: List[str] = []
    if built_year is None:
        return flags
    if built_year < SEISMIC_1981:
        flags.append(
            f"新耐震（1981）以前の建築物（建築 {built_year} 年）→ 構造調査ほぼ必須。"
        )
    if (
        structure
        and "木" in structure
        and built_year < WOODEN_2000
    ):
        flags.append(
            f"木造2000年基準以前（建築 {built_year} 年）→ 接合部・耐力壁配置の検証必要。"
        )
    if (
        structure
        and any(s in structure for s in ["RC", "S", "鉄筋", "鉄骨"])
        and built_year < STRUCT_CALC_2007
    ):
        flags.append(
            f"2007年構造計算厳格化以前の非木造（建築 {built_year} 年）→ 構造計算書の再評価推奨。"
        )
    return flags
