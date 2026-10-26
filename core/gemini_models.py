"""Gemini のモデル名を1箇所で解決する層.

## なぜこのファイルがあるか

2026-09-07、書類読み取りが「解析はできたがフィールド0件・信頼度0.00」になっていた。
原因は精度ではなく **モデル名の廃止**：

    404 NOT_FOUND: This model models/gemini-2.0-flash is no longer available.
    Please update your code to use models/gemini-3.6-flash

コードのあちこちにモデル名がハードコードされていたため、Google側の世代交代で全滅した。
同じことを繰り返さないために、この層が3段構えで解決する：

  1. **候補リスト**（環境変数で上書き可）
  2. **エラーからの自己修復**：404本文の「use models/X」を読んで X を次に試す
  3. **実在モデルの照会**：`client.models.list()` から generateContent 可能なものを取り、
     用途（pro寄り／flash寄り）でランク付けして試す

これでモデル名が変わっても、コードを直さずに追随できる。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 既定の候補。**2026-09-07 に公式ドキュメントで実在を確認した名前**を置く
#   （確認元: https://ai.google.dev/gemini-api/docs/models
#    stable = 3.8/3.7/3.6/3.5-flash・2.5-pro。gemini-2.0-flash は停止済み。
#    3.x に stable な pro は無く、pro は 2.5 系と 3.1-pro-preview のみ）
# ここが古くなっても discover() が実在モデルを拾い直すので止まらない。
DEFAULT_QUALITY_CHAIN: Tuple[str, ...] = (
    "gemini-3.8-flash",   # 現行で最も賢い flash（書類の表組み読取もこれが第一候補）
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-2.5-pro",     # 別系統の保険
)
DEFAULT_FAST_CHAIN: Tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
)

# 除外したいモデル（用途が違う・生成に使えない）
_EXCLUDE_TOKENS = (
    "embedding", "aqa", "imagen", "veo", "tts", "image",       # image=画像生成系
    "native-audio", "live", "learnlm", "transcribe", "omni",
)


def _chain_from_env(var: str) -> List[str]:
    """`GEMINI_MODEL_CHAIN=a,b,c` のように環境変数でチェーンを固定できる."""
    raw = os.getenv(var, "")
    return [m.strip() for m in raw.split(",") if m.strip()]


def candidate_chain(purpose: str = "quality", primary: Optional[str] = None) -> List[str]:
    """試すモデルの順番を返す.

    Args:
        purpose: "quality"（読み取り・調査＝精度優先）/ "fast"（チャット等）
        primary: 呼び出し側が明示指定したモデル（環境変数由来など）を先頭に置く
    """
    chain: List[str] = []
    if primary:
        chain.append(primary)
    chain += _chain_from_env("GEMINI_MODEL_CHAIN")
    base = DEFAULT_QUALITY_CHAIN if purpose == "quality" else DEFAULT_FAST_CHAIN
    chain += list(base)
    # 重複を除きつつ順序を保つ
    seen = set()
    out = []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def suggested_from_error(error: Any) -> Optional[str]:
    """404本文の「Please update your code to use models/X」から X を取り出す.

    Google はモデル廃止時に後継を教えてくれる。それを機械的に拾って次に試す。
    """
    msg = str(error or "")
    m = re.search(r"use\s+models/([A-Za-z0-9._\-]+)", msg)
    if m:
        return m.group(1)
    # 「models/X is no longer available」だけのケースは後継が分からない
    return None


def is_model_not_found(error: Any) -> bool:
    msg = str(error or "").lower()
    return (
        "404" in msg
        or "not_found" in msg
        or "no longer available" in msg
        or "is not found" in msg
    )


def rank_models(names: Sequence[str], purpose: str = "quality") -> List[str]:
    """実在モデル名を用途に合わせて並べる（純粋関数・テスト対象）.

    - **世代番号を最優先**（gemini-3.8 > gemini-2.5）。世代差のほうが効くため、
      「新しい flash」を「古い pro」より先に試す
    - 同世代なら用途で選ぶ（quality=pro寄り／fast=flash寄り）
    - preview / exp / lite は後回し、埋め込み・画像生成・音声系は除外
    """
    def version_of(name: str) -> float:
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        return float(m.group(1)) if m else 0.0

    def score(name: str) -> tuple:
        low = name.lower()
        is_pro = "pro" in low
        is_flash = "flash" in low
        wanted = is_pro if purpose == "quality" else is_flash
        other = is_flash if purpose == "quality" else is_pro
        penalty = any(t in low for t in ("preview", "-exp", "experimental", "lite"))
        # 大きいほど先に来るように（降順ソート）
        return (
            version_of(name),          # ① 世代
            1 if wanted else 0,        # ② 用途に合う系統
            1 if other else 0,
            0 if penalty else 1,       # ③ 安定版
            -len(name),                # ④ 同条件なら短い名前
        )

    usable = [
        n for n in names
        if n and not any(t in n.lower() for t in _EXCLUDE_TOKENS)
    ]
    return sorted(usable, key=score, reverse=True)


def discover(client: Any, purpose: str = "quality") -> List[str]:
    """API に実在するモデルを問い合わせて、用途順に並べて返す.

    照会に失敗したら空リスト（呼び出し側は候補リストで続行する）。
    """
    try:
        raw = list(client.models.list())
    except Exception as exc:  # noqa: BLE001 - 権限・ネットワーク
        logger.warning(f"モデル一覧の照会に失敗: {exc}")
        return []

    names: List[str] = []
    for m in raw:
        name = str(getattr(m, "name", "") or "").replace("models/", "")
        if not name:
            continue
        actions = (
            getattr(m, "supported_actions", None)
            or getattr(m, "supported_generation_methods", None)
            or []
        )
        if actions and not any("generatecontent" == str(a).lower().replace("_", "") or
                               "generatecontent" in str(a).lower().replace("_", "")
                               for a in actions):
            continue
        names.append(name)
    ranked = rank_models(names, purpose=purpose)
    if ranked:
        logger.info(f"利用可能なGeminiモデル（上位5件）: {ranked[:5]}")
    return ranked


def call_with_fallback(
    client: Any,
    run: Callable[[str], Any],
    purpose: str = "quality",
    primary: Optional[str] = None,
    max_models: int = 6,
    also_retry: Optional[Callable[[Exception], bool]] = None,
) -> Tuple[Any, str, List[str]]:
    """モデルを順に試して最初に成功した結果を返す.

    Args:
        run: モデル名を受け取って API を叩く関数
        also_retry: True を返した例外は「次のモデルを試す」対象にする
                    （空応答・JSON崩れなど、モデルを変えれば直る類の失敗）
    Returns:
        (結果, 使えたモデル名, 試したモデル名の一覧)
    Raises:
        最後のエラー（全滅時）
    """
    queue = candidate_chain(purpose=purpose, primary=primary)
    tried: List[str] = []
    discovered = False
    last_error: Optional[Exception] = None

    while queue and len(tried) < max_models:
        model = queue.pop(0)
        if model in tried:
            continue
        tried.append(model)
        try:
            return run(model), model, tried
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if not is_model_not_found(exc):
                # 呼び出し側が「これも次モデルで試したい」と言った失敗だけ続行する
                if also_retry is not None and also_retry(exc):
                    logger.info(f"{model} で失敗（{exc}）。次のモデルを試します")
                    continue
                raise  # モデル名以外の失敗（認証・レート等）は呼び出し側に任せる

            # ① エラーが後継モデルを教えてくれたら、それを次に試す
            hint = suggested_from_error(exc)
            if hint and hint not in tried and hint not in queue:
                logger.info(f"{model} は廃止。APIの案内に従い {hint} を試します")
                queue.insert(0, hint)
                continue

            # ② 一度だけ実在モデルを照会して、候補を差し替える
            if not discovered:
                discovered = True
                found = [m for m in discover(client, purpose=purpose) if m not in tried]
                if found:
                    logger.info(f"実在モデルへ切替: {found[:3]}")
                    queue = found + queue

    if last_error is not None:
        raise last_error
    raise RuntimeError("試せるGeminiモデルがありません")
