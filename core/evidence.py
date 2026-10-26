"""根拠（Evidence）の型と、一次情報レジストリ.

このアプリの判定は「結論」ではなく「根拠つきの結論」を返すことを目的にする。
そのため全ての判定項目は Evidence を1件以上ぶら下げる。

Evidence の出所は2系統ある：
  1. 本ファイルの PRIMARY_SOURCES … 全国共通の法律・政令・省令・国の通知。
     人手で一次情報を確認して埋め込んだ「正本」。LLM は関与しない。
  2. core/legal_research.py … 自治体条例・手引き等。Claude が web 検索で調べ、
     出典URLと引用が取れたものだけを verified=True で返す。

判定ロジックが参照してよいのは verified=True の Evidence のみ。
未検証（出典URLなし・引用なし）のものは表示はするが判定には使わない。
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SourceTier(str, Enum):
    """出典の階層。上ほど強い."""

    LAW = "law"  # 法律
    CABINET_ORDER = "cabinet_order"  # 政令（施行令）
    MINISTRY_ORDER = "ministry_order"  # 省令（施行規則）
    NOTICE = "notice"  # 国の通知・技術的助言・ガイドライン
    ORDINANCE = "ordinance"  # 自治体条例
    GUIDE = "guide"  # 自治体の手引き・FAQ・審査基準
    UNVERIFIED = "unverified"  # 出典が取れなかったもの（判定に使わない）


TIER_LABEL: Dict[str, str] = {
    SourceTier.LAW: "法律",
    SourceTier.CABINET_ORDER: "政令",
    SourceTier.MINISTRY_ORDER: "省令",
    SourceTier.NOTICE: "国の通知・ガイドライン",
    SourceTier.ORDINANCE: "自治体条例",
    SourceTier.GUIDE: "自治体の手引き",
    SourceTier.UNVERIFIED: "未検証",
}


class Evidence(BaseModel):
    """判定1件を支える根拠."""

    source_tier: SourceTier = SourceTier.UNVERIFIED
    law_name: str = ""  # 「旅館業法」「目黒区旅館業法施行条例」等
    article: str = ""  # 「第3条第2項」「第1条第1項第1号」等
    quote: str = ""  # 条文・手引きからの引用（要約ではなく原文）
    url: str = ""  # 出典URL
    publisher: str = ""  # e-Gov / 厚生労働省 / 目黒区 等
    checked_on: str = ""  # YYYY-MM-DD（一次情報を確認した日）
    note: str = ""  # 補足（適用条件・読み方）

    @property
    def verified(self) -> bool:
        """検証済みか.

        条件は3つとも必要：
          - 出典URLがある
          - 原文の引用がある
          - source_tier が UNVERIFIED でない

        3つ目が要るのは、URLと引用が揃っていても出所が公的でない場合
        （個人ブログ・解説記事など）があるため。調査層は信頼ドメインで
        なければ tier を UNVERIFIED に落とすので、ここで弾かれる。
        """
        return (
            bool(self.url)
            and bool(self.quote)
            and self.source_tier != SourceTier.UNVERIFIED
        )

    @property
    def label(self) -> str:
        base = f"{self.law_name} {self.article}".strip()
        return base or self.publisher or "出典未特定"

    def to_markdown(self) -> str:
        head = f"**{self.label}**（{TIER_LABEL.get(self.source_tier, '—')}）"
        if not self.verified:
            head += " ⚠️未検証"
        lines = [head]
        if self.quote:
            lines.append(f"> {self.quote}")
        meta = []
        if self.publisher:
            meta.append(self.publisher)
        if self.checked_on:
            meta.append(f"確認日 {self.checked_on}")
        if self.url:
            meta.append(f"[出典]({self.url})")
        if meta:
            lines.append("　".join(meta))
        if self.note:
            lines.append(f"_{self.note}_")
        return "\n\n".join(lines)


def unverified(law_name: str, article: str = "", note: str = "") -> Evidence:
    """出典が取れていないことを明示する Evidence."""
    return Evidence(
        source_tier=SourceTier.UNVERIFIED,
        law_name=law_name,
        article=article,
        note=note or "出典URL・条文引用が取得できていません。判定には使用していません。",
    )


# ---------------------------------------------------------------------------
# 一次情報レジストリ（全国共通・人手で確認済み）
#
# 追加・修正するときのルール：
#   - quote は必ず原文から。要約を quote に入れない（要約は note へ）。
#   - url は条文そのものが読める URL（e-Gov / 所管省庁）。
#   - checked_on は実際に開いて確認した日付を入れる。
# ---------------------------------------------------------------------------

_EGOV_RYOKAN_LAW = "https://laws.e-gov.go.jp/law/323AC0000000138"
_EGOV_RYOKAN_ORDER = "https://laws.e-gov.go.jp/law/332CO0000000152"
_EGOV_BSL = "https://laws.e-gov.go.jp/law/325AC0000000201"
_EGOV_MINPAKU_LAW = "https://laws.e-gov.go.jp/law/429AC0000000065"
_CHECKED = "2026-08-31"


PRIMARY_SOURCES: Dict[str, Evidence] = {
    # -- 旅館業法 -------------------------------------------------------
    "ryokan_law_2": Evidence(
        source_tier=SourceTier.LAW,
        law_name="旅館業法",
        article="第2条（定義）",
        quote=(
            "この法律で「旅館業」とは、旅館・ホテル営業、簡易宿所営業及び下宿営業をいう。"
            "／「簡易宿所営業」とは、宿泊する場所を多数人で共用する構造及び設備を主とする"
            "施設を設け、宿泊料を受けて、人を宿泊させる営業で、下宿営業以外のものをいう。"
        ),
        url=_EGOV_RYOKAN_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note="「宿泊」は寝具を使用して施設を利用すること（同条第5項）。",
    ),
    "ryokan_law_3_1": Evidence(
        source_tier=SourceTier.LAW,
        law_name="旅館業法",
        article="第3条第1項（営業の許可）",
        quote=(
            "旅館業を経営しようとする者は、都道府県知事の許可を受けなければならない。"
        ),
        url=_EGOV_RYOKAN_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note="保健所設置市・特別区では市長・区長が許可権者。申請先は所轄保健所。",
    ),
    "ryokan_law_3_2": Evidence(
        source_tier=SourceTier.LAW,
        law_name="旅館業法",
        article="第3条第2項（不許可事由）",
        quote=(
            "都道府県知事は、前項の許可の申請に係る施設の構造設備が政令で定める基準に"
            "適合しないと認めるとき、当該施設の設置場所が公衆衛生上不適当であると認めるとき、"
            "又は申請者が次の各号のいずれかに該当するときは、同項の許可を与えないことができる。"
        ),
        url=_EGOV_RYOKAN_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "不許可事由は「①構造設備が政令基準に不適合」「②設置場所が公衆衛生上不適当」"
            "「③申請者の欠格事由」の3系統。本アプリのゲートC・A4・Dがそれぞれ対応する。"
        ),
    ),
    "ryokan_law_3_2_disq": Evidence(
        source_tier=SourceTier.LAW,
        law_name="旅館業法",
        article="第3条第2項各号（申請者の欠格事由）",
        quote=(
            "心身の故障により旅館業を適正に行うことができない者／破産手続開始の決定を受けて"
            "復権を得ない者／拘禁刑以上の刑に処せられ、その執行を終わり、又は執行を受けることが"
            "なくなつた日から起算して三年を経過していない者／許可を取り消され、取消しの日から"
            "起算して三年を経過していない者／暴力団員等 等"
        ),
        url=_EGOV_RYOKAN_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note="法人の場合は役員が対象。物件側の情報では判定できないため申請者本人への確認事項。",
    ),
    "ryokan_law_3_3": Evidence(
        source_tier=SourceTier.LAW,
        law_name="旅館業法",
        article="第3条第3項（学校等の周辺）",
        quote=(
            "都道府県知事は、第一項の許可の申請に係る施設の設置場所が、学校教育法第一条に"
            "規定する学校（大学を除く。）、就学前の子どもに関する教育、保育等の総合的な提供の"
            "推進に関する法律第二条第七項に規定する幼保連携型認定こども園、児童福祉法第七条"
            "第一項に規定する児童福祉施設、社会教育法第二条に規定する社会教育に関する施設"
            "その他これらに類する施設の敷地の周囲おおむね百メートルの区域内にある場合において、"
            "その設置によつて当該施設の清純な施設環境が著しく害されるおそれがあると認めるときは、"
            "同項の許可を与えないことができる。"
        ),
        url=_EGOV_RYOKAN_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "「100m以内なら不許可」ではなく「清純な施設環境が著しく害されるおそれ」があるとき。"
            "実務上は第4項の意見聴取を経て条件付きで許可されるケースが多いが、"
            "自治体条例で対象施設・距離を上乗せしている場合があるため要確認。"
        ),
    ),
    "ryokan_law_3_4": Evidence(
        source_tier=SourceTier.LAW,
        law_name="旅館業法",
        article="第3条第4項（意見聴取）",
        quote=(
            "都道府県知事は、前項の規定による許可を与える場合には、あらかじめ、"
            "当該施設の所在地の管轄する学校の長等の意見を求めなければならない。"
        ),
        url=_EGOV_RYOKAN_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note="学校長・教育委員会・児童福祉行政庁等への意見照会。標準処理期間が延びる要因。",
    ),
    # -- 旅館業法施行令 -------------------------------------------------
    "ryokan_order_1_1": Evidence(
        source_tier=SourceTier.CABINET_ORDER,
        law_name="旅館業法施行令",
        article="第1条第1項（旅館・ホテル営業の構造設備の基準）",
        quote=(
            "一客室の床面積は、七平方メートル（寝台を置く客室にあつては、九平方メートル）"
            "以上であること。／宿泊しようとする者との面接に適する玄関帳場その他当該者の"
            "確認を適切に行うための設備であつて厚生労働省令で定める基準に適合するものを"
            "有すること。／適当な換気、採光、照明、防湿及び排水の設備を有すること。／"
            "当該施設に近接して公衆浴場がある等入浴に支障を来さないと認められる場合を除き、"
            "宿泊者の需要を満たすことができる規模の入浴設備を有すること。"
        ),
        url=_EGOV_RYOKAN_ORDER,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "玄関帳場は2018年改正で「その他当該者の確認を適切に行うための設備」が加わり、"
            "ICT代替（ビデオ通話・顔認証等＋駆けつけ体制）が可能になった。運用要件は自治体差が大きい。"
        ),
    ),
    "ryokan_order_1_2": Evidence(
        source_tier=SourceTier.CABINET_ORDER,
        law_name="旅館業法施行令",
        article="第1条第2項（簡易宿所営業の構造設備の基準）",
        quote=(
            "客室の延床面積は、三十三平方メートル（法第三条第一項の許可の申請に当たつて"
            "宿泊者の数を十人未満とする場合には、三・三平方メートルに当該宿泊者の数を"
            "乗じて得た面積）以上であること。／階層式寝台を有する場合には、上段と下段の"
            "間隔は、おおむね一メートル以上であること。"
        ),
        url=_EGOV_RYOKAN_ORDER,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "簡易宿所には旅館・ホテル営業のような「1室7㎡（寝台9㎡）」の1室単位の下限がない。"
            "小規模物件では簡易宿所のほうが構造設備基準を満たしやすい。"
        ),
    ),
    "ryokan_rule_1": Evidence(
        source_tier=SourceTier.MINISTRY_ORDER,
        law_name="旅館業法施行規則",
        article="第1条（許可の申請）",
        quote=(
            "法第三条第一項の規定により許可を受けようとする者は、次に掲げる事項を記載した"
            "申請書を、その営業施設所在地を管轄する都道府県知事（保健所を設置する市又は"
            "特別区にあつては、市長又は区長。）に提出しなければならない。／"
            "前項の申請書には、営業施設の構造設備を明らかにする図面を添付しなければならない。"
        ),
        url="https://www.mhlw.go.jp/web/t_doc?dataId=79090000&dataType=0&pageNo=1",
        publisher="厚生労働省 法令等データベース",
        checked_on=_CHECKED,
        note=(
            "国の省令が定める添付書類は『構造設備を明らかにする図面』のみ。"
            "消防法令適合通知書の提出は多くの自治体が審査基準・要綱で求める"
            "**運用上の要件**であり、全国一律の法令上の添付書類ではない点に注意。"
            "ただし実務上は消防の適合確認が許可のボトルネックになる。"
        ),
    ),
    # -- 建築基準法 -----------------------------------------------------
    "bsl_48": Evidence(
        source_tier=SourceTier.LAW,
        law_name="建築基準法",
        article="第48条・別表第二（用途地域内の建築物の制限）",
        quote=(
            "第一種低層住居専用地域内においては、別表第二（い）項に掲げる建築物以外の"
            "建築物は、建築してはならない。ただし、特定行政庁が第一種低層住居専用地域に"
            "おける良好な住居の環境を害するおそれがないと認め、又は公益上やむを得ないと"
            "認めて許可した場合においては、この限りでない。"
        ),
        url=_EGOV_BSL,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "「ホテル又は旅館」は第一種住居地域で3,000㎡以下に限り可、"
            "第二種住居・準住居・近隣商業・商業・準工業で可。"
            "低層/中高層住居専用・田園住居・工業・工業専用では本則不可（48条ただし書きの許可を除く）。"
            "旅館業法上の簡易宿所営業も建基法上は「ホテル又は旅館」に該当する。"
        ),
    ),
    "bsl_27": Evidence(
        source_tier=SourceTier.LAW,
        law_name="建築基準法",
        article="第27条第1項第1号（耐火建築物等としなければならない特殊建築物）",
        quote=(
            "次の各号のいずれかに該当する特殊建築物は、その特定主要構造部を〔中略〕"
            "政令で定める技術的基準に適合するもの〔中略〕としなければならない。／"
            "一　別表第一（ろ）欄に掲げる階を同表（い）欄（一）項から（四）項までに"
            "掲げる用途に供するもの（**階数が三で延べ面積が二百平方メートル未満のもの**"
            "（同表（ろ）欄に掲げる階を同表（い）欄（二）項に掲げる用途で政令で定めるものに"
            "供するものにあつては、**政令で定める技術的基準に従つて警報設備を設けたものに限る。**）"
            "を除く。）"
        ),
        url=_EGOV_BSL,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "平成30年改正（令和元年6月25日施行）により、階数3・延べ面積200㎡未満の"
            "就寝利用建築物（ホテル・旅館を含む）は、警報設備の設置により耐火建築物等と"
            "しなくてよい扱いになった。小規模な3階建て木造の宿泊転用で最も効く緩和。"
        ),
    ),
    "bsl_27_relax_2019": Evidence(
        source_tier=SourceTier.NOTICE,
        law_name="平成30年改正建築基準法（令和元年6月25日施行）",
        article="法27条1項ただし書・関係告示",
        quote=(
            "法第27条第1項の規定に基づく3階建・200㎡未満の建築物であって耐火構造と"
            "しないものについては、建築物の利用状況に応じて、就寝利用する建築物の場合は"
            "警報設備の設置が必要となります。"
        ),
        url="https://www.fdma.go.jp/laws/tutatsu/items/190624_yobou-syoukyu_56-81.pdf",
        publisher="消防庁 消防・救急課長通知（消防消第81号・消防予第56号）",
        checked_on=_CHECKED,
        note=(
            "階数3・延べ200㎡未満なら、耐火建築物等とせず警報設備で対応できる。"
            "木造3階建ての小規模宿泊転用ではこの緩和の適用可否が投資額を大きく左右する。"
            "適用には告示仕様（感知器の種別・警戒区域・非常電源等）の充足が必要。"
        ),
    ),
    "bsl_87": Evidence(
        source_tier=SourceTier.LAW,
        law_name="建築基準法",
        article="第87条（用途の変更に対する準用）",
        quote=(
            "建築物の用途を変更して第六条第一項第一号の特殊建築物のいずれかとする場合"
            "（当該用途の変更が政令で指定する類似の用途相互間におけるものである場合を除く。）"
            "においては、同条（第三項、第五項及び第六項を除く。）、第六条の二（第三項を除く。）、"
            "第六条の四（第一項第一号及び第二号の建築物に係る部分に限る。）、第七条第一項"
            "並びに第十八条第一項から第四項まで及び第十五項から第二十項までの規定を準用する。"
        ),
        url=_EGOV_BSL,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "法6条1項1号の特殊建築物は、2019年6月の改正で床面積が100㎡超→200㎡超に拡大。"
            "したがって用途変更部分が200㎡以下なら確認申請は不要。"
            "ただし確認申請が不要でも、建基法・消防法の実体規定は適用される（違反は違反）。"
        ),
    ),
    "bsl_68_2": Evidence(
        source_tier=SourceTier.LAW,
        law_name="建築基準法",
        article="第68条の2第1項（地区計画等の区域内における制限）",
        quote=(
            "市町村は、地区計画等の区域（地区整備計画、特定建築物地区整備計画、"
            "防災街区整備地区整備計画、歴史的風致維持向上地区整備計画、沿道地区整備計画"
            "又は集落地区整備計画〔中略〕が定められている区域に限る。）内において、"
            "建築物の敷地、構造、建築設備又は**用途**に関する事項で当該地区計画等の内容として"
            "定められたものを、条例で、これらに関する制限として定めることができる。"
        ),
        url=_EGOV_BSL,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "地区計画は用途地域の上に重ねて『用途』を制限できる。"
            "用途地域上はホテル可でも、地区計画に基づく市町村の条例でホテル・旅館が"
            "制限されている区域がある。制限の中身は自治体の条例にしか書かれていないため、"
            "自治体の都市計画課への確認（または条例の調査）が必要。"
        ),
    ),
    "city_planning_34": Evidence(
        source_tier=SourceTier.LAW,
        law_name="都市計画法",
        article="第34条（市街化調整区域における開発許可の基準）",
        quote=(
            "前条の規定にかかわらず、市街化調整区域に係る開発行為（主として第二種特定工作物の"
            "建設の用に供する目的で行う開発行為を除く。）については、当該申請に係る開発行為及び"
            "その申請の手続が同条に定める要件に該当するほか、当該申請に係る開発行為が次の各号の"
            "いずれかに該当すると認める場合でなければ、都道府県知事は、開発許可をしてはならない。"
        ),
        url="https://laws.e-gov.go.jp/law/343AC0000000100",
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "市街化調整区域では、各号のいずれかに該当しない限り開発許可が下りない。"
            "旅館・ホテルは原則としてこれに該当しないため、標準的なフローでは不可扱い。"
        ),
    ),
    # -- 住宅宿泊事業法 -------------------------------------------------
    "minpaku_law_2": Evidence(
        source_tier=SourceTier.LAW,
        law_name="住宅宿泊事業法",
        article="第2条第3項（住宅宿泊事業の定義）",
        quote=(
            "この法律において「住宅宿泊事業」とは、旅館業法第三条の二第一項に規定する"
            "旅館業を営む者以外の者が宿泊料を受けて住宅に人を宿泊させる事業であつて、"
            "人を宿泊させる日数が一年間で百八十日を超えないものをいう。"
        ),
        url=_EGOV_MINPAKU_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "許可ではなく届出。年間提供日数の上限180日は法律上のハード上限で、"
            "自治体が条例でさらに短縮できる（法18条）。"
        ),
    ),
    "minpaku_law_18": Evidence(
        source_tier=SourceTier.LAW,
        law_name="住宅宿泊事業法",
        article="第18条（区域・期間の制限）",
        quote=(
            "都道府県は、住宅宿泊事業に起因する騒音の発生その他の事象による生活環境の"
            "悪化を防止するため必要があるときは、合理的に必要と認められる限度において、"
            "政令で定める基準に従い条例で定めるところにより、区域を定めて、住宅宿泊事業を"
            "実施する期間を制限することができる。"
        ),
        url=_EGOV_MINPAKU_LAW,
        publisher="e-Gov法令検索",
        checked_on=_CHECKED,
        note=(
            "民泊は建築基準法上「住宅」扱いのため用途地域の制限はほぼ効かないが、"
            "代わりにこの条例が実質的な可否を決める。住居専用地域を平日禁止にする例が多い。"
        ),
    ),
    "minpaku_zero_day_2026": Evidence(
        source_tier=SourceTier.NOTICE,
        law_name="住宅宿泊事業法に規定する届出住宅に係るゼロ日規制等について（技術的助言）",
        article="令和8年7月15日 観光庁・国土交通省・厚生労働省",
        quote=(
            "住宅宿泊事業法に規定する届出住宅に係るゼロ日規制、ICTを用いた管理の"
            "義務付け等について、地方自治法に基づく技術的助言を行うもの。"
        ),
        url="https://www.mlit.go.jp/kankocho/news06_00067.html",
        publisher="観光庁 報道発表",
        checked_on=_CHECKED,
        note=(
            "⚠️重要な方針転換。自治体が条例で営業日数を実質ゼロ（＝新規禁止）にすることを"
            "国が容認した。『旅館業がダメでも民泊があるから大丈夫』という従来の前提は"
            "自治体によっては成立しない。民泊ルートは必ず最新の条例を確認すること。"
        ),
    ),
    "minpaku_safety_50m2": Evidence(
        source_tier=SourceTier.NOTICE,
        law_name="民泊の安全措置の手引き（住宅宿泊事業法における民泊の適正な事業実施のために）",
        article="1.（1）非常用照明器具について（告示第一）",
        quote=(
            "（建て方に関わらず）宿泊室の床面積の合計が50㎡以下、かつ家主が不在と"
            "ならない（一時的な不在を除く。）。→ 届出住宅全体で適用不要"
        ),
        url="https://www.mlit.go.jp/kankocho/minpaku/content/001368071.pdf",
        publisher="国土交通省（平成29年12月26日策定・令和6年4月1日最終改訂）",
        checked_on=_CHECKED,
        note=(
            "民泊の安全措置（非常用照明器具）の適用除外ライン。"
            "『宿泊室50㎡以下 かつ 家主居住』が民泊の設備投資を最も大きく左右する分岐で、"
            "同じ切り口が消防法令上の防火対象物区分でも使われる（下記 minpaku_fire_class 参照）。"
        ),
    ),
    "minpaku_fire_class": Evidence(
        source_tier=SourceTier.UNVERIFIED,
        law_name="民泊における消防法令上の取扱い等（消防庁）",
        article="令別表第一(5)項イ／住宅の区分",
        quote="",  # 原文引用が取得できていない＝判定には使わない
        url="https://www.fdma.go.jp/mission/prevention/suisin/post20.html",
        publisher="総務省消防庁",
        checked_on=_CHECKED,
        note=(
            "家主居住型で宿泊室の床面積合計が一定規模以下なら消防法令上『住宅』扱い、"
            "それ以外は令別表第一(5)項イ（旅館・ホテル等）扱いとされている。"
            "⚠️ 該当リーフレットが画像PDFのため条文の原文引用を取得できておらず、"
            "本アプリでは未検証として扱い判定には使用していない。"
            "設備要件は所轄消防本部への事前相談で確定させること。"
        ),
    ),
}


def cite(*keys: str) -> List[Evidence]:
    """PRIMARY_SOURCES からキー指定で Evidence を取り出す.

    未登録キーは黙って落とさず、未検証 Evidence として返す（欠落の可視化）。
    """
    out: List[Evidence] = []
    for k in keys:
        ev = PRIMARY_SOURCES.get(k)
        if ev is None:
            out.append(unverified(law_name=f"未登録の根拠キー: {k}"))
        else:
            out.append(ev)
    return out


def verified_only(evidences: List[Evidence]) -> List[Evidence]:
    return [e for e in evidences if e.verified]


def evidence_confidence(evidences: List[Evidence]) -> str:
    """根拠の強さから信頼度ラベルを作る."""
    if not evidences:
        return "低"
    ver = verified_only(evidences)
    if not ver:
        return "低"
    tiers = {e.source_tier for e in ver}
    hard = {SourceTier.LAW, SourceTier.CABINET_ORDER, SourceTier.MINISTRY_ORDER}
    if tiers & hard:
        return "高" if len(ver) == len(evidences) else "中"
    return "中" if len(ver) == len(evidences) else "低"
