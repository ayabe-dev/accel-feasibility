"""Pydantic データモデル定義."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


class BusinessType(str, Enum):
    """対象業態."""

    HOTEL_RYOKAN = "hotel_ryokan"  # 旅館・ホテル営業


class ProjectInput(BaseModel):
    """ユーザー入力."""

    address: str = Field(..., description="物件所在地（住居表示または地番）")
    business_type: BusinessType = Field(BusinessType.HOTEL_RYOKAN)
    floor_area_m2: Optional[float] = Field(None, description="延床面積（既知の場合）")
    floors_above: Optional[int] = Field(None, description="地上階数")
    floors_below: Optional[int] = Field(None, description="地下階数")
    structure: Optional[str] = Field(None, description="構造（木造/RC造/S造 等）")
    built_year: Optional[int] = Field(None, description="建築年")
    has_inspection_certificate: Optional[bool] = Field(
        None, description="検査済証の有無"
    )
    renovation_history: Optional[bool] = Field(
        None, description="増改築・大規模修繕履歴の有無"
    )
    additional_context: Optional[str] = Field(
        None,
        description=(
            "ユーザーが自由記述した特記事項（元の用途、既存設備、周辺状況、"
            "リスク要因、事業計画上の制約など）"
        ),
    )


# ---------------------------------------------------------------------------
# GIS 取得結果
# ---------------------------------------------------------------------------


class NearbyFacility(BaseModel):
    """学校・保育園等のPOI."""

    name: str
    facility_type: str  # 小学校 / 中学校 / 高校 / 幼稚園 / 保育園 等
    address: str = ""
    distance_m: float


class GeoLookupResult(BaseModel):
    """GIS API から取得した情報."""

    address: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    zoning_code: Optional[str] = Field(
        None, description="用途地域コード（例: first_residential）"
    )
    zoning_name: Optional[str] = None
    fire_district: Optional[str] = Field(
        None, description="fire_district / quasi_fire_district / no_district"
    )
    coverage_ratio_pct: Optional[float] = Field(None, description="建ぺい率(%)")
    floor_area_ratio_pct: Optional[float] = Field(None, description="容積率(%)")
    # XKT001 都市計画区域・区域区分
    city_planning_classification: Optional[str] = Field(
        None, description="市街化区域 / 市街化調整区域 / 非線引き / 都市計画区域外 等"
    )
    # XKT023 地区計画
    district_plan_name: Optional[str] = Field(None, description="地区計画名")
    # XKT024 高度利用地区
    high_use_district_name: Optional[str] = Field(None, description="高度利用地区名")
    # XKT006/007 近接施設
    nearby_facilities: List[NearbyFacility] = Field(default_factory=list)
    source: str = Field(
        "demo", description="reinfolib / municipal / demo / manual / document"
    )
    notes: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 判定結果
# ---------------------------------------------------------------------------


class JudgmentLevel(str, Enum):
    GO = "GO"
    CONDITIONAL = "CONDITIONAL"
    NO_GO = "NO_GO"


class ZoningJudgment(BaseModel):
    """用途地域判定の結果."""

    level: JudgmentLevel
    zoning_name: Optional[str] = None
    reason: str
    max_floor_area_m2: Optional[float] = None
    floor_area_check: Optional[str] = Field(
        None, description="床面積制限のチェック結果コメント"
    )


class DistanceCheckResult(BaseModel):
    """距離規制チェック結果."""

    has_issue: bool
    nearby_facilities: List[str] = Field(default_factory=list)
    action_required: Optional[str] = None
    note: str = ""


class InvestigationPattern(str, Enum):
    """既存建物の調査深度パターン（Phase 2 マトリクス）."""

    A = "A"  # 検査済証あり/築浅/図書完備
    B = "B"  # 検査済証あり/中間
    C = "C"  # 検査済証あり/築古
    D = "D"  # 検査済証なし
    UNKNOWN = "UNKNOWN"  # 判定不能


class CostEstimate(BaseModel):
    """費用・期間レンジ（万円・月）."""

    investigation_cost_min: int
    investigation_cost_max: int
    application_cost_min: int
    application_cost_max: int
    renovation_cost_min: int
    renovation_cost_max: int
    total_months_min: int
    total_months_max: int
    confidence: str  # 高 / 中 / 低
    investigation_tasks: List[str] = Field(default_factory=list)
    application_tasks: List[str] = Field(default_factory=list)
    renovation_tasks: List[str] = Field(default_factory=list)

    @property
    def total_cost_min(self) -> int:
        return (
            self.investigation_cost_min
            + self.application_cost_min
            + self.renovation_cost_min
        )

    @property
    def total_cost_max(self) -> int:
        return (
            self.investigation_cost_max
            + self.application_cost_max
            + self.renovation_cost_max
        )


# ---------------------------------------------------------------------------
# 書類解析
# ---------------------------------------------------------------------------


class DocumentType(str, Enum):
    INSPECTION_CERTIFICATE = "inspection_certificate"  # 検査済証
    CONFIRMATION_CERTIFICATE = "confirmation_certificate"  # 確認済証
    BUILDING_PLAN_SUMMARY = "building_plan_summary"  # 建築計画概要書
    IMPORTANT_MATTERS = "important_matters"  # 重要事項説明書
    LAND_REGISTRY = "land_registry"  # 登記簿（土地）
    BUILDING_REGISTRY = "building_registry"  # 登記簿（建物）
    SALES_CONTRACT = "sales_contract"  # 売買契約書
    DESIGN_DRAWING = "design_drawing"  # 設計図書
    STRUCTURAL_CALC = "structural_calc"  # 構造計算書
    REAL_ESTATE_FLYER = "real_estate_flyer"  # マイソク（物件販売資料）
    OTHER = "other"


class ExtractedDocument(BaseModel):
    """Claude API で抽出した書類情報."""

    file_name: str
    document_type: DocumentType
    confidence: float = Field(..., ge=0.0, le=1.0)
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)
    raw_text_summary: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 総合判定
# ---------------------------------------------------------------------------


class TodoItem(BaseModel):
    title: str
    description: str
    priority: str = "medium"  # high / medium / low
    owner: str = "建築士"  # 建築士 / クライアント / 不動産会社 等
    estimated_days: Optional[int] = None


class ApplicationRequirementJudgment(BaseModel):
    """Phase 3：用途変更確認申請の要否判定."""

    required: bool
    reason: str
    is_special_building: bool = True  # 旅館・ホテルは特殊建築物
    is_similar_use: bool = False  # 住宅→旅館は非類似
    floor_area_subject_m2: Optional[float] = None
    threshold_m2: int = 200
    applicable_articles: List[str] = Field(default_factory=list)
    related_obligations: List[str] = Field(
        default_factory=list,
        description="確認申請不要でも遡及する規定など",
    )


class CheckStatus(str, Enum):
    """規定別チェックステータス."""

    COMPLIANT = "compliant"  # 適合
    NON_COMPLIANT = "non_compliant"  # 不適合（要改修）
    NEEDS_REVIEW = "needs_review"  # 要確認
    NOT_APPLICABLE = "not_applicable"  # 非該当
    UNKNOWN = "unknown"  # 情報不足で判定不能


class RecommendationStep(BaseModel):
    """対応案の1ステップ."""

    title: str
    description: str = ""
    days: Optional[int] = None
    cost_min_man: Optional[int] = None  # 万円
    cost_max_man: Optional[int] = None  # 万円
    owner: str = "建築士"


class RecommendationOption(BaseModel):
    """1つの対応案（複数の案を選択肢として提示）."""

    title: str
    summary: str = ""
    cost_min_man: int = 0
    cost_max_man: int = 0
    duration_min_months: int = 0
    duration_max_months: int = 0
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)
    steps: List[RecommendationStep] = Field(default_factory=list)
    contact: str = ""  # 関係者・連絡先


class BuildingCodeCheckItem(BaseModel):
    """建基法の1規定単位."""

    rule_id: str
    rule_name: str
    article: str  # "法27条" 等
    status: CheckStatus
    requirement: str  # 何が要求されているか
    current: str = ""  # 現況
    recommended_action: str = ""
    impact: str = "medium"  # low / medium / high (改修コストへの影響)
    options: List[RecommendationOption] = Field(default_factory=list)


class FireSafetyCheckItem(BaseModel):
    """消防設備チェック."""

    rule_id: str
    equipment_name: str
    article: str  # 消防令の条文
    required: bool
    threshold_note: str  # どの規模・条件で必要か
    current_status: str = ""
    action: str = ""  # "既設" / "新設要" / "確認要"


class LodgingBusinessCheckItem(BaseModel):
    """旅館業法構造設備基準."""

    rule_id: str
    item_name: str  # "客室面積" "玄関帳場" 等
    standard: str  # 基準値
    status: CheckStatus
    note: str = ""


class ContextImpactItem(BaseModel):
    """追加情報による1つの影響項目."""

    aspect: str  # "建基法" "消防" "コスト" "事業性" 等
    direction: str  # "プラス" / "マイナス" / "中立"
    summary: str
    affected_rules: List[str] = Field(default_factory=list)
    score_adjustment_hint: int = 0  # -20〜+20 の目安


class ContextImpact(BaseModel):
    """追加情報の解釈結果."""

    raw_context: str
    overall_summary: str = ""
    impacts: List[ContextImpactItem] = Field(default_factory=list)
    suggested_additional_questions: List[str] = Field(default_factory=list)
    warning: str = ""  # 解釈に失敗した場合等


class FeasibilityReport(BaseModel):
    """Phase 1 一次スクリーニングの総合レポート."""

    input: ProjectInput
    geo: GeoLookupResult
    zoning: ZoningJudgment
    distance: Optional[DistanceCheckResult] = None
    pattern: InvestigationPattern
    pattern_reason: str
    cost_estimate: Optional[CostEstimate] = None
    overall_level: JudgmentLevel
    overall_summary: str
    extracted_documents: List[ExtractedDocument] = Field(default_factory=list)
    missing_documents: List[str] = Field(default_factory=list)
    todos: List[TodoItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    # Phase 3
    application_requirement: Optional[ApplicationRequirementJudgment] = None
    # Phase 4
    building_code_checks: List[BuildingCodeCheckItem] = Field(default_factory=list)
    # Phase 5
    fire_safety_checks: List[FireSafetyCheckItem] = Field(default_factory=list)
    lodging_business_checks: List[LodgingBusinessCheckItem] = Field(default_factory=list)
    # スコアリング（オプション、judgment.run()後に compute_score で計算）
    score_breakdown: Optional[dict] = None  # ScoreBreakdown をdict化して保持
    # 追加情報の影響解釈（Geminiで生成）
    context_impact: Optional["ContextImpact"] = None
