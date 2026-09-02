"""旅館業許可の可否判定エンジン（本アプリの主判定）.

問いの立て方を「用途変更できるか」から「**旅館業の許可が取れるか**」に変えた層。
不許可事由（旅館業法3条2項・3項）を起点に、許可までに越える必要があるものを
ゲートとして並べ、どこで落ちるかを示す。

## ゲート構成

  A 立地系   … 許可権者が「場所」を理由に不許可にできる領域
     A1 用途地域（建基法48条・別表第二）
     A2 都市計画（市街化調整区域・地区計画・特別用途地区）
     A3 学校等おおむね100m（旅館業法3条3項・4項）
     A4 設置場所が公衆衛生上不適当（旅館業法3条2項）

  B 建物系   … 建物が適法な旅館・ホテルになれるか
     B1 用途変更確認申請と検査済証（建基法87条）
     B2 法27条 耐火要求（令和元年緩和込み）
     B3 避難・防火（令119/120/121/112 等）
     B4 消防法令適合通知書（許可申請の添付書類＝実質ゲート）

  C 構造設備 … 旅館業法施行令1条＋自治体条例の上乗せ
     C1 客室面積（業態で基準が違う）
     C2 玄関帳場／ICT代替・無人運営の可否
     C3 入浴・便所・洗面・換気採光等

  D 申請者   … 欠格事由（物件情報では判定不能＝ヒアリング項目）

## 判定に使う根拠

  全国共通の法令 → core/evidence.py の PRIMARY_SOURCES（人手で確認した正本）
  自治体の上乗せ → core/legal_research.py（Claude + web検索。検証済みのみ採用）

  「検証済み」＝原文引用と公的ドメインの出典URLが両方ある、の意。
  未検証の所見はレポートに表示するが、ゲートの status は動かさない。
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from .evidence import Evidence, cite, evidence_confidence, verified_only
from .models import (
    BusinessType,
    DistanceCheckResult,
    GeoLookupResult,
    JudgmentLevel,
    ProjectInput,
    ZoningJudgment,
)

try:  # 調査層は任意（APIキーが無くても全国法令ベースで判定できる）
    from .legal_research import ResearchResult
except ImportError:  # pragma: no cover
    ResearchResult = None  # type: ignore


# ---------------------------------------------------------------------------
# 型
# ---------------------------------------------------------------------------


class GateStatus(str, Enum):
    PASS = "pass"  # 要件を満たしている
    CONDITIONAL = "conditional"  # 改修・手続きで越えられる
    CONSULT = "consult"  # 自治体・所轄機関の判断で決まる（事前協議必須）
    FAIL = "fail"  # 現状のままでは越えられない
    UNKNOWN = "unknown"  # 情報不足で判定できない
    NOT_APPLICABLE = "not_applicable"  # 該当しない


GATE_STATUS_LABEL: Dict[str, str] = {
    GateStatus.PASS: "✅ クリア",
    GateStatus.CONDITIONAL: "🔧 対応すればクリア",
    GateStatus.CONSULT: "🤝 事前協議で決まる",
    GateStatus.FAIL: "❌ 現状では越えられない",
    GateStatus.UNKNOWN: "❓ 情報不足",
    GateStatus.NOT_APPLICABLE: "⚪ 非該当",
}


class LicenseVerdict(str, Enum):
    GRANTABLE = "grantable"  # 取得可（標準フロー）
    CONDITIONAL = "conditional"  # 条件付き取得可（改修・申請が前提）
    CONSULT = "consult"  # 自治体協議次第（可否が行政判断に依存）
    DIFFICULT = "difficult"  # 実質困難（大規模改修・減築が前提）
    BLOCKED = "blocked"  # 不可（立地・条例で越えられない）
    UNKNOWN = "unknown"  # 情報不足で判定できない


class LicenseGateItem(BaseModel):
    """許可までに越えるゲート1つ."""

    gate_id: str
    category: str  # 立地 / 建物 / 構造設備 / 申請者
    title: str
    status: GateStatus
    blocking: bool = True  # このゲートが総合判定を動かすか
    hard: bool = False  # FAIL時に回避策が物件内に存在しない（＝BLOCKED）
    finding: str = ""  # 判定の内容
    remedy: str = ""  # 越えるための手段
    data_gaps: List[str] = Field(default_factory=list)  # 何が分かれば確定するか
    evidences: List[Evidence] = Field(default_factory=list)

    @property
    def confidence(self) -> str:
        return evidence_confidence(self.evidences)

    @property
    def status_label(self) -> str:
        return GATE_STATUS_LABEL.get(self.status, "—")


class AlternativeRoute(BaseModel):
    """別ルート（簡易宿所・民泊）の見込み."""

    business_type: BusinessType
    label: str
    verdict: LicenseVerdict
    summary: str = ""
    conditions: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    evidences: List[Evidence] = Field(default_factory=list)

    @property
    def verdict_label(self) -> str:
        return verdict_label(self.verdict)


class LicenseJudgment(BaseModel):
    """旅館業許可の可否判定の総合結果."""

    business_type: BusinessType
    business_label: str
    verdict: LicenseVerdict
    headline: str
    gates: List[LicenseGateItem] = Field(default_factory=list)
    alternatives: List[AlternativeRoute] = Field(default_factory=list)
    municipality_key: Optional[str] = None
    municipality_name: str = ""
    permit_authority: str = ""
    next_actions: List[str] = Field(default_factory=list)
    research_summary: str = ""
    research_note: str = ""  # 調査層が使えなかった場合の説明
    researched_on: str = ""
    unresolved: List[str] = Field(default_factory=list)

    @property
    def verdict_label(self) -> str:
        return verdict_label(self.verdict)

    @property
    def blocking_gates(self) -> List[LicenseGateItem]:
        return [
            g
            for g in self.gates
            if g.blocking and g.status in (GateStatus.FAIL, GateStatus.CONSULT)
        ]

    @property
    def data_completeness(self) -> float:
        """判定に必要な情報がどれだけ揃っているか（0〜1）."""
        judged = [g for g in self.gates if g.status != GateStatus.NOT_APPLICABLE]
        if not judged:
            return 0.0
        known = [g for g in judged if g.status != GateStatus.UNKNOWN]
        return round(len(known) / len(judged), 2)

    @property
    def confidence(self) -> str:
        """判定全体の信頼度。情報充足率と根拠の強さの低いほう."""
        dc = self.data_completeness
        by_data = "高" if dc >= 0.85 else ("中" if dc >= 0.6 else "低")
        blocking = [g for g in self.gates if g.blocking]
        confs = [g.confidence for g in blocking] or ["低"]
        rank = {"低": 0, "中": 1, "高": 2}
        by_evidence = min(confs, key=lambda c: rank.get(c, 0))
        return min([by_data, by_evidence], key=lambda c: rank.get(c, 0))

    @property
    def disclaimer(self) -> str:
        return (
            "本判定は一次スクリーニングです。旅館業の許可可否を最終的に決めるのは"
            "所轄保健所・特定行政庁・所轄消防であり、本アプリの出力はその事前協議に"
            "持ち込むための準備資料として使ってください。"
        )


def verdict_label(v: LicenseVerdict) -> str:
    return {
        LicenseVerdict.GRANTABLE: "🟢 許可取得の見込みあり",
        LicenseVerdict.CONDITIONAL: "🟡 条件付きで取得可（改修・申請が前提）",
        LicenseVerdict.CONSULT: "🟠 自治体協議次第（事前相談が必須）",
        LicenseVerdict.DIFFICULT: "🔴 実質困難（大規模改修・計画変更が前提）",
        LicenseVerdict.BLOCKED: "⛔ 許可は下りない（立地・条例で越えられない）",
        LicenseVerdict.UNKNOWN: "❓ 情報不足で判定不能",
    }.get(v, "—")


BUSINESS_LABEL: Dict[str, str] = {
    BusinessType.HOTEL_RYOKAN: "旅館・ホテル営業（旅館業許可）",
    BusinessType.SIMPLE_LODGING: "簡易宿所営業（旅館業許可）",
    BusinessType.MINPAKU: "住宅宿泊事業（民泊・届出）",
}


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def judge_license(
    project: ProjectInput,
    geo: GeoLookupResult,
    zoning: ZoningJudgment,
    municipality_key: Optional[str] = None,
    municipality_name: str = "",
    research: Optional["ResearchResult"] = None,
    distance: Optional[DistanceCheckResult] = None,
) -> LicenseJudgment:
    """旅館業許可の可否を判定する.

    Args:
        research: legal_research.research_municipality() の結果。None でも動く
                  （その場合は全国共通の法令だけで判定し、その旨を明示する）。
    """
    bt = project.business_type

    # 民泊が主業態として選ばれた場合は、許可ではなく届出の判定に切り替える
    if bt == BusinessType.MINPAKU:
        return _judge_minpaku_as_primary(
            project, geo, municipality_key, municipality_name, research
        )

    gates: List[LicenseGateItem] = []
    gates.append(_gate_a1_zoning(project, geo, zoning, research))
    gates.append(_gate_a2_city_planning(geo, research))
    gates.append(_gate_a3_school_distance(geo, distance, research))
    gates.append(_gate_a4_public_health(research))
    gates.append(_gate_b1_use_change(project))
    gates.append(_gate_b2_fire_resistance(project))
    gates.append(_gate_b3_evacuation(project))
    gates.append(_gate_b4_fire_compliance(project, research))
    gates.append(_gate_c1_room_area(project, research))
    gates.append(_gate_c2_front_desk(research))
    gates.append(_gate_c3_facilities(research))
    gates.append(_gate_d1_applicant())

    verdict, headline = _aggregate(gates, BUSINESS_LABEL.get(bt, ""))

    alternatives = _alternatives(
        project, geo, zoning, research, verdict, exclude=bt
    )

    judgment = LicenseJudgment(
        business_type=bt,
        business_label=BUSINESS_LABEL.get(bt, ""),
        verdict=verdict,
        headline=headline,
        gates=gates,
        alternatives=alternatives,
        municipality_key=municipality_key,
        municipality_name=municipality_name
        or (research.municipality_name if research else ""),
        permit_authority=(research.permit_authority if research else ""),
        research_summary=(research.summary if research and research.available else ""),
        research_note=_research_note(research),
        researched_on=(research.researched_on if research else ""),
        unresolved=(list(research.unresolved) if research else []),
    )
    judgment.next_actions = _next_actions(judgment)
    return judgment


def _research_note(research: Optional["ResearchResult"]) -> str:
    if research is None:
        return (
            "自治体条例の調査は実行されていません。全国共通の法令（旅館業法・同施行令・"
            "建築基準法）のみで判定しています。実際の可否は自治体の上乗せ条例で変わるため、"
            "自治体調査を実行するか、所轄保健所へ事前相談してください。"
        )
    if research.error:
        return research.error
    if not research.findings:
        return (
            f"{research.municipality_name} について条例・手引きの所見が得られませんでした。"
            "所轄保健所への事前相談で確認してください。"
        )
    return ""


# ---------------------------------------------------------------------------
# ゲートA：立地
# ---------------------------------------------------------------------------


def _gate_a1_zoning(
    project: ProjectInput,
    geo: GeoLookupResult,
    zoning: ZoningJudgment,
    research: Optional["ResearchResult"],
) -> LicenseGateItem:
    """A1 用途地域.

    旅館業法そのものの要件ではないが、用途地域で「ホテル又は旅館」が建てられない
    場所では適法な施設が作れないため、許可も実質的に下りない。最上位のハード制約。
    """
    ev = cite("bsl_48")
    zoning_name = zoning.zoning_name or geo.zoning_name or "不明"

    if zoning.level == JudgmentLevel.NO_GO:
        return LicenseGateItem(
            gate_id="A1_zoning",
            category="立地",
            title="用途地域（建基法48条・別表第二）",
            status=GateStatus.FAIL,
            blocking=True,
            hard=True,
            finding=zoning.reason,
            remedy=(
                "建基法48条ただし書きの特定行政庁の許可（用途許可）を取るルートは"
                "理論上あるが、住居専用地域でのホテル許可はきわめて例外的。"
                "現実的な選択肢は、住宅宿泊事業（民泊）への切替か、別物件の検討。"
            ),
            evidences=ev,
        )

    if zoning.level == JudgmentLevel.GO:
        detail = zoning.floor_area_check or ""
        return LicenseGateItem(
            gate_id="A1_zoning",
            category="立地",
            title="用途地域（建基法48条・別表第二）",
            status=GateStatus.PASS,
            blocking=True,
            hard=True,
            finding=f"{zoning_name}。{zoning.reason}",
            remedy="",
            data_gaps=[] if not detail else [],
            evidences=ev,
        )

    # CONDITIONAL：用途地域が特定できていない or 規模判定に必要な情報がない
    gaps: List[str] = []
    if geo.zoning_code is None:
        gaps.append("用途地域（自治体の都市計画情報・重要事項説明書で確認）")
    if zoning.max_floor_area_m2 is not None and project.floor_area_m2 is None:
        gaps.append("延床面積（用途地域の規模制限の判定に必要）")
    return LicenseGateItem(
        gate_id="A1_zoning",
        category="立地",
        title="用途地域（建基法48条・別表第二）",
        status=GateStatus.UNKNOWN if gaps else GateStatus.CONSULT,
        blocking=True,
        hard=True,
        finding=zoning.reason,
        remedy="用途地域と延床面積を確定させれば自動判定できます。",
        data_gaps=gaps,
        evidences=ev,
    )


def _gate_a2_city_planning(
    geo: GeoLookupResult, research: Optional["ResearchResult"]
) -> LicenseGateItem:
    """A2 都市計画（市街化調整区域・地区計画・特別用途地区）."""
    ev = cite("city_planning_34", "bsl_68_2")
    local_blocking = (
        research.blocking_findings(["zoning_local"]) if research else []
    )
    notes: List[str] = []

    if geo.city_planning_classification and "市街化調整区域" in str(
        geo.city_planning_classification
    ):
        return LicenseGateItem(
            gate_id="A2_city_planning",
            category="立地",
            title="都市計画（区域区分・地区計画・特別用途地区）",
            status=GateStatus.FAIL,
            blocking=True,
            hard=True,
            finding=(
                "市街化調整区域。都市計画法上、原則として旅館・ホテルの新築・用途変更は不可。"
            ),
            remedy=(
                "都市計画法34条の許可（開発許可・建築許可）が個別に得られれば検討可能だが、"
                "標準的なフローでは不可扱い。"
            ),
            evidences=ev,
        )

    if local_blocking:
        titles = "／".join(f.get("title", "") for f in local_blocking)
        return LicenseGateItem(
            gate_id="A2_city_planning",
            category="立地",
            title="都市計画（区域区分・地区計画・特別用途地区）",
            status=GateStatus.FAIL,
            blocking=True,
            hard=True,
            finding=f"自治体固有の立地制限に該当する可能性：{titles}",
            remedy="該当有無を都市計画課で確認。該当するなら別物件の検討が必要。",
            evidences=ev
            + (research.evidences(["zoning_local"]) if research else []),
        )

    if geo.district_plan_name:
        notes.append(f"地区計画あり：{geo.district_plan_name}")
    if geo.high_use_district_name:
        notes.append(f"高度利用地区：{geo.high_use_district_name}")
    local_cond = research.conditional_findings(["zoning_local"]) if research else []
    for f in local_cond:
        notes.append(f.get("title", ""))

    if notes:
        return LicenseGateItem(
            gate_id="A2_city_planning",
            category="立地",
            title="都市計画（区域区分・地区計画・特別用途地区）",
            status=GateStatus.CONSULT,
            blocking=True,
            hard=False,
            finding="／".join(notes)
            + "。地区計画・特別用途地区は用途地域の上に重ねてホテルを制限する場合がある。",
            remedy="自治体の都市計画課で地区計画の内容（用途の制限）を確認。",
            evidences=ev + (research.evidences(["zoning_local"]) if research else []),
        )

    return LicenseGateItem(
        gate_id="A2_city_planning",
        category="立地",
        title="都市計画（区域区分・地区計画・特別用途地区）",
        status=GateStatus.UNKNOWN
        if geo.city_planning_classification in (None, "", "取得不可")
        else GateStatus.PASS,
        blocking=True,
        hard=False,
        finding=(
            "地区計画・特別用途地区の指定は確認できていません。"
            if geo.city_planning_classification in (None, "", "取得不可")
            else "区域区分・地区計画に旅館・ホテルを制限する指定は確認されませんでした。"
        ),
        remedy="",
        data_gaps=(
            ["都市計画情報（区域区分・地区計画・特別用途地区）"]
            if geo.city_planning_classification in (None, "", "取得不可")
            else []
        ),
        evidences=ev,
    )


def _gate_a3_school_distance(
    geo: GeoLookupResult,
    distance: Optional[DistanceCheckResult],
    research: Optional["ResearchResult"],
) -> LicenseGateItem:
    """A3 学校等おおむね100m（旅館業法3条3項・4項）."""
    ev = cite("ryokan_law_3_3", "ryokan_law_3_4")
    if research:
        ev += research.evidences(["distance_regulation"])

    has_facility: Optional[bool] = None
    names: List[str] = []
    if geo.nearby_facilities:
        has_facility = True
        names = [
            f"{f.name}（{f.facility_type}・{f.distance_m:.0f}m）"
            for f in geo.nearby_facilities
        ]
    elif distance is not None:
        has_facility = distance.has_issue
        names = list(distance.nearby_facilities)

    if has_facility is None:
        return LicenseGateItem(
            gate_id="A3_school_distance",
            category="立地",
            title="学校等おおむね100m（旅館業法3条3項）",
            status=GateStatus.UNKNOWN,
            blocking=True,
            hard=False,
            finding="半径100m以内の学校・児童福祉施設・社会教育施設等の有無が未確認です。",
            remedy="",
            data_gaps=["半径100m以内の対象施設の有無（地図・自治体照会）"],
            evidences=ev,
        )

    if has_facility:
        return LicenseGateItem(
            gate_id="A3_school_distance",
            category="立地",
            title="学校等おおむね100m（旅館業法3条3項）",
            status=GateStatus.CONSULT,
            blocking=True,
            hard=False,
            finding=(
                "半径おおむね100m以内に対象施設あり："
                + "、".join(names[:5])
                + "。条文は『清純な施設環境が著しく害されるおそれ』がある場合に"
                "不許可にできると定めており、該当＝即不許可ではないが、"
                "法3条4項の意見聴取が必要になり標準処理期間が延びる。"
            ),
            remedy=(
                "保健所へ早期に事前相談し、意見照会の見通しを確認する。"
                "客室配置・出入口の向き・看板・運用ルール（深夜の出入り制限等）で"
                "『環境が害されない』ことを説明できる計画にする。"
            ),
            evidences=ev,
        )

    return LicenseGateItem(
        gate_id="A3_school_distance",
        category="立地",
        title="学校等おおむね100m（旅館業法3条3項）",
        status=GateStatus.PASS,
        blocking=True,
        hard=False,
        finding="半径おおむね100m以内に対象施設は確認されませんでした。",
        remedy="",
        evidences=ev,
    )


def _gate_a4_public_health(research: Optional["ResearchResult"]) -> LicenseGateItem:
    """A4 設置場所が公衆衛生上不適当（旅館業法3条2項）."""
    ev = cite("ryokan_law_3_2")
    if research:
        ev += research.evidences(["procedure"])
    return LicenseGateItem(
        gate_id="A4_public_health",
        category="立地",
        title="設置場所の公衆衛生（旅館業法3条2項）",
        status=GateStatus.CONSULT,
        blocking=False,  # 裁量条項。物件情報だけでは判定できないため総合判定は動かさない
        hard=False,
        finding=(
            "『設置場所が公衆衛生上不適当』は許可権者の裁量条項。"
            "周辺の衛生環境、給排水、廃棄物処理、近隣との関係などが問われる。"
        ),
        remedy="事前相談で保健所の懸念点を先に引き出し、計画に織り込む。",
        evidences=ev,
    )


# ---------------------------------------------------------------------------
# ゲートB：建物
# ---------------------------------------------------------------------------


def conversion_area(project: ProjectInput) -> Optional[float]:
    """用途変更の対象となる床面積.

    延床全体を転用するとは限らない（1階を住居に残す等）。
    conversion_area_m2 が入力されていればそれを、無ければ延床を使う。
    """
    v = getattr(project, "conversion_area_m2", None)
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    return project.floor_area_m2


def _gate_b1_use_change(project: ProjectInput) -> LicenseGateItem:
    """B1 用途変更確認申請と検査済証."""
    ev = cite("bsl_87")
    area = conversion_area(project)
    partial = (
        area is not None
        and project.floor_area_m2 is not None
        and abs(area - project.floor_area_m2) > 0.5
    )
    area_note = (
        f"用途変更部分 {area:,.1f}㎡（延床 {project.floor_area_m2:,.1f}㎡ のうち）"
        if partial and project.floor_area_m2
        else (f"用途変更部分 {area:,.1f}㎡" if area else "用途変更部分の面積が未入力")
    )

    insp = project.has_inspection_certificate
    gaps: List[str] = []
    if area is None:
        gaps.append("用途変更部分の床面積")
    if insp is None:
        gaps.append("検査済証の有無（建築計画概要書・台帳記載事項証明書で確認可）")

    if area is None:
        return LicenseGateItem(
            gate_id="B1_use_change",
            category="建物",
            title="用途変更確認申請・検査済証（建基法87条）",
            status=GateStatus.UNKNOWN,
            blocking=True,
            hard=False,
            finding="用途変更部分の床面積が未入力のため、確認申請の要否を判定できません。",
            remedy="",
            data_gaps=gaps,
            evidences=ev,
        )

    parts: List[str] = [area_note]
    if area <= 200:
        parts.append(
            "200㎡以下のため用途変更確認申請は不要（2019年6月改正以降）。"
            "ただし建基法・消防法の実体規定は適用されるため、"
            "『申請不要＝改修不要』ではない。"
        )
        status = GateStatus.PASS
        remedy = ""
    else:
        parts.append(
            "200㎡超かつ住宅→旅館・ホテルは非類似用途間のため、"
            "用途変更確認申請が必要（申請先：指定確認検査機関または建築主事）。"
        )
        status = GateStatus.CONDITIONAL
        remedy = "一級建築士事務所に用途変更確認申請を依頼。事前協議に2〜3ヶ月見込む。"

    if insp is False:
        parts.append(
            "検査済証がないため、国交省ガイドラインに基づく法適合状況調査が"
            "実質的に必須となり、調査費用と期間が大きく増える。"
        )
        status = GateStatus.CONDITIONAL
        remedy = (
            (remedy + " ") if remedy else ""
        ) + "検査済証なし → 指定確認検査機関等による法適合状況調査を先に実施。"
    elif insp is None:
        parts.append("検査済証の有無が未確認（安全側に重い前提で見ておくべき）。")

    return LicenseGateItem(
        gate_id="B1_use_change",
        category="建物",
        title="用途変更確認申請・検査済証（建基法87条）",
        status=status,
        blocking=True,
        hard=False,
        finding=" ".join(parts),
        remedy=remedy,
        data_gaps=gaps,
        evidences=ev,
    )


def _is_fireproof(structure: Optional[str]) -> bool:
    if not structure:
        return False
    return any(s in structure for s in ["RC", "SRC", "鉄筋コンクリート", "鉄骨鉄筋"])


def _gate_b2_fire_resistance(project: ProjectInput) -> LicenseGateItem:
    """B2 法27条 耐火要求.

    令和元年6月25日施行の緩和（階数3・延べ200㎡未満は警報設備で耐火要求を外せる）を
    反映する。ここを見落とすと、小規模な木造3階建てに不要な耐火改修費を計上してしまう。
    """
    ev = cite("bsl_27", "bsl_27_relax_2019")
    floors = project.floors_above or 0
    area = project.floor_area_m2
    structure = project.structure
    fireproof = _is_fireproof(structure)

    if floors == 0 or area is None:
        return LicenseGateItem(
            gate_id="B2_fire_resistance",
            category="建物",
            title="法27条 耐火要求",
            status=GateStatus.UNKNOWN,
            blocking=True,
            hard=False,
            finding="階数または延床面積が未入力のため判定できません。",
            remedy="",
            data_gaps=["地上階数", "延床面積"],
            evidences=ev,
        )

    if floors >= 3:
        if fireproof:
            return LicenseGateItem(
                gate_id="B2_fire_resistance",
                category="建物",
                title="法27条 耐火要求",
                status=GateStatus.PASS,
                blocking=True,
                hard=False,
                finding=(
                    f"{floors}階建 / 構造 {structure}（耐火相当）。"
                    "3階以上の宿泊用途に求められる耐火性能を満たす蓋然性が高い。"
                ),
                remedy="竣工図書で現況の耐火性能を確認。",
                evidences=ev,
            )
        if floors == 3 and area < 200:
            return LicenseGateItem(
                gate_id="B2_fire_resistance",
                category="建物",
                title="法27条 耐火要求",
                status=GateStatus.CONDITIONAL,
                blocking=True,
                hard=False,
                finding=(
                    f"3階建 / 延床 {area:,.1f}㎡（200㎡未満） / 構造 {structure or '不明'}。"
                    "令和元年6月25日施行の緩和により、耐火建築物等とせず"
                    "**警報設備の設置**で対応できる規模。"
                    "耐火被覆や減築を前提にしなくてよい可能性が高い。"
                ),
                remedy=(
                    "告示仕様の警報設備（感知器の種別・警戒区域・非常電源等）で"
                    "緩和が適用できるか、一級建築士と特定行政庁に確認する。"
                    "適用できれば耐火改修費（数百万〜1,500万円規模）を回避できる。"
                ),
                evidences=ev,
            )
        return LicenseGateItem(
            gate_id="B2_fire_resistance",
            category="建物",
            title="法27条 耐火要求",
            status=GateStatus.FAIL,
            blocking=True,
            hard=False,
            finding=(
                f"{floors}階建 / 延床 {area:,.1f}㎡ / 構造 {structure or '不明'}（非耐火）。"
                "3階以上を宿泊用途にする場合、耐火建築物等が必要。"
                "延べ200㎡未満の緩和も規模から適用外。"
            ),
            remedy=(
                "①耐火被覆・耐火構造への補強、②宿泊用途を2階以下に集約（3階は非宿泊用途）、"
                "③減築、のいずれか。②が最も安価に済むケースが多い。"
            ),
            evidences=ev,
        )

    if floors == 2 and area >= 300:
        return LicenseGateItem(
            gate_id="B2_fire_resistance",
            category="建物",
            title="法27条 耐火要求",
            status=GateStatus.CONSULT,
            blocking=True,
            hard=False,
            finding=(
                f"2階建 / 延床 {area:,.1f}㎡。2階の宿泊部分の床面積によって"
                "耐火・準耐火の要求が変わる規模帯。"
            ),
            remedy="2階の宿泊室部分の床面積を平面図で確定させ、特定行政庁に確認。",
            data_gaps=["2階部分の宿泊室床面積"],
            evidences=ev,
        )

    return LicenseGateItem(
        gate_id="B2_fire_resistance",
        category="建物",
        title="法27条 耐火要求",
        status=GateStatus.PASS,
        blocking=True,
        hard=False,
        finding=f"{floors}階建 / 延床 {area:,.1f}㎡。法27条本則の規模に該当しません。",
        remedy="ただし防火地域・準防火地域の規定（法61条）は別途適用される。",
        evidences=ev,
    )


def _gate_b3_evacuation(project: ProjectInput) -> LicenseGateItem:
    """B3 避難・防火（令119/120/121/112 等）."""
    ev = cite("bsl_87")  # 用途変更時にこれらの規定が準用される根拠
    floors = project.floors_above or 0
    if floors <= 1:
        return LicenseGateItem(
            gate_id="B3_evacuation",
            category="建物",
            title="避難・防火（令119・120・121・112 等）",
            status=GateStatus.NOT_APPLICABLE,
            blocking=False,
            finding="平屋のため二方向避難・竪穴区画の主要規定は非該当の可能性が高い。",
            remedy="",
            evidences=ev,
        )
    return LicenseGateItem(
        gate_id="B3_evacuation",
        category="建物",
        title="避難・防火（令119・120・121・112 等）",
        status=GateStatus.CONDITIONAL,
        blocking=True,
        hard=False,
        finding=(
            f"地上{floors}階。用途変更確認申請の要否にかかわらず、"
            "廊下幅（令119）・歩行距離（令120）・二方向避難（令121）・"
            "竪穴区画と異種用途区画（令112）は宿泊用途として適用される。"
            "現況図面と実測がないと適合可否は確定しない。"
        ),
        remedy=(
            "平面図で①階別の宿泊室床面積②直通階段の数と位置③廊下の有効幅員"
            "④階段室の区画状況 を確認。不足があれば階段増設・区画壁の新設が発生する。"
        ),
        data_gaps=[
            "各階平面図（宿泊室の配置と床面積）",
            "直通階段の数・位置",
            "廊下の有効幅員（実測）",
            "階段室・吹抜けの区画状況",
        ],
        evidences=ev,
    )


def _gate_b4_fire_compliance(
    project: ProjectInput, research: Optional["ResearchResult"] = None
) -> LicenseGateItem:
    """B4 消防設備と消防法令適合通知書.

    国の省令が定める許可申請の添付書類は「構造設備を明らかにする図面」だけで、
    消防法令適合通知書は法令上の全国一律の添付書類ではない（施行規則1条2項）。
    ただし多くの自治体が審査基準・要綱でその提出を求めており、実務上は
    消防の適合確認が許可のボトルネックになるため、ゲートとして扱う。
    """
    area = project.floor_area_m2
    floors = project.floors_above or 0
    items = ["消火器", "自動火災報知設備", "誘導灯"]
    if area and area >= 500:
        items.append("消防機関へ通報する火災報知設備")
    if (area and area >= 3000) or floors >= 11:
        items.append("スプリンクラー設備")

    ev = cite("ryokan_rule_1", "minpaku_fire_class")
    if research:
        ev += research.evidences(["procedure"])

    return LicenseGateItem(
        gate_id="B4_fire_compliance",
        category="建物",
        title="消防設備・消防法令適合通知書",
        status=GateStatus.CONDITIONAL,
        blocking=True,
        hard=False,
        finding=(
            "宿泊用途（消防法令別表第一(5)項イ）として想定される主な設備："
            + "、".join(items)
            + "。なお、国の省令が定める許可申請の添付書類は"
            "『営業施設の構造設備を明らかにする図面』のみで、"
            "消防法令適合通知書は自治体の審査基準・要綱による運用要件。"
            "全国一律の法令上の要件ではないが、実務上ここが許可のボトルネックになる。"
        ),
        remedy=(
            "所轄消防本部（予防課）へ事前相談して必要設備を確定させる。"
            "設備工事の完了検査 → 適合通知書の交付 → 保健所へ許可申請、"
            "という順序になるため、消防の相談は建築の相談と**並行して**始める。"
        ),
        data_gaps=["既設の消防設備（自火報・誘導灯・消火器）の有無", "無窓階・地階の該否"],
        evidences=ev,
    )


# ---------------------------------------------------------------------------
# ゲートC：構造設備（旅館業法施行令1条）
# ---------------------------------------------------------------------------


def _gate_c1_room_area(
    project: ProjectInput, research: Optional["ResearchResult"]
) -> LicenseGateItem:
    """C1 客室面積。業態で基準がまったく違う."""
    bt = project.business_type
    local_ev = research.evidences(["room_area"]) if research else []
    local_cond = research.conditional_findings(["room_area"]) if research else []
    local_note = (
        "／".join(f"{f.get('title')}：{f.get('requirement')}" for f in local_cond)
        if local_cond
        else ""
    )

    if bt == BusinessType.SIMPLE_LODGING:
        ev = cite("ryokan_order_1_2") + local_ev
        area = project.floor_area_m2
        # 客室延床は延床そのものではない。共用部・水回りを除いた概算で見る。
        est_guest_area = area * 0.62 if area else None
        if est_guest_area is None:
            status, finding = (
                GateStatus.UNKNOWN,
                "延床面積が未入力のため、客室延床33㎡以上の要件を判定できません。",
            )
        elif est_guest_area >= 33:
            status = GateStatus.CONDITIONAL
            finding = (
                f"延床 {area:,.1f}㎡ から客室延床を概算 {est_guest_area:,.1f}㎡ と想定。"
                "簡易宿所の客室延床33㎡以上は満たせる規模。"
                "（宿泊者10人未満とする場合は 3.3㎡×人数 が下限）"
            )
        else:
            status = GateStatus.CONSULT
            finding = (
                f"延床 {area:,.1f}㎡ から客室延床を概算 {est_guest_area:,.1f}㎡。"
                "33㎡を下回る可能性があるため、宿泊者数を10人未満として"
                "3.3㎡×人数の基準で組み立てられるかを検討する必要がある。"
            )
        return LicenseGateItem(
            gate_id="C1_room_area",
            category="構造設備",
            title="客室面積（旅館業法施行令1条2項）",
            status=status,
            blocking=True,
            hard=False,
            finding=finding + (f" 自治体の上乗せ：{local_note}" if local_note else ""),
            remedy="平面図で客室として算入できる部分の内法面積を確定させる。",
            data_gaps=["客室として算入する部分の内法面積", "計画する宿泊者数"],
            evidences=ev,
        )

    # 旅館・ホテル営業
    ev = cite("ryokan_order_1_1") + local_ev
    rooms = getattr(project, "guest_room_count", None)
    area = project.floor_area_m2
    finding = (
        "旅館・ホテル営業は1客室7㎡以上（寝台を置く客室は9㎡以上）。"
        "面積は内法で算定するのが一般的で、壁芯では判定されない。"
    )
    gaps = ["各客室の内法面積", "寝台（ベッド）を置く客室かどうか"]
    if rooms and area:
        avg = area * 0.62 / rooms
        finding += (
            f" 概算：延床 {area:,.1f}㎡ ÷ {rooms}室（客室占有率0.62想定）＝"
            f"1室あたり約 {avg:,.1f}㎡。"
        )
        gaps = ["各客室の内法面積（概算ではなく実測・図面値）"]
    if local_note:
        finding += f" 自治体の上乗せ：{local_note}"
    return LicenseGateItem(
        gate_id="C1_room_area",
        category="構造設備",
        title="客室面積（旅館業法施行令1条1項）",
        status=GateStatus.CONDITIONAL,
        blocking=True,
        hard=False,
        finding=finding,
        remedy=(
            "1室7㎡を割る客室が出る場合、客室の統合か、簡易宿所営業への切替を検討する"
            "（簡易宿所には1室単位の面積下限がなく、客室延床33㎡以上で足りる）。"
        ),
        data_gaps=gaps,
        evidences=ev,
    )


def _gate_c2_front_desk(research: Optional["ResearchResult"]) -> LicenseGateItem:
    """C2 玄関帳場・ICT代替・無人運営."""
    ev = cite("ryokan_order_1_1")
    if research:
        ev += research.evidences(["front_desk"])
    blocking_local = research.blocking_findings(["front_desk"]) if research else []
    cond_local = research.conditional_findings(["front_desk"]) if research else []

    finding = (
        "2018年改正で『宿泊しようとする者との面接に適する玄関帳場**その他当該者の"
        "確認を適切に行うための設備**』となり、ICT代替（ビデオ通話・顔認証等＋"
        "緊急時の駆けつけ体制）が認められた。ただし運用要件の厳しさは自治体差が大きい。"
    )
    status = GateStatus.CONDITIONAL
    if blocking_local:
        status = GateStatus.CONSULT
        finding += " ⚠️ この自治体には無人運営を認めない／条件を強く絞る規定がある：" + "／".join(
            f.get("title", "") for f in blocking_local
        )
    elif cond_local:
        finding += " 自治体の条件：" + "／".join(
            f"{f.get('title')}：{f.get('requirement')}" for f in cond_local
        )

    return LicenseGateItem(
        gate_id="C2_front_desk",
        category="構造設備",
        title="玄関帳場・ICT代替（旅館業法施行令1条）",
        status=status,
        blocking=True,
        hard=False,
        finding=finding,
        remedy=(
            "無人運営を前提にするなら、保健所に①本人確認の方法②鍵の受渡し方法"
            "③駆けつけ時間と体制 を具体的に示して事前に可否を取る。"
            "運営体制は収支（人件費）に直結するため、計画の早い段階で確定させる。"
        ),
        data_gaps=["運営形態（有人常駐／ICT無人）", "駆けつけ拠点からの所要時間"],
        evidences=ev,
    )


def _gate_c3_facilities(research: Optional["ResearchResult"]) -> LicenseGateItem:
    """C3 入浴・便所・洗面・換気採光等."""
    ev = cite("ryokan_order_1_1")
    if research:
        ev += research.evidences(["facility"])
    cond_local = research.conditional_findings(["facility"]) if research else []
    extra = (
        " 自治体の上乗せ："
        + "／".join(f"{f.get('title')}：{f.get('requirement')}" for f in cond_local)
        if cond_local
        else ""
    )
    return LicenseGateItem(
        gate_id="C3_facilities",
        category="構造設備",
        title="入浴・便所・洗面・換気採光（旅館業法施行令1条）",
        status=GateStatus.CONDITIONAL,
        blocking=True,
        hard=False,
        finding=(
            "宿泊者の需要を満たす入浴設備（近接して公衆浴場がある場合を除く）、"
            "適当な数の便所・洗面設備、適当な換気・採光・照明・防湿・排水の設備が必要。"
            "便所の数は自治体条例で定員別のテーブルが定められていることが多い。" + extra
        ),
        remedy="住宅の水回りをそのまま使えるかを設計段階で確認。定員設定と便所数は連動する。",
        data_gaps=["便所・洗面の数と配置", "入浴設備の有無と規模", "計画定員"],
        evidences=ev,
    )


def _gate_d1_applicant() -> LicenseGateItem:
    """D1 申請者の欠格事由."""
    return LicenseGateItem(
        gate_id="D1_applicant",
        category="申請者",
        title="申請者の欠格事由（旅館業法3条2項各号）",
        status=GateStatus.UNKNOWN,
        blocking=False,  # 物件情報では判定できないため総合判定は動かさない
        hard=False,
        finding=(
            "破産手続開始の決定を受けて復権を得ない者、拘禁刑以上の刑から3年を経過しない者、"
            "許可取消しから3年を経過しない者、暴力団員等は許可を受けられない。"
            "法人の場合は役員が対象。物件側の情報では判定できません。"
        ),
        remedy="申請者（法人なら役員全員）について該当がないことを確認する。",
        data_gaps=["申請者・役員の欠格事由の該否"],
        evidences=cite("ryokan_law_3_2_disq"),
    )


# ---------------------------------------------------------------------------
# 総合判定
# ---------------------------------------------------------------------------


def _aggregate(gates: List[LicenseGateItem], business_label: str) -> Tuple[LicenseVerdict, str]:
    blocking = [g for g in gates if g.blocking]

    hard_fail = [g for g in blocking if g.status == GateStatus.FAIL and g.hard]
    if hard_fail:
        names = "／".join(g.title for g in hard_fail)
        return (
            LicenseVerdict.BLOCKED,
            f"{names} で不可。この物件で{business_label}の許可を取るルートは"
            "現実的にありません。別ルート（下記）を検討してください。",
        )

    soft_fail = [g for g in blocking if g.status == GateStatus.FAIL]
    if soft_fail:
        names = "／".join(g.title for g in soft_fail)
        return (
            LicenseVerdict.DIFFICULT,
            f"{names} が現状不適合。改修または計画変更を前提にすれば到達可能ですが、"
            "投資額と期間が大きくなります。",
        )

    consult = [g for g in blocking if g.status == GateStatus.CONSULT]
    if consult:
        names = "／".join(g.title for g in consult)
        return (
            LicenseVerdict.CONSULT,
            f"法令上の明確な不可要因はありませんが、{names} が行政判断に依存します。"
            "事前相談で見通しを取るまで投資判断を確定させないでください。",
        )

    unknown = [g for g in blocking if g.status == GateStatus.UNKNOWN]
    if len(unknown) >= 2:
        names = "／".join(g.title for g in unknown)
        return (
            LicenseVerdict.UNKNOWN,
            f"判定に必要な情報が不足しています（{names}）。"
            "不足情報を埋めれば自動で確定します。",
        )

    conditional = [g for g in blocking if g.status == GateStatus.CONDITIONAL]
    if conditional or unknown:
        return (
            LicenseVerdict.CONDITIONAL,
            f"立地の法令上の障害は確認されませんでした。{business_label}は、"
            f"{len(conditional)}項目の改修・手続きをクリアすれば取得可能な見込みです。",
        )

    return (
        LicenseVerdict.GRANTABLE,
        f"確認した全ゲートをクリア。{business_label}の許可取得の見込みがあります。",
    )


def _next_actions(j: LicenseJudgment) -> List[str]:
    """判定から「次にやること」を優先度順に組み立てる."""
    actions: List[str] = []

    # 1. 情報の穴を埋める（判定が動くもの優先）
    for g in j.gates:
        if g.status == GateStatus.UNKNOWN and g.blocking and g.data_gaps:
            actions.append(f"【情報取得】{g.title}：{g.data_gaps[0]}")

    # 2. 事前協議
    consult = [g for g in j.gates if g.status == GateStatus.CONSULT]
    if consult:
        who = "所轄保健所"
        if any(g.category == "立地" for g in consult):
            who = "所轄保健所＋自治体の都市計画課"
        actions.append(
            f"【事前相談】{who}へ相談。論点："
            + "／".join(g.title for g in consult[:3])
        )

    # 3. 不適合の解消
    for g in j.gates:
        if g.status == GateStatus.FAIL:
            actions.append(f"【要対応】{g.title}：{g.remedy or '対応方針の検討'}")

    # 4. 消防と建築の並行相談
    if any(g.gate_id == "B4_fire_compliance" for g in j.gates):
        actions.append("【並行相談】所轄消防本部（予防課）へ必要設備を確認")

    if j.unresolved:
        actions.append("【未確認】" + "／".join(j.unresolved[:3]))

    if not actions:
        actions.append("【申請準備】必要書類を揃えて保健所へ事前相談 → 許可申請")
    return actions[:8]


# ---------------------------------------------------------------------------
# 別ルート（簡易宿所・民泊）
# ---------------------------------------------------------------------------


def _alternatives(
    project: ProjectInput,
    geo: GeoLookupResult,
    zoning: ZoningJudgment,
    research: Optional["ResearchResult"],
    primary_verdict: LicenseVerdict,
    exclude: BusinessType,
) -> List[AlternativeRoute]:
    out: List[AlternativeRoute] = []

    if exclude != BusinessType.SIMPLE_LODGING:
        out.append(_alt_simple_lodging(project, zoning, primary_verdict))
    out.append(_alt_minpaku(project, geo, research))
    return out


def _alt_simple_lodging(
    project: ProjectInput, zoning: ZoningJudgment, primary_verdict: LicenseVerdict
) -> AlternativeRoute:
    """簡易宿所ルート。建基法上は同じ『ホテル又は旅館』なので立地判定は変わらない."""
    ev = cite("ryokan_law_2", "ryokan_order_1_2", "bsl_48")
    if zoning.level == JudgmentLevel.NO_GO:
        return AlternativeRoute(
            business_type=BusinessType.SIMPLE_LODGING,
            label="簡易宿所営業（旅館業許可）",
            verdict=LicenseVerdict.BLOCKED,
            summary=(
                "簡易宿所も建築基準法上は『ホテル又は旅館』に該当するため、"
                "用途地域の制限は旅館・ホテル営業とまったく同じ。"
                "立地で落ちている以上、簡易宿所に切り替えても解決しない。"
            ),
            blockers=["用途地域（建基法48条・別表第二）"],
            evidences=ev,
        )
    return AlternativeRoute(
        business_type=BusinessType.SIMPLE_LODGING,
        label="簡易宿所営業（旅館業許可）",
        verdict=LicenseVerdict.CONDITIONAL,
        summary=(
            "立地・建築の要件は旅館・ホテル営業と同一だが、**構造設備基準が緩い**。"
            "旅館・ホテル営業の『1室7㎡（寝台9㎡）以上』という1室単位の下限がなく、"
            "客室延床33㎡以上（宿泊者10人未満なら3.3㎡×人数）で足りる。"
            "小さい部屋が多い住宅・小規模物件では、こちらのほうが成立しやすい。"
        ),
        conditions=[
            "客室延床33㎡以上（宿泊者10人未満とする場合は3.3㎡×人数）",
            "立地・耐火・避難・消防の要件は旅館・ホテル営業と同じ",
            "自治体条例で『多数人共用でない客室』の面積割合を制限する例がある",
        ],
        evidences=ev,
    )


def _alt_minpaku(
    project: ProjectInput,
    geo: GeoLookupResult,
    research: Optional["ResearchResult"],
) -> AlternativeRoute:
    """民泊（住宅宿泊事業）ルート."""
    ev = cite("minpaku_law_2", "minpaku_law_18", "minpaku_zero_day_2026")
    if research:
        ev += research.evidences(["minpaku_ordinance"])

    conditions = [
        "年間提供日数は180日以内（法2条3項のハード上限）",
        "許可ではなく届出。建築基準法上は「住宅」扱いのため用途地域の制限は原則かからない",
        "家主不在型は住宅宿泊管理業者への委託が必要",
        "分譲マンションでは管理規約で禁止されていないことの確認が必須",
    ]
    blockers: List[str] = []
    verdict = LicenseVerdict.CONDITIONAL
    summary_parts = [
        "住宅宿泊事業は建築基準法上「住宅」のままなので、**用途地域で落ちた物件でも"
        "成立する可能性がある**のが最大の利点。用途変更確認申請も原則不要。"
    ]

    mp = (research.minpaku or {}) if research else {}
    status = str(mp.get("status") or "unknown")
    days_cap = mp.get("max_days_per_year")
    restriction = str(mp.get("restriction_summary") or "")

    if status == "prohibited":
        verdict = LicenseVerdict.BLOCKED
        blockers.append("自治体条例による住宅宿泊事業の禁止・実質ゼロ日規制")
        summary_parts.append(f"⛔ ただしこの自治体は条例で実施を禁止している：{restriction}")
    elif status == "restricted":
        verdict = LicenseVerdict.CONDITIONAL
        summary_parts.append(f"⚠️ この自治体は条例で区域・期間を制限している：{restriction}")
        if isinstance(days_cap, int) and 0 < days_cap < 180:
            conditions.insert(0, f"条例により年間 {days_cap} 日に短縮されている")
        elif isinstance(days_cap, int) and days_cap == 0:
            verdict = LicenseVerdict.BLOCKED
            blockers.append("条例により年間営業日数が0日")
    elif status == "allowed":
        summary_parts.append("この自治体では条例による区域・期間の制限は確認されなかった。")
    else:
        verdict = LicenseVerdict.UNKNOWN
        summary_parts.append(
            "⚠️ この自治体の条例を確認できていない。2026年7月の国の技術的助言により、"
            "条例で営業日数を実質ゼロにする『ゼロ日規制』が容認されたため、"
            "『旅館業がダメでも民泊がある』という前提は成立しない自治体がある。"
            "必ず最新の条例を確認すること。"
        )

    conditions.append(
        "宿泊室の床面積合計50㎡以下かつ家主が不在とならない場合、"
        "非常用照明器具の設置が届出住宅全体で適用除外になる（安全措置の手引き）。"
        "この線を越えるかどうかで初期投資が大きく変わる。"
    )
    ev += cite("minpaku_safety_50m2")

    return AlternativeRoute(
        business_type=BusinessType.MINPAKU,
        label="住宅宿泊事業（民泊・届出）",
        verdict=verdict,
        summary=" ".join(summary_parts),
        conditions=conditions,
        blockers=blockers,
        evidences=ev,
    )


def _judge_minpaku_as_primary(
    project: ProjectInput,
    geo: GeoLookupResult,
    municipality_key: Optional[str],
    municipality_name: str,
    research: Optional["ResearchResult"],
) -> LicenseJudgment:
    """民泊を主業態として選んだ場合の判定（許可ではなく届出）."""
    route = _alt_minpaku(project, geo, research)

    gates: List[LicenseGateItem] = [
        LicenseGateItem(
            gate_id="M1_zoning",
            category="立地",
            title="用途地域（住宅宿泊事業）",
            status=GateStatus.PASS,
            blocking=True,
            hard=True,
            finding=(
                "住宅宿泊事業は住宅を活用する事業のため、原則として用途地域にかかわらず"
                "実施できる。旅館業と異なり第一種低層住居専用地域でも可能。"
            ),
            remedy="",
            evidences=cite("minpaku_law_2"),
        ),
        LicenseGateItem(
            gate_id="M2_ordinance",
            category="立地",
            title="条例による区域・期間の制限（法18条）",
            status={
                LicenseVerdict.BLOCKED: GateStatus.FAIL,
                LicenseVerdict.CONDITIONAL: GateStatus.CONDITIONAL,
                LicenseVerdict.UNKNOWN: GateStatus.UNKNOWN,
            }.get(route.verdict, GateStatus.CONDITIONAL),
            blocking=True,
            hard=(route.verdict == LicenseVerdict.BLOCKED),
            finding=route.summary,
            remedy="自治体の住宅宿泊事業担当課へ、対象区域と実施可能期間を確認する。",
            data_gaps=["条例による区域・期間の制限の有無"],
            evidences=cite("minpaku_law_18", "minpaku_zero_day_2026")
            + (research.evidences(["minpaku_ordinance"]) if research else []),
        ),
        LicenseGateItem(
            gate_id="M3_days",
            category="事業条件",
            title="年間提供日数180日上限",
            status=GateStatus.CONDITIONAL,
            blocking=False,
            finding=(
                "人を宿泊させる日数は1年間で180日を超えられない（法2条3項）。"
                "条例でさらに短縮されている場合はそちらが優先。"
                "残りの期間の収益をどう埋めるか（マンスリー・賃貸併用等）が事業性の鍵。"
            ),
            remedy="",
            evidences=cite("minpaku_law_2"),
        ),
        LicenseGateItem(
            gate_id="M4_safety",
            category="構造設備",
            title="安全措置（非常用照明・避難経路表示等）",
            status=GateStatus.CONDITIONAL,
            blocking=True,
            hard=False,
            finding=(
                "宿泊室の床面積合計が50㎡以下、かつ家主が不在とならない場合は、"
                "非常用照明器具が届出住宅全体で適用除外になる。"
                "これを越えると宿泊室と避難経路に非常用照明が必要になり、"
                "消防設備の要求も重くなる。"
            ),
            remedy=(
                "宿泊室の合計面積と家主居住／不在の別を先に決める。"
                "50㎡以下＋家主居住に収める設計にできれば初期投資を大きく圧縮できる。"
            ),
            data_gaps=["宿泊室の床面積合計", "家主居住型か家主不在型か"],
            evidences=cite("minpaku_safety_50m2", "minpaku_fire_class"),
        ),
        LicenseGateItem(
            gate_id="M5_management",
            category="申請者",
            title="届出・管理業者委託・管理規約",
            status=GateStatus.CONDITIONAL,
            blocking=False,
            finding=(
                "家主不在型は住宅宿泊管理業者への委託が必要。"
                "分譲マンションでは管理規約で民泊が禁止されていないことの確認が必須。"
            ),
            remedy="管理規約を確認し、必要なら管理業者を選定する。",
            data_gaps=["家主居住／不在の別", "分譲マンションの場合の管理規約"],
            evidences=cite("minpaku_law_2"),
        ),
    ]

    verdict, headline = _aggregate(gates, BUSINESS_LABEL[BusinessType.MINPAKU])

    judgment = LicenseJudgment(
        business_type=BusinessType.MINPAKU,
        business_label=BUSINESS_LABEL[BusinessType.MINPAKU],
        verdict=verdict,
        headline=headline,
        gates=gates,
        alternatives=[],
        municipality_key=municipality_key,
        municipality_name=municipality_name
        or (research.municipality_name if research else ""),
        permit_authority=(research.permit_authority if research else ""),
        research_summary=(research.summary if research and research.available else ""),
        research_note=_research_note(research),
        researched_on=(research.researched_on if research else ""),
        unresolved=(list(research.unresolved) if research else []),
    )
    judgment.next_actions = _next_actions(judgment)
    return judgment
