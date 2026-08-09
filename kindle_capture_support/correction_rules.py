"""Profile-explicit generic OCR correction rules."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CorrectionRule:
    """A text correction that can only run in its declared profile."""

    profile: str
    name: str
    pattern: re.Pattern[str]
    replacement: str


@dataclass(frozen=True)
class ReviewRule:
    """A suspicious-text check scoped to one correction profile."""

    profile: str
    reason: str
    pattern: re.Pattern[str]


OCR_CORRECTION_RULES = (
    CorrectionRule(
        profile='ai-rag',
        name='LIM -> LLM',
        pattern=re.compile(r"(?<![A-Za-z0-9])LIM(?![A-Za-z0-9])"),
        replacement='LLM',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='LILM -> LLM',
        pattern=re.compile(r"(?<![A-Za-z0-9])LILM(?![A-Za-z0-9])"),
        replacement='LLM',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='UM -> LLM (before を評価者)',
        pattern=re.compile(r"(?<![A-Za-z0-9])UM(?=\s*を評価者)"),
        replacement='LLM',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='LLMLas-a-Judge -> LLM-as-a-Judge',
        pattern=re.compile(r"(?<![A-Za-z0-9])LLMLas-a-Judge(?![A-Za-z0-9])"),
        replacement='LLM-as-a-Judge',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='LM -> LLM (LLM context)',
        pattern=re.compile(r"(?<![A-Za-z0-9])LM(?=\s*(?:で回答|に渡す))"),
        replacement='LLM',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='Al -> AI (before LLM)',
        pattern=re.compile(r"(?<![A-Za-z0-9])Al(?=\s*[・/]\s*LLM(?![A-Za-z0-9]))"),
        replacement='AI',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='Al -> AI (after 生成)',
        pattern=re.compile(r"(?<=生成 )Al(?![A-Za-z0-9])"),
        replacement='AI',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='Al -> AI (AI context)',
        pattern=re.compile(
            r"(?<![A-Za-z0-9])Al(?=\s*(?:に|の|を|が|は|へ|で|と)?\s*"
            r"(?:検索|サービス|エージェント|スタートアップ|分野|モデル|"
            r"技術|システム))"
        ),
        replacement='AI',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='AL -> AI (before 分野)',
        pattern=re.compile(r"(?<![A-Za-z0-9])AL(?=\s*分野)"),
        replacement='AI',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='OpenAl/OpenAT -> OpenAI',
        pattern=re.compile(r"(?<![A-Za-z0-9])OpenA[lT](?![A-Za-z0-9])"),
        replacement='OpenAI',
    ),
    CorrectionRule(
        profile='common',
        name='ユュユーザー -> ユーザー',
        pattern=re.compile(r"ユュユーザー"),
        replacement='ユーザー',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='DeepSerach -> DeepSearch',
        pattern=re.compile(r"\bDeepSerach\b"),
        replacement='DeepSearch',
    ),
    CorrectionRule(
        profile='common',
        name='りリリース -> リリース',
        pattern=re.compile(r"りリリース"),
        replacement='リリース',
    ),
    CorrectionRule(
        profile='common',
        name='ュユーザー -> ユーザー',
        pattern=re.compile(r"ュユーザー"),
        replacement='ユーザー',
    ),
    CorrectionRule(
        profile='common',
        name='ユュースケース -> ユースケース',
        pattern=re.compile(r"ユュースケース"),
        replacement='ユースケース',
    ),
    CorrectionRule(
        profile='common',
        name='ユューザー -> ユーザー',
        pattern=re.compile(r"ユューザー"),
        replacement='ユーザー',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='J udge -> Judge',
        pattern=re.compile(r"(?<![A-Za-z])J\s+udge\b"),
        replacement='Judge',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='クニエリ -> クエリ',
        pattern=re.compile(r"クニエリ"),
        replacement='クエリ',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='チャンジンク -> チャンク',
        pattern=re.compile(r"チャンジンク"),
        replacement='チャンク',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='親チャンクノン親ドキュメント -> 親チャンク／親ドキュメント',
        pattern=re.compile(r"親チャンクノン親ドキュメント"),
        replacement='親チャンク／親ドキュメント',
    ),
    CorrectionRule(
        profile='ai-rag',
        name='親ページプン親チャンク -> 親ページ／親チャンク',
        pattern=re.compile(r"親ページプン親チャンク"),
        replacement='親ページ／親チャンク',
    ),
)


OCR_REVIEW_RULES = (
    ReviewRule(
        profile="common",
        reason="mixed_script_ending",
        pattern=re.compile(r"[ぁ-んァ-ヶ一-龯]\s+[A-Z]{1,3}[,.:;]?$")
    ),
    ReviewRule(
        profile="ai-rag",
        reason="embedded_ai_rag_noise",
        pattern=re.compile(r"クニエリ|チャンジンク|親チャンクノン|親ページプン"),
    ),
    ReviewRule(
        profile="common",
        reason="ocr_symbol_fragment",
        pattern=re.compile(r"\[(?:=|[A-Za-z]{1,3})\]"),
    ),
)


POST_OCR_REVIEW_RULES = (
    ReviewRule(
        profile="ai-rag",
        reason="llm_variant_lilm",
        pattern=re.compile(r"(?<![A-Za-z0-9])LILM(?![A-Za-z0-9])"),
    ),
    ReviewRule(
        profile="ai-rag",
        reason="llm_variant_um",
        pattern=re.compile(r"(?<![A-Za-z0-9])UM(?=\s*を評価者)"),
    ),
    ReviewRule(
        profile="ai-rag",
        reason="llm_variant_title",
        pattern=re.compile(r"\bLLMLas-a-Judge\b"),
    ),
    ReviewRule(
        profile="ai-rag",
        reason="llm_variant_lm",
        pattern=re.compile(r"(?<![A-Za-z0-9])LM(?=\s*(?:で回答|に渡す))"),
    ),
    ReviewRule(
        profile="common",
        reason="checkbox_fragment",
        pattern=re.compile(r"\[(?:=|[A-Za-z]{1,3})\]"),
    ),
    ReviewRule(
        profile="ai-rag",
        reason="joined_heading",
        pattern=re.compile(r"Self-RAGLLM"),
    ),
)
