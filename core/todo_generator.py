"""TODO・不足書類リストの生成エンジン.

調査パターン（A〜D）と、既にアップロードされた書類リストから、
- 不足している書類
- 次にやるべき TODO
を生成する。
"""
from __future__ import annotations

from typing import List, Set, Tuple

from .models import (
    DocumentType,
    ExtractedDocument,
    InvestigationPattern,
    JudgmentLevel,
    TodoItem,
    ZoningJudgment,
)


# パターン別に「最低限あると望ましい書類」
REQUIRED_DOCS_BY_PATTERN = {
    InvestigationPattern.A: {
        DocumentType.INSPECTION_CERTIFICATE,
        DocumentType.CONFIRMATION_CERTIFICATE,
        DocumentType.BUILDING_REGISTRY,
        DocumentType.IMPORTANT_MATTERS,
    },
    InvestigationPattern.B: {
        DocumentType.INSPECTION_CERTIFICATE,
        DocumentType.CONFIRMATION_CERTIFICATE,
        DocumentType.BUILDING_PLAN_SUMMARY,
        DocumentType.BUILDING_REGISTRY,
        DocumentType.IMPORTANT_MATTERS,
        DocumentType.DESIGN_DRAWING,
    },
    InvestigationPattern.C: {
        DocumentType.INSPECTION_CERTIFICATE,
        DocumentType.CONFIRMATION_CERTIFICATE,
        DocumentType.BUILDING_PLAN_SUMMARY,
        DocumentType.BUILDING_REGISTRY,
        DocumentType.IMPORTANT_MATTERS,
        DocumentType.DESIGN_DRAWING,
        DocumentType.STRUCTURAL_CALC,
    },
    InvestigationPattern.D: {
        DocumentType.CONFIRMATION_CERTIFICATE,
        DocumentType.BUILDING_PLAN_SUMMARY,
        DocumentType.BUILDING_REGISTRY,
        DocumentType.IMPORTANT_MATTERS,
        DocumentType.DESIGN_DRAWING,
        DocumentType.STRUCTURAL_CALC,
    },
}


DOC_LABELS = {
    DocumentType.INSPECTION_CERTIFICATE: "検査済証（原本／写し／台帳記載事項証明書）",
    DocumentType.CONFIRMATION_CERTIFICATE: "確認済証（原本／写し）",
    DocumentType.BUILDING_PLAN_SUMMARY: "建築計画概要書（自治体保存分）",
    DocumentType.BUILDING_REGISTRY: "建物登記簿謄本",
    DocumentType.LAND_REGISTRY: "土地登記簿謄本",
    DocumentType.IMPORTANT_MATTERS: "重要事項説明書",
    DocumentType.SALES_CONTRACT: "売買契約書",
    DocumentType.DESIGN_DRAWING: "既存図面一式（意匠・設備・構造）",
    DocumentType.STRUCTURAL_CALC: "構造計算書",
}


def missing_documents(
    pattern: InvestigationPattern,
    uploaded: List[ExtractedDocument],
) -> List[str]:
    """不足書類のラベル一覧."""
    required = REQUIRED_DOCS_BY_PATTERN.get(pattern, set())
    have: Set[DocumentType] = {d.document_type for d in uploaded}
    missing = required - have
    return [DOC_LABELS.get(dt, dt.value) for dt in missing]


def _municipality_todos(municipality_key) -> List[TodoItem]:
    """自治体固有の最優先確認事項をTODO化する（窓口の電話番号つき）."""
    from .municipality import get_municipality_rule
    muni = get_municipality_rule(municipality_key)
    if not muni or muni.get("detail_level") != "full":
        return []
    name = muni.get("name", municipality_key)
    todos: List[TodoItem] = []

    # 建築課の電話番号を引く
    tel_kenchiku = ""
    tel_hokenjo = ""
    for c in muni.get("contacts", []):
        if "建築" in (c.get("dept") or "") and not tel_kenchiku:
            tel_kenchiku = c.get("tel", "")
        if "生活衛生" in (c.get("dept") or "") and not tel_hokenjo:
            tel_hokenjo = c.get("tel", "")

    # 事業化不可になり得る条件は最優先
    for b in (muni.get("building_code_notes") or {}).get("blocking", []):
        todos.append(TodoItem(
            title=f"【最優先】{b.get('title', '')}の該当有無を確認",
            description=(
                f"{name}：{b.get('detail', '')}"
                + (f"／確認先：建築課 建築指導係 {tel_kenchiku}" if tel_kenchiku else "")
            ),
            priority="high",
            owner="クライアント",
            estimated_days=1,
        ))

    # 用途地域の確認（区の地図サービス）
    z = muni.get("zoning") or {}
    if z.get("map_service"):
        todos.append(TodoItem(
            title="用途地域・地区計画・特別用途地区の確認",
            description=(
                f"{name}：{z['map_service']} で用途地域を確認。"
                + "／".join(z.get("special_district_prohibitions", []))
                + "／" + "／".join(z.get("extra_notes", []))
            ),
            priority="high",
            owner="クライアント",
            estimated_days=1,
        ))

    # 保健所への事前相談
    if tel_hokenjo:
        todos.append(TodoItem(
            title=f"{name}保健所へ事前相談（図面持参）",
            description=(
                f"生活衛生課 環境衛生係 {tel_hokenjo}。申請場所・構造設備について図面等を持参して相談。"
                "玄関帳場（代替設備の可否）・客室面積・定員・便所数・洗面設備を事前に詰める。"
                + (f"／完全無人運営は不可（従業員常駐が必要）"
                   if (muni.get("front_desk") or {}).get("unmanned_allowed") is False else "")
            ),
            priority="high",
            owner="クライアント",
            estimated_days=7,
        ))

    # 建築課への事前相談
    if tel_kenchiku:
        todos.append(TodoItem(
            title=f"{name}建築課へ事前相談",
            description=(
                f"建築課 建築指導係 {tel_kenchiku}。用途変更の可否・既存遡及の範囲・"
                "耐火要件・区画・接道を確認。確認済証／検査済証の記録照会も併せて依頼。"
            ),
            priority="high",
            owner="建築士",
            estimated_days=7,
        ))
    return todos


def generate_todos(
    pattern: InvestigationPattern,
    overall_level: JudgmentLevel,
    zoning: ZoningJudgment,
    missing_docs: List[str],
    milestone_flags: List[str],
    municipality_key=None,
) -> List[TodoItem]:
    """総合判定とパターンに応じた TODO 生成."""
    todos: List[TodoItem] = []
    # 自治体固有の最優先事項を先頭に（見落とすと後戻りが大きい）
    todos.extend(_municipality_todos(municipality_key))

    # NO-GO ならまず中止 or 別物件検討
    if overall_level == JudgmentLevel.NO_GO:
        todos.append(
            TodoItem(
                title="案件停止または再検討",
                description=(
                    f"立地が NO-GO 判定です。理由：{zoning.reason}"
                    "別の物件・別の業態（簡易宿所など）への切替を検討してください。"
                ),
                priority="high",
                owner="クライアント",
                estimated_days=1,
            )
        )
        return todos

    # 不足書類の取得
    for label in missing_docs:
        todos.append(
            TodoItem(
                title=f"取得：{label}",
                description=_doc_acquisition_hint(label),
                priority="high",
                owner="クライアント / 不動産会社",
                estimated_days=7,
            )
        )

    # 距離規制の確認
    todos.append(
        TodoItem(
            title="距離規制（学校等100m以内）の現地確認",
            description=(
                "Googleマップ等で半径100m以内の学校・幼稚園・保育所・"
                "児童福祉施設の有無を確認。該当ありなら自治体保健所に事前相談。"
            ),
            priority="high",
            owner="建築士",
            estimated_days=1,
        )
    )

    # パターンごとの追加TODO
    if pattern in {InvestigationPattern.C, InvestigationPattern.D}:
        todos.append(
            TodoItem(
                title="指定確認検査機関へ事前相談",
                description=(
                    "国交省ガイドラインに基づく法適合状況調査のスコープと"
                    "費用感を確定。建物用途・規模・自治体差を共有して見積取得。"
                ),
                priority="high",
                owner="建築士",
                estimated_days=14,
            )
        )
        todos.append(
            TodoItem(
                title="既存図面の復元 / 現地実測 計画立案",
                description=(
                    "図書散逸または欠落のため、復元設計＋現地実測が必要。"
                    "建築士・構造設計者・設備設計者の体制を組む。"
                ),
                priority="high",
                owner="建築士",
                estimated_days=30,
            )
        )

    if pattern == InvestigationPattern.B:
        todos.append(
            TodoItem(
                title="既存不適格項目の洗い出し",
                description=(
                    "既存図面を現行基準と突き合わせ、既存不適格（救済）・"
                    "不適合（是正）を切り分け。Phase 4 のチェックリストへ連動。"
                ),
                priority="medium",
                owner="建築士",
                estimated_days=14,
            )
        )

    # 法改正節目フラグごとに TODO
    for flag in milestone_flags:
        todos.append(
            TodoItem(
                title="法改正節目またぎ：要追加調査",
                description=flag,
                priority="high",
                owner="建築士",
                estimated_days=14,
            )
        )

    # 自治体協議
    todos.append(
        TodoItem(
            title="所管自治体・保健所への事前協議",
            description=(
                "対象自治体の旅館業条例・建築指導課・保健所と事前協議し、"
                "上乗せ規制・運用差を確認。"
            ),
            priority="medium",
            owner="建築士",
            estimated_days=14,
        )
    )

    # 消防事前相談
    todos.append(
        TodoItem(
            title="所轄消防署への事前相談",
            description=(
                "消防法令適合通知書の取得要件、必要設備（自火報・誘導灯・SP等）の"
                "適用範囲を確認。"
            ),
            priority="medium",
            owner="建築士 / 設備設計者",
            estimated_days=14,
        )
    )

    return todos


def _doc_acquisition_hint(label: str) -> str:
    hints = {
        "検査済証（原本／写し／台帳記載事項証明書）": (
            "所有者から原本入手。なければ自治体建築指導課で『台帳記載事項証明書』を申請。"
        ),
        "確認済証（原本／写し）": (
            "所有者から原本入手。なければ自治体で証明書類を申請。"
        ),
        "建築計画概要書（自治体保存分）": (
            "自治体建築指導課で閲覧申請（多くは無料、写し交付は手数料あり）。"
        ),
        "建物登記簿謄本": "法務局またはオンラインで取得。",
        "土地登記簿謄本": "法務局またはオンラインで取得。",
        "重要事項説明書": "不動産会社から取り寄せ。",
        "売買契約書": "不動産会社から取り寄せ。",
        "既存図面一式（意匠・設備・構造）": (
            "所有者・前所有者・設計事務所・施工会社に確認。"
            "散逸時は復元設計を視野に入れる。"
        ),
        "構造計算書": (
            "建築年と構造種別による。所有者・設計事務所に確認。"
        ),
    }
    return hints.get(label, "関係者に確認のうえ取得。")
