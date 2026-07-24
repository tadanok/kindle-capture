"""OCRmyPDF plugin for adaptive, read-aloud-friendly Japanese OCR."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from statistics import median

from ocrmypdf import OcrClass, OcrElement, hookimpl
from ocrmypdf._exec import tesseract
from ocrmypdf.builtin_plugins.tesseract_ocr import TesseractOcrEngine
from ocrmypdf.hocrtransform import HocrParser
from PIL import Image


JAPANESE_CHARACTER = (
    r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff66-\uff9f"
)
JAPANESE_PUNCTUATION = r"、。，．・：；！？「」『』（）［］【】〈〉《》〔〕｛｝"
JAPANESE_OR_PUNCTUATION = JAPANESE_CHARACTER + JAPANESE_PUNCTUATION
PAGE_NUMBER_RE = re.compile(r"^[\s\-–—―]*\d+[\s\-–—―]*$")
MEANINGFUL_CHARACTER_RE = re.compile(f"[A-Za-z0-9{JAPANESE_CHARACTER}]")
SAFE_PUNCTUATION = set("、。，．・：；！？「」『』（）［］【】〈〉《》〔〕｛｝.,:;!?()[]{}<>+-/%&@#'\"")
COMMON_OCR_CORRECTIONS = (
    (
        "LIM -> LLM",
        re.compile(r"(?<![A-Za-z0-9])LIM(?![A-Za-z0-9])"),
        "LLM",
    ),
    (
        "LILM -> LLM",
        re.compile(r"(?<![A-Za-z0-9])LILM(?![A-Za-z0-9])"),
        "LLM",
    ),
    (
        "UM -> LLM (before を評価者)",
        re.compile(r"(?<![A-Za-z0-9])UM(?=\s*を評価者)"),
        "LLM",
    ),
    (
        "LLMLas-a-Judge -> LLM-as-a-Judge",
        re.compile(r"(?<![A-Za-z0-9])LLMLas-a-Judge(?![A-Za-z0-9])"),
        "LLM-as-a-Judge",
    ),
    (
        "LM -> LLM (LLM context)",
        re.compile(r"(?<![A-Za-z0-9])LM(?=\s*(?:で回答|に渡す))"),
        "LLM",
    ),
    (
        "Al -> AI (before LLM)",
        re.compile(r"(?<![A-Za-z0-9])Al(?=\s*[・/]\s*LLM(?![A-Za-z0-9]))"),
        "AI",
    ),
    (
        "Al -> AI (after 生成)",
        re.compile(r"(?<=生成 )Al(?![A-Za-z0-9])"),
        "AI",
    ),
    (
        "Al -> AI (AI context)",
        re.compile(
            r"(?<![A-Za-z0-9])Al(?=\s*(?:に|の|を|が|は|へ|で|と)?\s*"
            r"(?:検索|サービス|エージェント|スタートアップ|分野|モデル|"
            r"技術|システム))"
        ),
        "AI",
    ),
    (
        "AL -> AI (before 分野)",
        re.compile(r"(?<![A-Za-z0-9])AL(?=\s*分野)"),
        "AI",
    ),
    (
        "OpenAl/OpenAT -> OpenAI",
        re.compile(r"(?<![A-Za-z0-9])OpenA[lT](?![A-Za-z0-9])"),
        "OpenAI",
    ),
    (
        "ユュユーザー -> ユーザー",
        re.compile(r"ユュユーザー"),
        "ユーザー",
    ),
    (
        "本書の最初の草 -> 本書の最初の章",
        re.compile(r"本書の最初の草"),
        "本書の最初の章",
    ),
    ("徹し込む -> 流し込む", re.compile(r"徹し込む"), "流し込む"),
    (
        "クエリに登場しレた -> クエリに登場した",
        re.compile(r"クエリに登場しレた"),
        "クエリに登場した",
    ),
    ("ノフード間 -> ノード間", re.compile(r"ノフード間"), "ノード間"),
    ("精統化 -> 精緻化", re.compile(r"精統化"), "精緻化"),
    ("DeepSerach -> DeepSearch", re.compile(r"\bDeepSerach\b"), "DeepSearch"),
    ("流輸さ -> 流暢さ", re.compile(r"流輸さ"), "流暢さ"),
    (
        "モニタリンググ基盤 -> モニタリング基盤",
        re.compile(r"モニタリンググ基盤"),
        "モニタリング基盤",
    ),
    (
        "マッピングレし -> マッピングし",
        re.compile(r"マッピングレし"),
        "マッピングし",
    ),
    (
        "検索記/刀推論 -> 検索&推論",
        re.compile(r"検索[記刀]推論"),
        "検索&推論",
    ),
    ("りリリース -> リリース", re.compile(r"りリリース"), "リリース"),
    ("翼境 -> 環境", re.compile(r"翼境"), "環境"),
    ("ュユーザー -> ユーザー", re.compile(r"ュユーザー"), "ユーザー"),
    (
        "ユュースケース -> ユースケース",
        re.compile(r"ユュースケース"),
        "ユースケース",
    ),
    ("ユューザー -> ユーザー", re.compile(r"ユューザー"), "ユーザー"),
    ("ソツール -> ツール", re.compile(r"ソツール"), "ツール"),
    (
        "場合なあります -> 場合があります",
        re.compile(r"場合なあります"),
        "場合があります",
    ),
    ("人歓迎します -> 歓迎します", re.compile(r"人歓迎します"), "歓迎します"),
    (
        "HIE RAG -> 第3章 RAG",
        re.compile(r"(?<![A-Za-z])HIE(?=\s*RAG\s*精度改善)"),
        "第3章",
    ),
    ("精度改番 -> 精度改善", re.compile(r"精度改番"), "精度改善"),
    (
        "根拠に思実か -> 根拠に忠実か",
        re.compile(r"根拠に思実か"),
        "根拠に忠実か",
    ),
    (
        "下がりやすぐ -> 下がりやすく",
        re.compile(r"下がりやすぐ"),
        "下がりやすく",
    ),
    (
        "マルチモーダレル -> マルチモーダル",
        re.compile(r"マルチモーダレル"),
        "マルチモーダル",
    ),
    (
        "画像トナテキスト -> 画像やテキスト",
        re.compile(r"画像トナテキスト"),
        "画像やテキスト",
    ),
    (
        "物在のよりモダン -> 現在のよりモダン",
        re.compile(r"物在のよりモダン"),
        "現在のよりモダン",
    ),
    (
        "ブフォーマシト -> フォーマット",
        re.compile(r"ブフォーマシト"),
        "フォーマット",
    ),
    ("宮崎験 -> 宮崎駿", re.compile(r"宮崎験"), "宮崎駿"),
    (
        "宮崎駿一 (会社 -> 宮崎駿 — (会社",
        re.compile(r"宮崎駿一(?=\s*\(会社)"),
        "宮崎駿 —",
    ),
    (
        "numbered-list underscore -> removed",
        re.compile(r"(?<=\d\.)\s*_\s*(?=前処理)"),
        " ",
    ),
    (
        "_ LanceDB -> LanceDB",
        re.compile(r"(?<![A-Za-z])_\s+(?=LanceDB\b)"),
        "",
    ),
    (
        "Hallucination) J -> Hallucination)」",
        re.compile(r"Hallucination\)\s*J"),
        "Hallucination)」",
    ),
    ("J udge -> Judge", re.compile(r"(?<![A-Za-z])J\s+udge\b"), "Judge"),
    ("いっつた -> いった", re.compile(r"いっ\s*つた"), "いった"),
    (
        "説明 ET, -> 説明します。",
        re.compile(r"説明\s+ET,"),
        "説明します。",
    ),
    (
        "構成されま i : -> 構成されます：",
        re.compile(r"構成されま\s+i\s*[:：]"),
        "構成されます：",
    ),
    ("です。 HF -> です。研", re.compile(r"です。\s*HF\b"), "です。研"),
    ("完レポート -> 究レポート", re.compile(r"完レポート"), "究レポート"),
    (
        "整理じてでておりまずすず -> 整理しております",
        re.compile(r"^整理じてでておりまずすず\s*[:：]"),
        "整理しております：",
    ),
    (
        "じてでておりまずすず -> しております",
        re.compile(r"^じてでておりまずすず\s*[:：]"),
        "しております：",
    ),
    (
        "retrieval chapter sentence recovery",
        re.compile(
            r"^AELOET,\s*EL,\s*FOFEY\s+THEE\]\s*TRH\s+ERA,\s*データの"
        ),
        "をまとめます。ただし、どの手法も「万能」ではありません。データの",
    ),
    ("クニエリ -> クエリ", re.compile(r"クニエリ"), "クエリ"),
    ("チャンジンク -> チャンク", re.compile(r"チャンジンク"), "チャンク"),
    ("比較レて -> 比較して", re.compile(r"比較レて"), "比較して"),
    (
        "親チャンクノン親ドキュメント -> 親チャンク／親ドキュメント",
        re.compile(r"親チャンクノン親ドキュメント"),
        "親チャンク／親ドキュメント",
    ),
    ("BELベル -> 段落レベル", re.compile(r"BELベル"), "段落レベル"),
    ("元の文書き。 -> 元の文書や、", re.compile(r"元の文書き。"), "元の文書や、"),
    (
        "親ページプン親チャンク -> 親ページ／親チャンク",
        re.compile(r"親ページプン親チャンク"),
        "親ページ／親チャンク",
    ),
    (
        "来てまずよ/という ? う形 -> 来てますよ/という形",
        re.compile(r"来てまずよ」どという\s*\?\s*う形"),
        "来てますよ」という形",
    ),
)
AI_RAG_CORRECTION_NAMES = frozenset(
    {
        "LIM -> LLM",
        "LILM -> LLM",
        "UM -> LLM (before を評価者)",
        "LLMLas-a-Judge -> LLM-as-a-Judge",
        "LM -> LLM (LLM context)",
        "Al -> AI (before LLM)",
        "Al -> AI (after 生成)",
        "Al -> AI (AI context)",
        "AL -> AI (before 分野)",
        "OpenAl/OpenAT -> OpenAI",
        "DeepSerach -> DeepSearch",
        "クニエリ -> クエリ",
        "チャンジンク -> チャンク",
        "親チャンクノン親ドキュメント -> 親チャンク／親ドキュメント",
        "親ページプン親チャンク -> 親ページ／親チャンク",
        "J udge -> Judge",
    }
)
RAG_ACCURACY_BOOK_CORRECTION_NAMES = frozenset(
    {
        "HIE RAG -> 第3章 RAG",
        "宮崎験 -> 宮崎駿",
        "宮崎駿一 (会社 -> 宮崎駿 — (会社",
        "numbered-list underscore -> removed",
        "_ LanceDB -> LanceDB",
        "Hallucination) J -> Hallucination)」",
        "説明 ET, -> 説明します。",
        "構成されま i : -> 構成されます：",
        "です。 HF -> です。研",
        "完レポート -> 究レポート",
        "整理じてでておりまずすず -> 整理しております",
        "じてでておりまずすず -> しております",
        "retrieval chapter sentence recovery",
        "来てまずよ/という ? う形 -> 来てますよ/という形",
    }
)
DEFAULT_CORRECTION_PROFILES = frozenset({"common"})


def expand_correction_profiles(
    profiles: set[str] | frozenset[str] | None,
) -> frozenset[str]:
    """Expand profile dependencies while keeping book-specific fixes opt-in."""
    expanded = set(DEFAULT_CORRECTION_PROFILES if profiles is None else profiles)
    if "rag-accuracy-book" in expanded:
        expanded.update({"common", "ai-rag"})
    if "ai-rag" in expanded:
        expanded.add("common")
    return frozenset(expanded)


def correction_profile_for_name(name: str) -> str:
    if name in RAG_ACCURACY_BOOK_CORRECTION_NAMES:
        return "rag-accuracy-book"
    if name in AI_RAG_CORRECTION_NAMES:
        return "ai-rag"
    return "common"


def correction_profiles_from_environment() -> frozenset[str]:
    value = os.environ.get("KINDLE_OCR_CORRECTION_PROFILES", "common")
    return expand_correction_profiles(
        {profile.strip() for profile in value.split(",") if profile.strip()}
    )


KNOWN_ACRONYMS = {
    "AI",
    "API",
    "DB",
    "FAQ",
    "LLM",
    "OCR",
    "QA",
    "RAG",
    "SQL",
    "URL",
}
OCR_REVIEW_PATTERNS = (
    (
        "mixed_script_ending",
        re.compile(r"[ぁ-んァ-ヶ一-龯]\s+[A-Z]{1,3}[,.:;]?$"),
    ),
    ("repeated_kana", re.compile(r"すず|じてでて|まずすず")),
    (
        "embedded_ocr_noise",
        re.compile(
            r"クニエリ|チャンジンク|比較レ|レポー\s*\d|"
            r"ソツール|場合なあります|人歓迎|HIE\s+RAG|"
            r"ユューザー|思実|やすぐ|マルチモーダレル|"
            r"画像トナ|ブフォーマシト|宮崎験|WET,"
        ),
    ),
    (
        "ocr_symbol_fragment",
        re.compile(r"\[(?:=|[A-Za-z]{1,3})\]"),
    ),
)


def _line_words(line: OcrElement) -> list[OcrElement]:
    return [
        child
        for child in line.children
        if child.ocr_class == OcrClass.WORD and child.text
    ]


def normalize_line_text(words: list[str]) -> str:
    """Join OCR words while removing only spaces inserted inside Japanese text."""
    text = " ".join(word.strip() for word in words if word.strip())
    return re.sub(
        f"(?<=[{JAPANESE_OR_PUNCTUATION}])[ \t]+"
        f"(?=[{JAPANESE_OR_PUNCTUATION}])",
        "",
        text,
    ).strip()


def reorder_ocr_tree_by_position(page: OcrElement) -> int:
    """Order page blocks and paragraph lines by their visual coordinates."""
    moved = 0

    def position(element: OcrElement) -> tuple[float, float]:
        if element.bbox is not None:
            return element.bbox.top, element.bbox.left
        child_positions = [
            position(child)
            for child in element.children
            if child.bbox is not None or child.children
        ]
        return min(child_positions) if child_positions else (float("inf"), float("inf"))

    def visit(element: OcrElement) -> None:
        nonlocal moved
        for child in element.children:
            visit(child)
        if element.ocr_class not in {OcrClass.PAGE, OcrClass.PARAGRAPH}:
            return
        original = list(element.children)
        ordered = sorted(
            enumerate(original),
            key=lambda item: (*position(item[1]), item[0]),
        )
        reordered = [child for _, child in ordered]
        moved += sum(
            before is not after
            for before, after in zip(original, reordered, strict=True)
        )
        element.children = reordered

    visit(page)
    return moved


def correct_common_ocr_misrecognitions(
    text: str,
    profiles: set[str] | frozenset[str] | None = None,
) -> tuple[str, dict[str, int]]:
    """Correct only exact or narrowly contextualized, recurring OCR errors."""
    corrected = text
    corrections: Counter[str] = Counter()
    active_profiles = expand_correction_profiles(profiles)
    for name, pattern, replacement in COMMON_OCR_CORRECTIONS:
        if correction_profile_for_name(name) not in active_profiles:
            continue
        corrected, count = pattern.subn(replacement, corrected)
        if count:
            corrections[name] += count
    return corrected, dict(sorted(corrections.items()))


def apply_common_ocr_corrections(
    page: OcrElement,
    profiles: set[str] | frozenset[str] | None = None,
) -> dict[str, int]:
    """Apply conservative corrections while retaining each OCR line's bbox."""
    active_profiles = expand_correction_profiles(profiles)
    ai_rag_enabled = "ai-rag" in active_profiles
    book_profile_enabled = "rag-accuracy-book" in active_profiles
    corrections: Counter[str] = Counter()
    active_lines: list[tuple[OcrElement, list[OcrElement], str]] = []
    for line in page.lines:
        words = _line_words(line)
        if not words or line.bbox is None:
            continue
        active_lines.append(
            (line, words, normalize_line_text([word.text for word in words]))
        )

    def replace_line_text(
        line: OcrElement,
        words: list[OcrElement],
        corrected_text: str,
    ) -> None:
        confidences = [
            word.confidence for word in words if word.confidence is not None
        ]
        line.children = [
            OcrElement(
                ocr_class=OcrClass.WORD,
                bbox=line.bbox,
                text=corrected_text,
                confidence=(
                    sum(confidences) / len(confidences)
                    if confidences
                    else None
                ),
                font=words[0].font,
                language=line.language,
                direction=line.direction,
            )
        ]

    for index, (line, words, line_text) in enumerate(active_lines):
        previous_line_text = (
            active_lines[index - 1][2] if index > 0 else ""
        )
        next_line_text = (
            active_lines[index + 1][2]
            if index + 1 < len(active_lines)
            else ""
        )
        split_title_continuation = (
            ai_rag_enabled
            and previous_line_text.endswith("LLM-as-a-")
            and bool(re.fullmatch(r"J\s+udge\)", line_text))
        )
        split_sentence_continuation = (
            book_profile_enabled
            and previous_line_text.endswith("構成されま")
            and bool(re.fullmatch(r"i\s*[:：]", line_text))
        )
        misplaced_node_detail = (
            book_profile_enabled
            and line_text.startswith("(人名、組織、場所、出来事")
            and next_line_text.startswith("e ノード:")
        )
        misplaced_sentence_end = (
            book_profile_enabled
            and line_text == "す。"
            and next_line_text.startswith("「構造化された関係性」")
        )
        if (
            split_title_continuation
            or split_sentence_continuation
            or misplaced_node_detail
            or misplaced_sentence_end
        ):
            line.children = []
            continue

        corrected_text, line_corrections = correct_common_ocr_misrecognitions(
            line_text,
            profiles=active_profiles,
        )
        if (
            ai_rag_enabled
            and corrected_text.endswith("LLM-as-a-")
            and re.fullmatch(r"J\s+udge\)", next_line_text)
        ):
            corrected_text += "Judge)"
            line_corrections[
                "split LLM-as-a-/J udge -> LLM-as-a-Judge"
            ] = 1
        if (
            book_profile_enabled
            and corrected_text.endswith("構成されま")
            and re.fullmatch(r"i\s*[:：]", next_line_text)
        ):
            corrected_text += "す："
            line_corrections[
                "split 構成されま/i : -> 構成されます："
            ] = 1
        if (
            ai_rag_enabled
            and re.search(r"(?<![A-Za-z0-9])Al$", corrected_text)
            and re.match(r"^の実務経験", next_line_text)
        ):
            corrected_text = re.sub(r"Al$", "AI", corrected_text)
            line_corrections["Al -> AI (before next-line の実務経験)"] = 1
        if not book_profile_enabled:
            if line_corrections:
                replace_line_text(line, words, corrected_text)
                corrections.update(line_corrections)
            continue
        if (
            corrected_text.endswith("一歩を中")
            and next_line_text.startswith("み出したい")
        ):
            corrected_text = corrected_text[:-1]
            line_corrections["一歩を中/み出す -> 一歩を踏み出す"] = 1
        if (
            previous_line_text.endswith("一歩を中")
            and corrected_text.startswith("み出したい")
        ):
            corrected_text = "踏" + corrected_text
            line_corrections["中/み出したい -> 踏み出したい"] = 1
        if (
            corrected_text.endswith("なりま")
            and next_line_text.startswith("To ARAL のブラックボックス性")
        ):
            corrected_text += "す。"
            line_corrections["なりま/To ARAL -> なります。生成 AI"] = 1
        if (
            previous_line_text.endswith("なりま")
            and corrected_text.startswith("To ARAL のブラックボックス性")
        ):
            corrected_text = re.sub(
                r"^To ARAL の",
                "生成 AI の",
                corrected_text,
            )
            line_corrections["To ARAL の -> 生成 AI の"] = 1
        if corrected_text.endswith("定番で") and next_line_text == "Te":
            corrected_text += "す。"
            line_corrections["定番で/Te -> 定番です。"] = 1
        if corrected_text == "Te" and previous_line_text.endswith("定番で"):
            line.children = []
            corrections["Te after 定番で -> removed"] = 1
            continue
        if (
            corrected_text.endswith("と =")
            and next_line_text.startswith("ークリッド距離")
        ):
            corrected_text = re.sub(r"=$", "ユ", corrected_text)
            line_corrections["=/ークリッド -> ユークリッド"] = 1
        if corrected_text == "WET,":
            if previous_line_text.endswith("提案して"):
                corrected_text = "います。"
                line_corrections["提案して/WET, -> 提案しています。"] = 1
            elif previous_line_text.endswith("向いて"):
                corrected_text = "います。"
                line_corrections["向いて/WET, -> 向いています。"] = 1
        if (
            previous_line_text.endswith("下がり")
            and corrected_text.startswith("やすぐ")
        ):
            corrected_text = re.sub(r"^やすぐ", "やすく", corrected_text)
            line_corrections["下がり/やすぐ -> 下がりやすく"] = 1
        if previous_line_text.endswith("パイプライ"):
            corrected_text, count = re.subn(
                r"^ジ(?=です)",
                "ン",
                corrected_text,
            )
            if count:
                line_corrections["ジ -> ン (after パイプライ)"] = count
        if previous_line_text.endswith("とい"):
            corrected_text, count = re.subn(
                r"^っつた(?=自動スコア)",
                "った",
                corrected_text,
            )
            if count:
                line_corrections["とい/っつた -> といった"] = count
        if previous_line_text.startswith("(人名、組織、場所、出来事"):
            if corrected_text.startswith("e ノード:"):
                corrected_text += " " + previous_line_text
                line_corrections[
                    "node detail before label -> label before node detail"
                ] = 1
        if previous_line_text == "す。":
            if (
                corrected_text.startswith("「構造化された関係性」")
                and corrected_text.endswith("特徴で")
            ):
                corrected_text += "す。"
                line_corrections[
                    "misordered sentence end -> sentence end"
                ] = 1
        if corrected_text.endswith("BEL") and next_line_text.startswith("ベルや"):
            corrected_text = re.sub(r"BEL$", "段落レ", corrected_text)
            line_corrections["BEL/ベル -> 段落レベル"] = 1
        if corrected_text.endswith("比較レ") and next_line_text.startswith("て、"):
            corrected_text = re.sub(r"比較レ$", "比較し", corrected_text)
            line_corrections["比較レ/て -> 比較して"] = 1
        if previous_line_text.endswith("来てま"):
            corrected_text, count = re.subn(
                r"^ずよ」どという\s*\?\s*う形",
                "すよ」という形",
                corrected_text,
            )
            if count:
                line_corrections[
                    "来てま/ずよどという ? う形 -> 来てますよという形"
                ] = count
        if (
            previous_line_text.endswith("イメージで")
            and re.fullmatch(r"すず?\s*[:：]", corrected_text)
        ):
            corrected_text = "す："
            line_corrections["すず : -> す： (after イメージで)"] = 1
        if (
            corrected_text.endswith("パイプライ")
            and not next_line_text.startswith(("ジです", "ンです"))
        ):
            corrected_text += "ン"
            line_corrections[
                "パイプライ -> パイプライン (missing continuation)"
            ] = 1
        if re.fullmatch(
            r"\d+(?:\.\d+){2}\s+(?:Self-RAG|Agentic RAG|RAG-Reasoning)",
            corrected_text,
        ):
            corrected_text += "："
            line_corrections["section heading -> section heading："] = 1
        if not line_corrections:
            continue

        replace_line_text(line, words, corrected_text)
        corrections.update(line_corrections)
    return dict(sorted(corrections.items()))


def find_ocr_review_candidates(page: OcrElement) -> list[dict[str, object]]:
    """Return suspicious surviving lines without changing their text."""
    candidates: list[dict[str, object]] = []
    for line in page.lines:
        words = _line_words(line)
        if not words:
            continue
        item = _line_metrics(line)
        reasons = _line_review_reasons(item)
        if not reasons:
            continue
        candidates.append(
            {
                "text": item["text"],
                "confidence": item["confidence"],
                "reasons": sorted(set(reasons)),
            }
        )
    return candidates


def _line_review_reasons(
    item: dict[str, float | int | str],
) -> list[str]:
    text = str(item["text"])
    reasons = [
        name for name, pattern in OCR_REVIEW_PATTERNS if pattern.search(text)
    ]
    ending_acronym = re.search(r"\b([A-Z]{1,4})[,.:;]?$", text)
    if (
        ending_acronym
        and ending_acronym.group(1) in KNOWN_ACRONYMS
        and "mixed_script_ending" in reasons
    ):
        reasons.remove("mixed_script_ending")
    has_japanese = bool(re.search(f"[{JAPANESE_CHARACTER}]", text))
    uppercase_tokens = re.findall(r"\b[A-Z]{2,}\b", text)
    if (
        float(item["confidence"]) < 0.48
        and has_japanese
        and (
            len(uppercase_tokens) >= 2
            or float(item["suspicious_ratio"]) >= 0.12
        )
    ):
        reasons.append("low_confidence_mixed_script")
    return sorted(set(reasons))


def should_accept_line_retry(
    original: dict[str, float | int | str],
    alternative: dict[str, float | int | str],
) -> bool:
    """Accept a line retry only when it is materially safer than the original."""
    original_text = str(original["text"])
    alternative_text = str(alternative["text"])
    if _line_review_reasons(alternative):
        return False
    if not re.search(f"[A-Za-z0-9{JAPANESE_CHARACTER}]", alternative_text):
        return False
    length_ratio = len(alternative_text) / max(1, len(original_text))
    if not 0.45 <= length_ratio <= 2.2:
        return False
    return float(alternative["confidence"]) >= max(
        0.72,
        float(original["confidence"]) + 0.08,
    )


def retry_review_candidate_lines(
    page: OcrElement,
    image: Image.Image,
    options,
    page_number: int,
) -> int:
    """Retry only suspicious lines using a scaled single-line crop."""
    retried = 0
    image = image.convert("RGB")
    for line_index, line in enumerate(list(page.lines)):
        words = _line_words(line)
        if not words or line.bbox is None:
            continue
        original = _line_metrics(line)
        if not _line_review_reasons(original):
            continue

        line_height = max(1.0, line.bbox.bottom - line.bbox.top)
        x_padding = max(8, int(image.width * 0.01))
        y_padding = max(3, int(line_height * 0.35))
        crop_box = (
            max(0, int(line.bbox.left) - x_padding),
            max(0, int(line.bbox.top) - y_padding),
            min(image.width, int(line.bbox.right) + x_padding),
            min(image.height, int(line.bbox.bottom) + y_padding),
        )
        crop = image.crop(crop_box)
        if crop.width < 10 or crop.height < 5:
            continue
        crop = crop.resize(
            (crop.width * 2, crop.height * 2),
            Image.Resampling.LANCZOS,
        )

        with tempfile.TemporaryDirectory(
            prefix=f"kindle-line-retry-{page_number + 1}-{line_index}-"
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            crop_path = temporary_root / "line.png"
            output_hocr = temporary_root / "line.hocr"
            output_text = temporary_root / "line.txt"
            crop.save(crop_path, format="PNG")
            try:
                tesseract.generate_hocr(
                    input_file=crop_path,
                    output_hocr=output_hocr,
                    output_text=output_text,
                    languages=options.languages,
                    engine_mode=options.tesseract.oem,
                    tessconfig=options.tesseract.config,
                    timeout=options.tesseract.timeout,
                    pagesegmode=7,
                    thresholding=options.tesseract.thresholding,
                    user_words=options.tesseract.user_words,
                    user_patterns=options.tesseract.user_patterns,
                    omp_thread_limit=options.tesseract.omp_thread_limit,
                )
                retry_page = HocrParser(output_hocr).parse()
            except (OSError, RuntimeError, subprocess.SubprocessError):
                continue

        retry_lines = [
            candidate
            for candidate in retry_page.lines
            if _line_words(candidate)
        ]
        if not retry_lines:
            continue
        retry_line = max(
            retry_lines,
            key=lambda candidate: float(_line_metrics(candidate)["confidence"]),
        )
        alternative = _line_metrics(retry_line)
        if not should_accept_line_retry(original, alternative):
            continue

        alternative_words = _line_words(retry_line)
        line.children = [
            OcrElement(
                ocr_class=OcrClass.WORD,
                bbox=line.bbox,
                text=str(alternative["text"]),
                confidence=float(alternative["confidence"]),
                font=words[0].font,
                language=line.language,
                direction=line.direction,
            )
        ]
        retried += 1
    return retried


def _line_metrics(line: OcrElement) -> dict[str, float | int | str]:
    words = _line_words(line)
    text = normalize_line_text([word.text for word in words])
    confidences = [
        word.confidence for word in words if word.confidence is not None
    ]
    confidence = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )
    visible = [character for character in text if not character.isspace()]
    suspicious = [
        character
        for character in visible
        if not MEANINGFUL_CHARACTER_RE.fullmatch(character)
        and character not in SAFE_PUNCTUATION
    ]
    return {
        "text": text,
        "characters": len(visible),
        "confidence": round(confidence, 2),
        "suspicious_ratio": (
            len(suspicious) / len(visible) if visible else 0.0
        ),
    }


def analyze_ocr_page(page: OcrElement) -> dict[str, float | int]:
    """Return comparable OCR quality metrics for one structured OCR page."""
    lines = [line for line in page.lines if _line_words(line)]
    metrics = [_line_metrics(line) for line in lines]
    character_count = sum(int(item["characters"]) for item in metrics)
    confidences = [
        float(item["confidence"])
        for item in metrics
        if int(item["characters"]) > 0
    ]
    mean_confidence = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )
    suspicious_characters = sum(
        int(item["characters"]) * float(item["suspicious_ratio"])
        for item in metrics
    )
    suspicious_ratio = (
        suspicious_characters / character_count if character_count else 1.0
    )
    score = (
        mean_confidence * 100
        + min(8.0, character_count / 80)
        - suspicious_ratio * 25
    )
    return {
        "line_count": len(lines),
        "character_count": character_count,
        "mean_confidence": round(mean_confidence, 2),
        "suspicious_ratio": round(suspicious_ratio, 4),
        "score": round(score, 2),
    }


def should_retry_ocr(metrics: dict[str, float | int]) -> bool:
    """Retry only pages whose first OCR pass is sparse or unreliable."""
    line_count = int(metrics["line_count"])
    character_count = int(metrics["character_count"])
    confidence = float(metrics["mean_confidence"])
    suspicious_ratio = float(metrics["suspicious_ratio"])
    return (
        confidence < 0.78
        or suspicious_ratio > 0.12
        or (line_count <= 15 and character_count < 220)
    )


def choose_alternate_pagesegmode(
    metrics: dict[str, float | int],
    primary_pagesegmode: int | None,
) -> int:
    """Choose one alternate layout, avoiding a full multi-mode brute force."""
    line_count = int(metrics["line_count"])
    character_count = int(metrics["character_count"])
    if line_count <= 18 or character_count < 260:
        alternate = 11  # sparse text, suitable for covers and diagrams
    else:
        alternate = 3  # automatic page layout
    if primary_pagesegmode == alternate:
        return 6 if alternate != 6 else 3
    return alternate


def filter_low_confidence_lines(page: OcrElement) -> int:
    """Remove only short, isolated, low-confidence, symbol-heavy OCR noise."""
    lines = [line for line in page.lines if _line_words(line)]
    heights = [
        line.bbox.height
        for line in lines
        if line.bbox is not None and line.bbox.height > 0
    ]
    typical_height = median(heights) if heights else 1.0
    sparse_page = len(lines) <= 18
    removed = 0

    for index, line in enumerate(lines):
        item = _line_metrics(line)
        text = str(item["text"])
        if not text or PAGE_NUMBER_RE.fullmatch(text):
            continue

        previous = lines[index - 1] if index > 0 else None
        following = lines[index + 1] if index + 1 < len(lines) else None
        gaps: list[float] = []
        if line.bbox is not None and previous is not None and previous.bbox is not None:
            gaps.append(max(0.0, line.bbox.top - previous.bbox.bottom))
        if line.bbox is not None and following is not None and following.bbox is not None:
            gaps.append(max(0.0, following.bbox.top - line.bbox.bottom))
        isolated = bool(gaps) and min(gaps) > typical_height * 1.8

        low_confidence = float(item["confidence"]) < 0.48
        short = int(item["characters"]) <= 40
        symbol_heavy = float(item["suspicious_ratio"]) >= 0.18
        if low_confidence and short and symbol_heavy and (sparse_page or isolated):
            line.children = []
            removed += 1

    return removed


def filter_list_marker_words(
    page: OcrElement,
    image: Image.Image,
) -> list[dict[str, object]]:
    """Remove visually isolated list markers from the invisible text layer."""
    image = image.convert("L")
    candidates: list[dict[str, object]] = []
    explicit_markers = set("●•○◉◦✓✔☑□■▪▫・")
    common_marker_misrecognitions = {"e", "o", "O", "@", "$", "る", "®"}

    for line in page.lines:
        words = _line_words(line)
        if not words:
            continue
        marker = words[0]
        following_words = words[1:]
        marker_is_own_line = not following_words
        if marker_is_own_line and marker.bbox is not None:
            marker_center_y = (
                marker.bbox.top + marker.bbox.bottom
            ) / 2
            nearby_lines: list[tuple[float, list[OcrElement]]] = []
            for other_line in page.lines:
                if other_line is line:
                    continue
                other_words = _line_words(other_line)
                if not other_words or other_words[0].bbox is None:
                    continue
                other = other_words[0]
                if other.bbox.left <= marker.bbox.right:
                    continue
                other_center_y = (
                    other.bbox.top + other.bbox.bottom
                ) / 2
                vertical_distance = abs(other_center_y - marker_center_y)
                if vertical_distance > max(
                    marker.bbox.height * 0.55,
                    other.bbox.height * 0.75,
                ):
                    continue
                nearby_lines.append(
                    (
                        other.bbox.left - marker.bbox.right
                        + vertical_distance,
                        other_words,
                    )
                )
            if nearby_lines:
                following_words = min(
                    nearby_lines,
                    key=lambda item: item[0],
                )[1]
        if not following_words:
            continue
        following = following_words[0]
        if marker.bbox is None or following.bbox is None:
            continue
        token = marker.text.strip()
        if not token or len(token) > 4:
            continue
        if re.fullmatch(r"(?:\d+|[A-Za-z])[.)]", token):
            continue

        width = marker.bbox.right - marker.bbox.left
        height = marker.bbox.bottom - marker.bbox.top
        if width <= 0 or height <= 0:
            continue
        minimum_marker_size = max(8.0, min(image.size) * 0.005)
        if min(width, height) < minimum_marker_size:
            continue
        aspect_ratio = width / height
        if not 0.55 <= aspect_ratio <= 1.65:
            continue
        gap = following.bbox.left - marker.bbox.right
        if gap < 0:
            continue

        crop_box = (
            max(0, int(marker.bbox.left)),
            max(0, int(marker.bbox.top)),
            min(image.width, int(marker.bbox.right)),
            min(image.height, int(marker.bbox.bottom)),
        )
        crop = image.crop(crop_box)
        histogram = crop.histogram()
        pixel_count = crop.width * crop.height
        dark_ratio = (
            sum(histogram[:128]) / pixel_count
            if pixel_count
            else 0.0
        )
        solid_marker = (
            dark_ratio >= 0.68
            and (
                token in explicit_markers
                or gap >= max(18.0, height * 0.7)
            )
        )
        large_marker_candidate = height >= min(image.size) * 0.03
        checkbox_candidate = (
            (
                0.32 <= dark_ratio < 0.68
                or (
                    large_marker_candidate
                    and 0.25 <= dark_ratio < 0.68
                )
            )
            and gap >= height * (
                0.15 if large_marker_candidate else 0.2
            )
            and (
                token.startswith("[")
                or token in {"M", "V", "v", "W"}
                or large_marker_candidate
            )
        )
        if not solid_marker and not checkbox_candidate:
            continue

        following_text = normalize_line_text(
            [word.text for word in following_words]
        )
        following_match = re.match(
            f"^([^A-Za-z0-9{JAPANESE_CHARACTER}]{{0,3}})"
            f"[A-Za-z0-9{JAPANESE_CHARACTER}]",
            following_text,
        )
        if not following_match:
            continue
        if (
            following_match.group(1).strip()
            and token not in explicit_markers
            and token not in common_marker_misrecognitions
        ):
            continue
        candidates.append(
            {
                "line": line,
                "word": marker,
                "recognized_as": token,
                "following_text": following_text[:80],
                "bbox": {
                    "left": round(marker.bbox.left, 1),
                    "top": round(marker.bbox.top, 1),
                    "right": round(marker.bbox.right, 1),
                    "bottom": round(marker.bbox.bottom, 1),
                },
                "center_x": (marker.bbox.left + marker.bbox.right) / 2,
                "height": height,
                "dark_ratio": dark_ratio,
                "solid_marker": solid_marker,
                "checkbox_candidate": checkbox_candidate,
            }
        )

    accepted: list[dict[str, object]] = []
    for candidate in candidates:
        if candidate["solid_marker"]:
            accepted.append(candidate)
            continue
        aligned = sum(
            abs(float(other["center_x"]) - float(candidate["center_x"]))
            <= max(
                float(candidate["height"]) * 0.45,
                image.width * 0.012,
            )
            for other in candidates
            if other["checkbox_candidate"]
        )
        if aligned >= 2:
            accepted.append(candidate)

    checkbox_anchors = [
        candidate
        for candidate in accepted
        if candidate["checkbox_candidate"]
    ]
    if len(checkbox_anchors) >= 2:
        accepted_lines = {id(candidate["line"]) for candidate in accepted}
        for line in page.lines:
            words = _line_words(line)
            if not words or id(line) in accepted_lines:
                continue
            marker = words[0]
            if marker.bbox is None:
                continue
            token = marker.text.strip()
            strong_checkbox_fragment = bool(
                re.fullmatch(r"\[[A-Za-z]{1,3}\]?", token)
            )
            if (
                not token
                or len(token) > 4
                or (
                    not token.startswith("[")
                    and token not in {"M", "V", "v", "W"}
                )
                or (len(words) != 1 and not strong_checkbox_fragment)
            ):
                continue
            width = marker.bbox.width
            height = marker.bbox.height
            if (
                not strong_checkbox_fragment
                and (
                    min(width, height)
                    < max(8.0, min(image.size) * 0.005)
                    or height < min(image.size) * 0.03
                    or not 0.35 <= width / height <= 2.5
                )
            ):
                continue
            crop = image.crop(
                (
                    max(0, int(marker.bbox.left)),
                    max(0, int(marker.bbox.top)),
                    min(image.width, int(marker.bbox.right)),
                    min(image.height, int(marker.bbox.bottom)),
                )
            )
            histogram = crop.histogram()
            pixel_count = crop.width * crop.height
            dark_ratio = (
                sum(histogram[:128]) / pixel_count
                if pixel_count
                else 0.0
            )
            center_x = (
                marker.bbox.left + marker.bbox.right
            ) / 2
            aligned = sum(
                abs(float(anchor["center_x"]) - center_x)
                <= max(height * 0.45, image.width * 0.012)
                for anchor in checkbox_anchors
            )
            if (
                not strong_checkbox_fragment
                and (dark_ratio < 0.20 or aligned < 2)
            ):
                continue
            accepted.append(
                {
                    "line": line,
                    "word": marker,
                    "recognized_as": token,
                    "following_text": normalize_line_text(
                        [word.text for word in words[1:]]
                    )[:80],
                    "bbox": {
                        "left": round(marker.bbox.left, 1),
                        "top": round(marker.bbox.top, 1),
                        "right": round(marker.bbox.right, 1),
                        "bottom": round(marker.bbox.bottom, 1),
                    },
                    "center_x": center_x,
                    "height": height,
                    "dark_ratio": dark_ratio,
                    "solid_marker": False,
                    "checkbox_candidate": True,
                }
            )

    reports: list[dict[str, object]] = []
    for candidate in accepted:
        line = candidate["line"]
        marker = candidate["word"]
        assert isinstance(line, OcrElement)
        assert isinstance(marker, OcrElement)
        line.children = [
            child for child in line.children if child is not marker
        ]
        reports.append(
            {
                "recognized_as": candidate["recognized_as"],
                "following_text": candidate["following_text"],
                "bbox": candidate["bbox"],
                "detection": (
                    "solid_marker"
                    if candidate["solid_marker"]
                    else "aligned_checkbox"
                ),
                "dark_ratio": round(float(candidate["dark_ratio"]), 3),
            }
        )
    return reports


def _line_image_metrics(
    image: Image.Image,
    line: OcrElement,
) -> tuple[float, float, float]:
    """Measure color and ink around an OCR line to identify graphic regions."""
    if line.bbox is None:
        return (0.0, 0.0, 0.0)
    bbox = line.bbox
    padding = max(8, int(bbox.height * 0.8))
    left = max(0, int(bbox.left) - padding)
    top = max(0, int(bbox.top) - padding)
    right = min(image.width, int(bbox.right) + padding)
    bottom = min(image.height, int(bbox.bottom) + padding)
    if right <= left or bottom <= top:
        return (0.0, 0.0, 0.0)

    x_step = max(1, (right - left) // 220)
    y_step = max(1, (bottom - top) // 70)
    pixels = image.load()
    assert pixels is not None
    sampled = [
        pixels[x, y]
        for y in range(top, bottom, y_step)
        for x in range(left, right, x_step)
    ]
    if not sampled:
        return (0.0, 0.0, 0.0)
    saturated = sum(
        max(pixel) - min(pixel) > 35 and min(pixel) < 235
        for pixel in sampled
    )
    nonwhite = sum(min(pixel) < 235 for pixel in sampled)
    dark = sum(max(pixel) < 100 for pixel in sampled)
    count = len(sampled)
    return (saturated / count, nonwhite / count, dark / count)


def detect_figure_regions(
    page: OcrElement,
    image: Image.Image,
) -> list[tuple[float, float]]:
    """Detect vertical regions containing charts, diagrams, or table graphics."""
    lines = [
        line for line in page.lines if _line_words(line) and line.bbox is not None
    ]
    if len(lines) < 2:
        return []
    heights = [line.bbox.height for line in lines if line.bbox is not None]
    sorted_heights = sorted(heights)
    typical_height = (
        sorted_heights[min(len(sorted_heights) - 1, len(sorted_heights) * 3 // 4)]
        if sorted_heights
        else 1.0
    )

    candidates: list[tuple[OcrElement, bool, bool, bool, bool]] = []
    for line in lines:
        saturated, nonwhite, dark = _line_image_metrics(image, line)
        item = _line_metrics(line)
        assert line.bbox is not None
        color_graphic = saturated >= 0.012
        rule_graphic = dark >= 0.075 and nonwhite >= 0.18
        unusual_height = line.bbox.height >= typical_height * 1.7
        low_confidence_label = (
            float(item["confidence"]) < 0.62
            and int(item["characters"]) <= 18
        )
        layout_graphic = (
            unusual_height or low_confidence_label
        ) and line.bbox.width <= image.width * 0.8
        screenshot_label = (
            float(item["confidence"]) < 0.78
            and int(item["characters"]) <= 18
            and (
                saturated >= 0.005
                or bool(re.search(r"[A-Za-z<>=@#$\\|]", str(item["text"])))
            )
        )
        if color_graphic or rule_graphic or layout_graphic or screenshot_label:
            candidates.append(
                (
                    line,
                    color_graphic,
                    rule_graphic,
                    layout_graphic,
                    screenshot_label,
                )
            )
    if not candidates:
        return []

    candidates.sort(key=lambda item: item[0].bbox.top if item[0].bbox else 0)
    maximum_gap = max(typical_height * 5, image.height * 0.15)
    groups: list[list[tuple[OcrElement, bool, bool, bool, bool]]] = []
    for candidate in candidates:
        bbox = candidate[0].bbox
        assert bbox is not None
        if not groups:
            groups.append([candidate])
            continue
        previous_bbox = groups[-1][-1][0].bbox
        assert previous_bbox is not None
        if bbox.top - previous_bbox.bottom <= maximum_gap:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])

    regions: list[tuple[float, float]] = []
    for group in groups:
        color_count = sum(item[1] for item in group)
        rule_count = sum(item[2] for item in group)
        layout_count = sum(item[3] for item in group)
        screenshot_count = sum(item[4] for item in group)
        group_boxes = [
            item[0].bbox for item in group if item[0].bbox is not None
        ]
        group_top = min(box.top for box in group_boxes)
        screenshot_context = any(
            line.bbox is not None
            and line.bbox.bottom <= group_top
            and group_top - line.bbox.bottom <= image.height * 0.35
            and bool(
                re.search(
                    r"画面|イメージ",
                    str(_line_metrics(line)["text"]),
                )
            )
            for line in lines
        )
        standalone_color_label = False
        if color_count >= 2:
            region_items = [item for item in group if item[1]]
        elif rule_count >= 3:
            region_items = [item for item in group if item[2]]
        elif layout_count >= 3:
            region_items = [item for item in group if item[3]]
        elif screenshot_count >= 2 and screenshot_context:
            region_items = [item for item in group if item[4]]
        elif color_count == 1 and len(group) == 1:
            line = group[0][0]
            assert line.bbox is not None
            saturated, _, _ = _line_image_metrics(image, line)
            item = _line_metrics(line)
            standalone_color_label = (
                saturated >= 0.05
                and int(item["characters"]) <= 40
                and line.bbox.width <= image.width * 0.4
            )
            if not standalone_color_label:
                continue
            region_items = group
        else:
            continue
        boxes = [
            item[0].bbox for item in region_items if item[0].bbox is not None
        ]
        region_top = min(box.top for box in boxes)
        region_bottom = max(box.bottom for box in boxes)
        span = region_bottom - region_top
        if not standalone_color_label and not (
            (color_count >= 2 and span >= typical_height * 2.5)
            or (rule_count >= 3 and span >= typical_height * 3)
            or (layout_count >= 3 and span >= typical_height * 4)
            or (
                screenshot_count >= 2
                and screenshot_context
                and span >= typical_height * 2.5
            )
        ):
            continue
        padding = typical_height
        region_top = max(0.0, region_top - padding)
        region_bottom = min(float(image.height), region_bottom + padding)

        # Include nearby short labels and captions, but not a full-width heading
        # or the explanatory body text around the figure.
        for _ in range(2):
            changed = False
            for line in lines:
                assert line.bbox is not None
                item = _line_metrics(line)
                short_label = (
                    int(item["characters"]) <= 40
                    and line.bbox.width <= image.width * 0.4
                )
                if not short_label:
                    continue
                center_y = (line.bbox.top + line.bbox.bottom) / 2
                proximity = typical_height * 2
                if region_top - proximity <= center_y < region_top:
                    region_top = max(0.0, line.bbox.top - typical_height * 0.5)
                    changed = True
                elif region_bottom < center_y <= region_bottom + proximity:
                    region_bottom = min(
                        float(image.height),
                        line.bbox.bottom + typical_height * 0.5,
                    )
                    changed = True
            if not changed:
                break

        # A full-page cover or decorative title page is not treated as an
        # embedded figure. Require normal prose context outside the region.
        body_context = 0
        for line in lines:
            assert line.bbox is not None
            center_y = (line.bbox.top + line.bbox.bottom) / 2
            if region_top <= center_y <= region_bottom:
                continue
            item = _line_metrics(line)
            saturated, _, _ = _line_image_metrics(image, line)
            if (
                int(item["characters"]) >= 18
                and line.bbox.width >= image.width * 0.3
                and saturated < 0.005
            ):
                body_context += 1
        if body_context < 2:
            continue
        regions.append((region_top, region_bottom))
    return regions


def filter_figure_lines(page: OcrElement, image: Image.Image) -> int:
    """Remove OCR lines positioned inside detected figure regions."""
    regions = detect_figure_regions(page, image)
    removed = 0
    for line in page.lines:
        if not _line_words(line) or line.bbox is None:
            continue
        center_y = (line.bbox.top + line.bbox.bottom) / 2
        inside_region = any(top <= center_y <= bottom for top, bottom in regions)
        item = _line_metrics(line)
        near_region_noise = (
            int(item["characters"]) <= 6
            and float(item["suspicious_ratio"]) >= 0.25
            and any(
                top - image.height * 0.15
                <= center_y
                <= bottom + image.height * 0.15
                for top, bottom in regions
            )
        )
        if inside_region or near_region_noise:
            line.children = []
            removed += 1
    return removed


def filtered_page_text(page: OcrElement) -> str:
    """Serialize the filtered page while preserving its line order."""
    lines: list[str] = []
    for line in page.lines:
        words = _line_words(line)
        if not words:
            continue
        text = normalize_line_text([word.text for word in words])
        if text and not PAGE_NUMBER_RE.fullmatch(text):
            lines.append(text)
    return "\n".join(lines).strip()


def normalize_ocr_tree(page: OcrElement) -> OcrElement:
    """Merge each OCR line into one positioned word for predictable extraction."""
    for line in page.lines:
        words = _line_words(line)
        if not words or line.bbox is None:
            continue

        line_text = normalize_line_text([word.text for word in words])
        if not line_text or PAGE_NUMBER_RE.fullmatch(line_text):
            line.children = []
            continue

        confidences = [
            word.confidence for word in words if word.confidence is not None
        ]
        line.children = [
            OcrElement(
                ocr_class=OcrClass.WORD,
                bbox=line.bbox,
                text=line_text,
                confidence=min(confidences) if confidences else None,
                font=words[0].font,
                language=line.language,
                direction=line.direction,
            )
        ]
    return page


def _run_hocr(
    input_file: Path,
    options,
    page_number: int,
    suffix: str,
    pagesegmode: int | None,
) -> tuple[OcrElement, str]:
    output_hocr = input_file.with_name(
        f"{input_file.stem}.readaloud-{page_number}-{suffix}.hocr"
    )
    output_text = input_file.with_name(
        f"{input_file.stem}.readaloud-{page_number}-{suffix}.txt"
    )
    tesseract.generate_hocr(
        input_file=input_file,
        output_hocr=output_hocr,
        output_text=output_text,
        languages=options.languages,
        engine_mode=options.tesseract.oem,
        tessconfig=options.tesseract.config,
        timeout=options.tesseract.timeout,
        pagesegmode=pagesegmode,
        thresholding=options.tesseract.thresholding,
        user_words=options.tesseract.user_words,
        user_patterns=options.tesseract.user_patterns,
        omp_thread_limit=options.tesseract.omp_thread_limit,
    )
    return (
        HocrParser(output_hocr).parse(),
        output_text.read_text(encoding="utf-8"),
    )


def _write_page_artifacts(
    page_number: int,
    text: str,
    report: dict[str, object],
) -> None:
    artifact_value = os.environ.get("KINDLE_OCR_ARTIFACT_DIR", "")
    if not artifact_value:
        return
    artifact_dir = Path(artifact_value)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{page_number:06d}"
    (artifact_dir / f"{stem}.filtered.txt").write_text(text, encoding="utf-8")
    (artifact_dir / f"{stem}.quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class ReadaloudTesseractEngine(TesseractOcrEngine):
    """Tesseract engine with selective retries and confidence filtering."""

    @staticmethod
    def supports_generate_ocr() -> bool:
        return True

    @staticmethod
    def generate_ocr(
        input_file: Path,
        options,
        page_number: int = 0,
    ) -> tuple[OcrElement, str]:
        primary_mode = options.tesseract.pagesegmode
        page, raw_text = _run_hocr(
            input_file,
            options,
            page_number,
            "primary",
            primary_mode,
        )
        primary_metrics = analyze_ocr_page(page)
        selected_metrics = primary_metrics
        selected_mode = primary_mode
        retried = False

        adaptive = os.environ.get("KINDLE_OCR_ADAPTIVE", "1") == "1"
        if adaptive and should_retry_ocr(primary_metrics):
            retried = True
            alternate_mode = choose_alternate_pagesegmode(
                primary_metrics,
                primary_mode,
            )
            alternate_page, alternate_text = _run_hocr(
                input_file,
                options,
                page_number,
                "alternate",
                alternate_mode,
            )
            alternate_metrics = analyze_ocr_page(alternate_page)
            if float(alternate_metrics["score"]) > float(primary_metrics["score"]) + 1:
                page = alternate_page
                raw_text = alternate_text
                selected_metrics = alternate_metrics
                selected_mode = alternate_mode

        removed_lines = 0
        if os.environ.get("KINDLE_OCR_FILTER_LOW_CONFIDENCE", "1") == "1":
            removed_lines = filter_low_confidence_lines(page)
        figure_lines = 0
        filtered_list_markers: list[dict[str, object]] = []
        with Image.open(input_file) as input_image:
            input_rgb = input_image.convert("RGB")
            if os.environ.get("KINDLE_OCR_INCLUDE_FIGURES", "0") != "1":
                figure_lines = filter_figure_lines(
                    page,
                    input_rgb,
                )
            if os.environ.get("KINDLE_OCR_INCLUDE_LIST_MARKERS", "0") != "1":
                filtered_list_markers = filter_list_marker_words(
                    page,
                    input_rgb,
                )
        reordered_elements = reorder_ocr_tree_by_position(page)
        corrections: dict[str, int] = {}
        correction_profiles = correction_profiles_from_environment()
        if os.environ.get("KINDLE_OCR_CORRECT_COMMON_ERRORS", "1") == "1":
            corrections = apply_common_ocr_corrections(
                page,
                profiles=correction_profiles,
            )
        retried_lines = 0
        if adaptive:
            with Image.open(input_file) as input_image:
                retried_lines = retry_review_candidate_lines(
                    page,
                    input_image,
                    options,
                    page_number,
                )
        if (
            retried_lines
            and os.environ.get("KINDLE_OCR_CORRECT_COMMON_ERRORS", "1") == "1"
        ):
            retry_corrections = apply_common_ocr_corrections(
                page,
                profiles=correction_profiles,
            )
            corrections = dict(
                sorted((Counter(corrections) + Counter(retry_corrections)).items())
            )
        review_candidates = find_ocr_review_candidates(page)
        filtered_text = filtered_page_text(page)
        report: dict[str, object] = {
            "page": page_number + 1,
            "primary_pagesegmode": primary_mode,
            "selected_pagesegmode": selected_mode,
            "retried": retried,
            "filtered_lines": removed_lines,
            "filtered_figure_lines": figure_lines,
            "filtered_list_marker_count": len(filtered_list_markers),
            "filtered_list_markers": filtered_list_markers,
            "reordered_elements": reordered_elements,
            "retried_lines": retried_lines,
            "correction_count": sum(corrections.values()),
            "corrections": corrections,
            "correction_profiles": sorted(correction_profiles),
            "review_candidate_count": len(review_candidates),
            "review_candidates": review_candidates,
            **selected_metrics,
        }
        _write_page_artifacts(page_number, filtered_text, report)
        return normalize_ocr_tree(page), raw_text


@hookimpl(tryfirst=True)
def get_ocr_engine(options):
    """Prefer the adaptive engine when this plugin was explicitly loaded."""
    if options is None:
        return ReadaloudTesseractEngine()
    if getattr(options, "ocr_engine", "auto") in ("auto", "tesseract"):
        return ReadaloudTesseractEngine()
    return None
