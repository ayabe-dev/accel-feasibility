"""Phase 4：建基法技術基準のチェックリストエンジン.

入力：ProjectInput + GeoLookupResult
出力：BuildingCodeCheckItem のリスト

各規定について「該当・適合・要確認・非該当」を判定し、改修要否の見通しを返す。
詳細な現況の判定は図面情報が必要だが、本MVPは規模・構造から「該当の有無」と
「現況での対応方針」を提示するレベル。
"""
from __future__ import annotations

from typing import List, Optional

from .models import (
    BuildingCodeCheckItem,
    CheckStatus,
    GeoLookupResult,
    ProjectInput,
    RecommendationOption,
    RecommendationStep,
)


# ----------------------------------------------------------------------
# ヘルパー
# ----------------------------------------------------------------------


def _is_wooden(structure: Optional[str]) -> bool:
    return structure is not None and "木" in structure


def _is_fireproof_struct(structure: Optional[str]) -> bool:
    if structure is None:
        return False
    return any(s in structure for s in ["RC", "SRC", "鉄筋コンクリート", "鉄骨鉄筋"])


def _is_semi_fireproof_struct(structure: Optional[str]) -> bool:
    """準耐火相当の蓋然性が高い構造."""
    if structure is None:
        return False
    return "鉄骨" in structure or "S造" in structure


# ----------------------------------------------------------------------
# 各規定の判定
# ----------------------------------------------------------------------


def _check_law_27(project: ProjectInput) -> BuildingCodeCheckItem:
    """法27条 特殊建築物の耐火要件."""
    floors_above = project.floors_above or 0
    floor_area = project.floor_area_m2 or 0
    structure = project.structure
    is_fp = _is_fireproof_struct(structure)

    if floors_above >= 3:
        if is_fp:
            return BuildingCodeCheckItem(
                rule_id="law_27",
                rule_name="法27条：特殊建築物の耐火要件",
                article="建築基準法27条",
                status=CheckStatus.COMPLIANT,
                requirement="3階以上の階を旅館用途とする場合、耐火建築物等であること",
                current=f"{floors_above}階建 / 構造：{structure}（耐火相当）",
                recommended_action="現況の耐火性能を竣工図書で再確認",
                impact="high",
            )
        return BuildingCodeCheckItem(
            rule_id="law_27",
            rule_name="法27条：特殊建築物の耐火要件",
            article="建築基準法27条",
            status=CheckStatus.NON_COMPLIANT,
            requirement="3階以上の旅館は耐火建築物等が必要",
            current=f"{floors_above}階建 / 構造：{structure or '不明'}（非耐火）",
            recommended_action="耐火被覆/耐火構造への補強、または規模見直し（3階以下の旅館部分に減築）",
            impact="high",
            options=_options_for_law_27_non_compliant(project),
        )

    if floors_above == 2:
        if floor_area >= 600:
            return BuildingCodeCheckItem(
                rule_id="law_27",
                rule_name="法27条：特殊建築物の耐火要件",
                article="建築基準法27条",
                status=CheckStatus.NEEDS_REVIEW,
                requirement="2階の旅館部分600㎡以上：耐火建築物 または 1時間準耐火建築物等",
                current=f"2階建 / 延床{floor_area:,.0f}㎡（2階旅館面積の精査要）",
                recommended_action="2階旅館部分の床面積を精査の上、耐火性能確認",
                impact="high",
            )
        if floor_area >= 300:
            return BuildingCodeCheckItem(
                rule_id="law_27",
                rule_name="法27条：特殊建築物の耐火要件",
                article="建築基準法27条",
                status=CheckStatus.NEEDS_REVIEW,
                requirement="2階の旅館部分300㎡以上：耐火建築物等が必要",
                current=f"2階建 / 延床{floor_area:,.0f}㎡",
                recommended_action="2階旅館部分の面積と現況耐火性能を確認",
                impact="high",
            )
    return BuildingCodeCheckItem(
        rule_id="law_27",
        rule_name="法27条：特殊建築物の耐火要件",
        article="建築基準法27条",
        status=CheckStatus.NOT_APPLICABLE,
        requirement="法27条本則の規模に該当せず",
        current=f"{floors_above}階建 / 延床{floor_area:,.0f}㎡",
        recommended_action="ただし令21条等の規模規定や個別告示は別途確認",
        impact="low",
    )


def _check_ord_121(project: ProjectInput) -> BuildingCodeCheckItem:
    """令121条 二方向避難（直通階段2以上）."""
    floors_above = project.floors_above or 0
    floor_area = project.floor_area_m2 or 0
    structure = project.structure
    is_fp = _is_fireproof_struct(structure) or _is_semi_fireproof_struct(structure)
    threshold = 200 if is_fp else 100

    if floors_above < 2:
        return BuildingCodeCheckItem(
            rule_id="ord_121",
            rule_name="令121条：二方向避難",
            article="建築基準法施行令121条",
            status=CheckStatus.NOT_APPLICABLE,
            requirement="避難階以外の階に宿泊室がある場合に適用",
            current=f"地上{floors_above}階建のため、避難階のみで該当しない可能性",
            recommended_action="平屋なら本条非該当。複数階なら平面図で再確認",
            impact="low",
        )

    # 概算：1階あたりの宿泊室床面積を全床面積の50%と仮定して判定
    est_per_floor = floor_area / max(floors_above, 1) * 0.5

    if est_per_floor > threshold:
        return BuildingCodeCheckItem(
            rule_id="ord_121",
            rule_name="令121条：二方向避難",
            article="建築基準法施行令121条",
            status=CheckStatus.NEEDS_REVIEW,
            requirement=(
                f"避難階以外の各階の宿泊室合計が{threshold}㎡超 → 2以上の直通階段が必要"
            ),
            current=(
                f"地上{floors_above}階 / 延床{floor_area:,.0f}㎡ "
                f"（推定階別宿泊室面積 ~{est_per_floor:,.0f}㎡）"
            ),
            recommended_action="平面図で宿泊室の階別床面積を確認、直通階段が2以上あるか実測",
            impact="high",
            options=_options_for_ord_121(project, threshold),
        )
    return BuildingCodeCheckItem(
        rule_id="ord_121",
        rule_name="令121条：二方向避難",
        article="建築基準法施行令121条",
        status=CheckStatus.NEEDS_REVIEW,
        requirement=f"宿泊室合計{threshold}㎡以下なら1階段でも可",
        current=f"地上{floors_above}階 / 延床{floor_area:,.0f}㎡",
        recommended_action="平面図で宿泊室の階別床面積を確認",
        impact="medium",
    )


def _check_ord_119(project: ProjectInput) -> BuildingCodeCheckItem:
    """令119条 廊下幅."""
    return BuildingCodeCheckItem(
        rule_id="ord_119",
        rule_name="令119条：廊下幅員",
        article="建築基準法施行令119条",
        status=CheckStatus.NEEDS_REVIEW,
        requirement="旅館客室部分の廊下：両側居室 1.6m以上、片側居室 1.2m以上",
        current="現況図面・実測で確認要",
        recommended_action="平面図と現地実測で各階廊下の有効幅員を確認",
        impact="medium",
    )


def _check_ord_120(project: ProjectInput) -> BuildingCodeCheckItem:
    """令120条 直通階段までの歩行距離."""
    structure = project.structure
    is_fp = _is_fireproof_struct(structure) or _is_semi_fireproof_struct(structure)
    limit = 50 if is_fp else 30
    return BuildingCodeCheckItem(
        rule_id="ord_120",
        rule_name="令120条：直通階段までの歩行距離",
        article="建築基準法施行令120条",
        status=CheckStatus.NEEDS_REVIEW,
        requirement=f"歩行距離 {limit}m以下（{'準耐火・耐火相当' if is_fp else '非耐火'}）",
        current=f"構造：{structure or '不明'}",
        recommended_action="平面図上で最遠居室から直通階段までの経路距離を測定",
        impact="medium",
    )


def _check_ord_126_2(project: ProjectInput) -> BuildingCodeCheckItem:
    """令126条の2 排煙設備."""
    floor_area = project.floor_area_m2 or 0
    if floor_area > 500:
        return BuildingCodeCheckItem(
            rule_id="ord_126_2",
            rule_name="令126条の2：排煙設備",
            article="建築基準法施行令126条の2",
            status=CheckStatus.NEEDS_REVIEW,
            requirement="延床500㎡超 → 排煙設備設置（緩和規定の適用検討可）",
            current=f"延床{floor_area:,.0f}㎡",
            recommended_action="既存窓の開口面積、機械排煙の有無を確認。検証法での緩和も検討",
            impact="medium",
        )
    return BuildingCodeCheckItem(
        rule_id="ord_126_2",
        rule_name="令126条の2：排煙設備",
        article="建築基準法施行令126条の2",
        status=CheckStatus.NEEDS_REVIEW,
        requirement="500㎡以下でも排煙無窓居室等は対象",
        current=f"延床{floor_area:,.0f}㎡",
        recommended_action="客室・廊下の排煙有効開口を平面図で確認",
        impact="low",
    )


def _check_ord_126_4(project: ProjectInput) -> BuildingCodeCheckItem:
    """令126条の4 非常用照明."""
    return BuildingCodeCheckItem(
        rule_id="ord_126_4",
        rule_name="令126条の4：非常用照明装置",
        article="建築基準法施行令126条の4",
        status=CheckStatus.NEEDS_REVIEW,
        requirement="旅館の居室・避難経路（廊下・階段等）に非常用照明",
        current="既存照明設備の確認要",
        recommended_action="既設照明の停電時点灯機能を確認、不足分は追加設置",
        impact="low",
    )


def _check_ord_128_4(project: ProjectInput) -> BuildingCodeCheckItem:
    """令128条の4・5 内装制限."""
    floor_area = project.floor_area_m2 or 0
    if floor_area >= 200:
        return BuildingCodeCheckItem(
            rule_id="ord_128_4",
            rule_name="令128条の4・5：内装制限",
            article="建築基準法施行令128条の4・5",
            status=CheckStatus.NEEDS_REVIEW,
            requirement="旅館の200㎡以上の階の居室部分は壁・天井を難燃材料以上に",
            current=f"延床{floor_area:,.0f}㎡（200㎡以上）",
            recommended_action="既存内装の仕様確認、改修時は難燃以上の仕上げに",
            impact="medium",
        )
    return BuildingCodeCheckItem(
        rule_id="ord_128_4",
        rule_name="令128条の4・5：内装制限",
        article="建築基準法施行令128条の4・5",
        status=CheckStatus.NOT_APPLICABLE,
        requirement="200㎡未満は規模規定の対象外",
        current=f"延床{floor_area:,.0f}㎡",
        recommended_action="ただし火気使用室や3階以上等の個別規定は別途確認",
        impact="low",
    )


def _check_law_28(project: ProjectInput) -> BuildingCodeCheckItem:
    """法28条 採光・換気."""
    return BuildingCodeCheckItem(
        rule_id="law_28",
        rule_name="法28条：居室の採光・換気",
        article="建築基準法28条",
        status=CheckStatus.NEEDS_REVIEW,
        requirement="旅館客室：床面積の1/10以上の有効採光、1/20以上の有効換気",
        current="既存窓の有効採光面積の確認要",
        recommended_action="平面図で各客室の窓面積と床面積の比率を計算",
        impact="medium",
    )


def _check_ord_112(project: ProjectInput) -> BuildingCodeCheckItem:
    """令112条 防火区画."""
    floors_above = project.floors_above or 0
    if floors_above >= 2:
        return BuildingCodeCheckItem(
            rule_id="ord_112",
            rule_name="令112条：防火区画（異種用途/竪穴区画）",
            article="建築基準法施行令112条",
            status=CheckStatus.NEEDS_REVIEW,
            requirement="階段・吹抜・EVシャフトの竪穴区画、旅館部分と他用途間の異種用途区画",
            current=f"地上{floors_above}階",
            recommended_action="既存階段室の区画状況、他用途併存時の区画壁・防火戸を確認",
            impact="high",
        )
    return BuildingCodeCheckItem(
        rule_id="ord_112",
        rule_name="令112条：防火区画（異種用途/竪穴区画）",
        article="建築基準法施行令112条",
        status=CheckStatus.NOT_APPLICABLE,
        requirement="複数階の竪穴区画が主たる対象",
        current="平屋",
        recommended_action="他用途併存時の異種用途区画は別途確認",
        impact="low",
    )


def _check_law_20(project: ProjectInput) -> BuildingCodeCheckItem:
    """法20条 構造耐力（積載荷重の変更）."""
    return BuildingCodeCheckItem(
        rule_id="law_20",
        rule_name="法20条：構造耐力（積載荷重の変動）",
        article="建築基準法20条",
        status=CheckStatus.NEEDS_REVIEW,
        requirement="旅館の積載荷重（1,800N/㎡相当）で構造再評価",
        current=f"構造：{project.structure or '不明'}",
        recommended_action="既存構造計算書と旅館用途荷重で差分検証。不足あれば補強",
        impact="high",
    )


# ----------------------------------------------------------------------
# 公開関数
# ----------------------------------------------------------------------


def run_building_code_checks(
    project: ProjectInput, geo: GeoLookupResult
) -> List[BuildingCodeCheckItem]:
    """全規定のチェックを実行."""
    return [
        _check_law_27(project),
        _check_ord_121(project),
        _check_ord_119(project),
        _check_ord_120(project),
        _check_ord_126_2(project),
        _check_ord_126_4(project),
        _check_ord_128_4(project),
        _check_law_28(project),
        _check_ord_112(project),
        _check_law_20(project),
    ]


# ----------------------------------------------------------------------
# 対応オプションジェネレータ（不適合・要確認時）
# ----------------------------------------------------------------------


def _options_for_law_27_non_compliant(project: ProjectInput) -> List[RecommendationOption]:
    """法27条 不適合時の対応案を生成（耐火被覆 / 減築 / 用途見直し）."""
    fa = project.floor_area_m2 or 300
    structure = project.structure or "木造"
    floors_above = project.floors_above or 3

    # 耐火被覆コスト概算：㎡あたり3〜8万円（S造想定）、木造はもっと高くなる
    is_wooden = "木" in structure
    is_steel = "鉄骨" in structure or "S造" in structure

    if is_wooden:
        cover_unit_min, cover_unit_max = 8, 18  # 木造は被覆難しく高い
    elif is_steel:
        cover_unit_min, cover_unit_max = 3, 8
    else:
        cover_unit_min, cover_unit_max = 5, 12

    options: List[RecommendationOption] = []

    # 案1：耐火被覆/補強で適合化
    options.append(
        RecommendationOption(
            title="案1：既存構造を残し、耐火被覆・補強で適合化",
            summary=(
                "既存の主要構造部に耐火被覆を追加して耐火建築物（または準耐火建築物等）として認定を取る。"
                "建物全体の使い方を変えずに済むため、計画変更が最小。"
            ),
            cost_min_man=int(fa * cover_unit_min),
            cost_max_man=int(fa * cover_unit_max),
            duration_min_months=3,
            duration_max_months=7,
            pros=[
                "現状の間取り・客室数を維持できる",
                "営業フロアを減らさないので売上計画への影響なし",
                "確認申請の構造変更のみ（用途変更とは独立）",
            ],
            cons=[
                "耐火被覆で天井高が下がるケースあり",
                "意匠的な制約（鉄骨現しなどはできなくなる）",
                "工期が中〜長め",
            ],
            steps=[
                RecommendationStep(
                    title="① 構造設計事務所に現況調査依頼",
                    description="既存図書と現地調査で構造種別・耐火性能を確定。",
                    days=14,
                    cost_min_man=20,
                    cost_max_man=60,
                    owner="構造設計事務所",
                ),
                RecommendationStep(
                    title="② 耐火認定取得済み仕様の選定",
                    description="石膏ボード被覆・耐火塗料・繊維巻きなど、認定番号付き仕様を選定。",
                    days=7,
                    cost_min_man=0,
                    cost_max_man=0,
                    owner="構造設計事務所＋一級建築士",
                ),
                RecommendationStep(
                    title="③ 施工業者見積もり3社競合",
                    description="耐火被覆の専門業者から相見積取得。",
                    days=14,
                    cost_min_man=0,
                    cost_max_man=0,
                    owner="一級建築士・発注者",
                ),
                RecommendationStep(
                    title="④ 確認申請（用途変更）の構造変更を反映",
                    description="進行中の用途変更確認申請に耐火補強の図面を追加。",
                    days=30,
                    cost_min_man=30,
                    cost_max_man=80,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="⑤ 耐火被覆工事の実施",
                    description="石膏ボード等の被覆工事。並行して他改修も。",
                    days=60,
                    cost_min_man=int(fa * cover_unit_min) - 50,
                    cost_max_man=int(fa * cover_unit_max) - 80,
                    owner="施工業者（耐火被覆専門）",
                ),
                RecommendationStep(
                    title="⑥ 完了検査・耐火認定写真の提出",
                    description="施工写真を確認検査機関に提出して認定確認。",
                    days=14,
                    cost_min_man=0,
                    cost_max_man=10,
                    owner="一級建築士事務所",
                ),
            ],
            contact="構造設計事務所、耐火被覆専門業者、確認検査機関",
        )
    )

    # 案2：減築（3階以下の旅館部分に縮小）
    if floors_above >= 4:
        reduced_fa = fa * 0.75
    else:
        reduced_fa = fa * 0.85
    options.append(
        RecommendationOption(
            title=f"案2：減築（{floors_above}階→3階以下に旅館部分を集約）",
            summary=(
                "3階以上の旅館用途を取りやめ、3階以下に客室を集約。"
                "上階は事務所・倉庫等の非旅館用途に変更するか、解体。"
                "法27条本則の対象から外れるため、耐火要件を回避できる。"
            ),
            cost_min_man=500,
            cost_max_man=2000,
            duration_min_months=5,
            duration_max_months=10,
            pros=[
                "耐火被覆コストを丸ごと回避",
                "営業面積は減るが、稼働率次第で利益確保可",
                "解体で建物重量が減り、構造上も有利になる場合あり",
            ],
            cons=[
                "客室数が減り、売上のキャップが下がる",
                "上階の用途設定が難しい（収益化しづらい）",
                "解体工事費＋廃材処分費が発生",
            ],
            steps=[
                RecommendationStep(
                    title="① 減築範囲の検討（基本設計）",
                    description=f"延床{fa:.0f}㎡→{reduced_fa:.0f}㎡（想定）に縮小、客室配置の見直し。",
                    days=14,
                    cost_min_man=30,
                    cost_max_man=80,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="② 構造影響評価（残存部の構造耐力）",
                    description="上階を解体した後の残存構造の安全性を構造設計者が確認。",
                    days=14,
                    cost_min_man=20,
                    cost_max_man=60,
                    owner="構造設計事務所",
                ),
                RecommendationStep(
                    title="③ 確認申請（用途変更＋減築）",
                    description="減築は規模10㎡超で確認申請対象。用途変更と一括申請。",
                    days=60,
                    cost_min_man=100,
                    cost_max_man=300,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="④ 解体工事",
                    description="養生・足場・解体・廃材処分。アスベスト含有材は別途。",
                    days=45,
                    cost_min_man=200,
                    cost_max_man=800,
                    owner="解体工事業者",
                ),
                RecommendationStep(
                    title="⑤ 残存部の防水・外装仕上げ",
                    description="切り取った接合部の止水・外装復旧。",
                    days=30,
                    cost_min_man=100,
                    cost_max_man=400,
                    owner="施工業者",
                ),
                RecommendationStep(
                    title="⑥ 内装改修＋客室整備",
                    description="3階以下の旅館仕様に内装整備。",
                    days=60,
                    cost_min_man=300,
                    cost_max_man=1000,
                    owner="施工業者・内装業者",
                ),
            ],
            contact="一級建築士事務所、構造設計事務所、解体工事業者",
        )
    )

    # 案3：用途変更先の見直し（簡易宿所等への切替）
    options.append(
        RecommendationOption(
            title="案3：業態を簡易宿所など別形態に切替",
            summary=(
                "旅館・ホテル営業（法27条の規模規定が厳しい）から、簡易宿所営業（同じ建基法上の「ホテル又は旅館」だが客室1室OK）"
                "に切替。または、住宅宿泊事業（民泊・180日上限）に切替。"
                "**※法27条上の扱いは旅館・ホテルと同じため、本質的解決にはならないが**、"
                "営業日数を絞る運用で全体投資を圧縮できる可能性。"
            ),
            cost_min_man=300,
            cost_max_man=1500,
            duration_min_months=4,
            duration_max_months=8,
            pros=[
                "投資規模を圧縮できる可能性",
                "民泊は許可ではなく届出で済む",
                "運用負荷も軽い",
            ],
            cons=[
                "民泊は180日上限で売上のキャップが厳しい",
                "簡易宿所でも法27条の規模規定は同じく適用される",
                "事業計画の全面見直しが必要",
            ],
            steps=[
                RecommendationStep(
                    title="① 事業計画再評価",
                    description="想定売上・利回りを業態別に比較。",
                    days=14,
                    cost_min_man=0,
                    cost_max_man=30,
                    owner="事業主・コンサル",
                ),
                RecommendationStep(
                    title="② 自治体保健所に事前相談",
                    description="目指す業態が当該物件で可能か確認。",
                    days=7,
                    cost_min_man=0,
                    cost_max_man=0,
                    owner="事業主・建築士",
                ),
                RecommendationStep(
                    title="③ 業態別の改修計画立案",
                    description="簡易宿所・民泊それぞれの要件を満たす改修内容を策定。",
                    days=21,
                    cost_min_man=30,
                    cost_max_man=80,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="④ 改修工事＋届出/申請",
                    description="工事完了後、保健所への申請・届出。",
                    days=120,
                    cost_min_man=200,
                    cost_max_man=1400,
                    owner="施工業者・行政書士",
                ),
            ],
            contact="所轄保健所、行政書士、一級建築士事務所",
        )
    )

    return options


def _options_for_ord_121(project: ProjectInput, threshold: int) -> List[RecommendationOption]:
    """令121条 二方向避難 不適合時の対応案."""
    fa = project.floor_area_m2 or 300
    floors_above = project.floors_above or 3

    options: List[RecommendationOption] = []

    options.append(
        RecommendationOption(
            title="案1：屋外避難階段を増設",
            summary=(
                "既存の屋内階段に加え、外部に避難階段を1本増設して二方向避難を確保。"
                "敷地に余裕がある場合の標準解。"
            ),
            cost_min_man=300,
            cost_max_man=1200,
            duration_min_months=3,
            duration_max_months=6,
            pros=[
                "客室レイアウトを大きく変えずに済む",
                "営業面積を減らさない",
                "確実に法令適合できる",
            ],
            cons=[
                "敷地に階段設置スペースが必要",
                "建ぺい率・斜線制限に影響する場合あり",
                "外観デザインが変わる",
            ],
            steps=[
                RecommendationStep(
                    title="① 階段設置位置の検討（敷地調査）",
                    description="法定容積・建ぺい・斜線への影響、避難経路としての適切性を確認。",
                    days=14,
                    cost_min_man=20,
                    cost_max_man=50,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="② 鉄骨階段の設計",
                    description="既製品の屋外階段（メーカー品）を中心に選定。",
                    days=14,
                    cost_min_man=20,
                    cost_max_man=60,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="③ 確認申請（増築扱い）",
                    description="屋外階段は床面積カウント外でも構造変更扱いになる。",
                    days=45,
                    cost_min_man=50,
                    cost_max_man=150,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="④ 基礎工事＋鉄骨階段設置",
                    description="独立基礎を打って既製品階段を建て付ける。",
                    days=30,
                    cost_min_man=200,
                    cost_max_man=800,
                    owner="施工業者（鉄骨専門）",
                ),
                RecommendationStep(
                    title="⑤ 既存壁との取合い・防水",
                    description="階段の壁面接続部、避難扉の設置と防水処理。",
                    days=14,
                    cost_min_man=30,
                    cost_max_man=150,
                    owner="施工業者",
                ),
            ],
            contact="一級建築士事務所、鉄骨階段メーカー（横森製作所、東京鉄骨橋梁等）",
        )
    )

    options.append(
        RecommendationOption(
            title=f"案2：宿泊室を{threshold}㎡以下に再配置",
            summary=(
                f"避難階以外の各階の宿泊室合計を{threshold}㎡以下に抑える計画。"
                "客室数を減らすか、上階の一部を共用ラウンジ・倉庫に転用。"
            ),
            cost_min_man=100,
            cost_max_man=500,
            duration_min_months=2,
            duration_max_months=4,
            pros=[
                "新規工事最小で適合化",
                "屋外階段増設の難しい狭小地でも可",
                "コストインパクト小",
            ],
            cons=[
                "客室数が減るので売上に直接影響",
                "「非客室」エリアの収益化が課題",
            ],
            steps=[
                RecommendationStep(
                    title="① 客室配置の再計画",
                    description=f"各階の宿泊室面積を{threshold}㎡以下に収める間取り。",
                    days=14,
                    cost_min_man=20,
                    cost_max_man=50,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="② 非客室エリアの活用検討",
                    description="共用ラウンジ・コワーキング・物販等への転用設計。",
                    days=14,
                    cost_min_man=10,
                    cost_max_man=30,
                    owner="一級建築士事務所",
                ),
                RecommendationStep(
                    title="③ 内装改修工事",
                    description="間仕切り変更を中心とした軽改修。",
                    days=60,
                    cost_min_man=70,
                    cost_max_man=420,
                    owner="施工業者・内装業者",
                ),
            ],
            contact="一級建築士事務所、内装業者",
        )
    )

    return options
