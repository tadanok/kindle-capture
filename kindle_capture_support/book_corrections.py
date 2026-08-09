"""Opt-in corrections for the RAG accuracy source book.

These replacements encode wording and layout from one specific book. They are
kept outside the generic OCR rules so that the default profile can never apply
them to unrelated content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kindle_capture_support.correction_rules import CorrectionRule, ReviewRule


RAG_ACCURACY_BOOK_CORRECTIONS = (
    CorrectionRule(
        profile='rag-accuracy-book',
        name='HIE RAG -> 第3章 RAG',
        pattern=re.compile(r"(?<![A-Za-z])HIE(?=\s*RAG\s*精度改善)"),
        replacement='第3章',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='宮崎験 -> 宮崎駿',
        pattern=re.compile(r"宮崎験"),
        replacement='宮崎駿',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='宮崎駿一 (会社 -> 宮崎駿 — (会社',
        pattern=re.compile(r"宮崎駿一(?=\s*\(会社)"),
        replacement='宮崎駿 —',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='numbered-list underscore -> removed',
        pattern=re.compile(r"(?<=\d\.)\s*_\s*(?=前処理)"),
        replacement=' ',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='_ LanceDB -> LanceDB',
        pattern=re.compile(r"(?<![A-Za-z])_\s+(?=LanceDB\b)"),
        replacement='',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='Hallucination) J -> Hallucination)」',
        pattern=re.compile(r"Hallucination\)\s*J"),
        replacement='Hallucination)」',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='説明 ET, -> 説明します。',
        pattern=re.compile(r"説明\s+ET,"),
        replacement='説明します。',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='構成されま i : -> 構成されます：',
        pattern=re.compile(r"構成されま\s+i\s*[:：]"),
        replacement='構成されます：',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='です。 HF -> です。研',
        pattern=re.compile(r"です。\s*HF\b"),
        replacement='です。研',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='完レポート -> 究レポート',
        pattern=re.compile(r"完レポート"),
        replacement='究レポート',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='整理じてでておりまずすず -> 整理しております',
        pattern=re.compile(r"^整理じてでておりまずすず\s*[:：]"),
        replacement='整理しております：',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='じてでておりまずすず -> しております',
        pattern=re.compile(r"^じてでておりまずすず\s*[:：]"),
        replacement='しております：',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='retrieval chapter sentence recovery',
        pattern=re.compile(r"^AELOET,\s*EL,\s*FOFEY\s+THEE\]\s*TRH\s+ERA,\s*データの"),
        replacement='をまとめます。ただし、どの手法も「万能」ではありません。データの',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='来てまずよ/という ? う形 -> 来てますよ/という形',
        pattern=re.compile(r"来てまずよ」どという\s*\?\s*う形"),
        replacement='来てますよ」という形',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='本書の最初の草 -> 本書の最初の章',
        pattern=re.compile(r"本書の最初の草"),
        replacement='本書の最初の章',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='徹し込む -> 流し込む',
        pattern=re.compile(r"徹し込む"),
        replacement='流し込む',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='クエリに登場しレた -> クエリに登場した',
        pattern=re.compile(r"クエリに登場しレた"),
        replacement='クエリに登場した',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='ノフード間 -> ノード間',
        pattern=re.compile(r"ノフード間"),
        replacement='ノード間',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='精統化 -> 精緻化',
        pattern=re.compile(r"精統化"),
        replacement='精緻化',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='流輸さ -> 流暢さ',
        pattern=re.compile(r"流輸さ"),
        replacement='流暢さ',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='モニタリンググ基盤 -> モニタリング基盤',
        pattern=re.compile(r"モニタリンググ基盤"),
        replacement='モニタリング基盤',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='マッピングレし -> マッピングし',
        pattern=re.compile(r"マッピングレし"),
        replacement='マッピングし',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='検索記/刀推論 -> 検索&推論',
        pattern=re.compile(r"検索[記刀]推論"),
        replacement='検索&推論',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='翼境 -> 環境',
        pattern=re.compile(r"翼境"),
        replacement='環境',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='ソツール -> ツール',
        pattern=re.compile(r"ソツール"),
        replacement='ツール',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='場合なあります -> 場合があります',
        pattern=re.compile(r"場合なあります"),
        replacement='場合があります',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='人歓迎します -> 歓迎します',
        pattern=re.compile(r"人歓迎します"),
        replacement='歓迎します',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='精度改番 -> 精度改善',
        pattern=re.compile(r"精度改番"),
        replacement='精度改善',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='根拠に思実か -> 根拠に忠実か',
        pattern=re.compile(r"根拠に思実か"),
        replacement='根拠に忠実か',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='下がりやすぐ -> 下がりやすく',
        pattern=re.compile(r"下がりやすぐ"),
        replacement='下がりやすく',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='マルチモーダレル -> マルチモーダル',
        pattern=re.compile(r"マルチモーダレル"),
        replacement='マルチモーダル',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='画像トナテキスト -> 画像やテキスト',
        pattern=re.compile(r"画像トナテキスト"),
        replacement='画像やテキスト',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='物在のよりモダン -> 現在のよりモダン',
        pattern=re.compile(r"物在のよりモダン"),
        replacement='現在のよりモダン',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='ブフォーマシト -> フォーマット',
        pattern=re.compile(r"ブフォーマシト"),
        replacement='フォーマット',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='いっつた -> いった',
        pattern=re.compile(r"いっ\s*つた"),
        replacement='いった',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='比較レて -> 比較して',
        pattern=re.compile(r"比較レて"),
        replacement='比較して',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='BELベル -> 段落レベル',
        pattern=re.compile(r"BELベル"),
        replacement='段落レベル',
    ),
    CorrectionRule(
        profile='rag-accuracy-book',
        name='元の文書き。 -> 元の文書や、',
        pattern=re.compile(r"元の文書き。"),
        replacement='元の文書や、',
    ),
)


RAG_ACCURACY_BOOK_OCR_REVIEW_RULES = (
    ReviewRule(
        profile="rag-accuracy-book",
        reason="repeated_book_kana",
        pattern=re.compile(r"じてでて|まずすず|すず"),
    ),
    ReviewRule(
        profile="rag-accuracy-book",
        reason="embedded_book_ocr_noise",
        pattern=re.compile(
            r"比較レ|レポー\s*\d|HIE\s+RAG|宮崎験|WET,|To ARAL|AELOET"
        ),
    ),
)


RAG_ACCURACY_BOOK_POST_OCR_REVIEW_RULES = (
    ReviewRule(
        profile="rag-accuracy-book",
        reason="confirmed_book_ocr_error",
        pattern=re.compile(
            r"じてでて|まずすず|AELOET|比較レて|来てまずよ|HIE\s*RAG|"
            r"中み出|To ARAL|=ークリッド|宮崎験|WET,|いっ\s*つた|"
            r"ソツール|場合なあります|人歓迎|ユューザー|根拠に思実|"
            r"やすぐ|マルチモーダレル|画像トナ|ブフォーマシト"
        ),
    ),
    ReviewRule(
        profile="rag-accuracy-book",
        reason="reading_order",
        pattern=re.compile(r"す。[「『]構造化"),
    ),
)


@dataclass
class BookLineCorrection:
    """Result of applying source-book-only cross-line repairs."""

    text: str
    remove: bool = False
    corrections: dict[str, int] = field(default_factory=dict)


def correct_book_line_context(
    previous_text: str,
    text: str,
    next_text: str,
) -> BookLineCorrection:
    """Apply cross-line repairs that are valid only for the source book."""
    if (
        previous_text.endswith("構成されま")
        and re.fullmatch(r"i\s*[:：]", text)
    ):
        return BookLineCorrection(text="", remove=True)
    if (
        text.startswith("(人名、組織、場所、出来事")
        and next_text.startswith("e ノード:")
    ):
        return BookLineCorrection(text="", remove=True)
    if text == "す。" and next_text.startswith("「構造化された関係性」"):
        return BookLineCorrection(text="", remove=True)

    corrected = text
    corrections: dict[str, int] = {}
    if corrected.endswith("構成されま") and re.fullmatch(r"i\s*[:：]", next_text):
        corrected += "す："
        corrections["split 構成されま/i : -> 構成されます："] = 1
    if corrected.endswith("一歩を中") and next_text.startswith("み出したい"):
        corrected = corrected[:-1]
        corrections["一歩を中/み出す -> 一歩を踏み出す"] = 1
    if previous_text.endswith("一歩を中") and corrected.startswith("み出したい"):
        corrected = "踏" + corrected
        corrections["中/み出したい -> 踏み出したい"] = 1
    if corrected.endswith("なりま") and next_text.startswith(
        "To ARAL のブラックボックス性"
    ):
        corrected += "す。"
        corrections["なりま/To ARAL -> なります。生成 AI"] = 1
    if previous_text.endswith("なりま") and corrected.startswith(
        "To ARAL のブラックボックス性"
    ):
        corrected = re.sub(r"^To ARAL の", "生成 AI の", corrected)
        corrections["To ARAL の -> 生成 AI の"] = 1
    if corrected.endswith("定番で") and next_text == "Te":
        corrected += "す。"
        corrections["定番で/Te -> 定番です。"] = 1
    if corrected == "Te" and previous_text.endswith("定番で"):
        return BookLineCorrection(
            text="",
            remove=True,
            corrections={"Te after 定番で -> removed": 1},
        )
    if corrected.endswith("と =") and next_text.startswith("ークリッド距離"):
        corrected = re.sub(r"=$", "ユ", corrected)
        corrections["=/ークリッド -> ユークリッド"] = 1
    if corrected == "WET,":
        if previous_text.endswith("提案して"):
            corrected = "います。"
            corrections["提案して/WET, -> 提案しています。"] = 1
        elif previous_text.endswith("向いて"):
            corrected = "います。"
            corrections["向いて/WET, -> 向いています。"] = 1
    if previous_text.endswith("下がり") and corrected.startswith("やすぐ"):
        corrected = re.sub(r"^やすぐ", "やすく", corrected)
        corrections["下がり/やすぐ -> 下がりやすく"] = 1
    if previous_text.endswith("パイプライ"):
        corrected, count = re.subn(r"^ジ(?=です)", "ン", corrected)
        if count:
            corrections["ジ -> ン (after パイプライ)"] = count
    if previous_text.endswith("とい"):
        corrected, count = re.subn(r"^っつた(?=自動スコア)", "った", corrected)
        if count:
            corrections["とい/っつた -> といった"] = count
    if previous_text.startswith("(人名、組織、場所、出来事") and corrected.startswith(
        "e ノード:"
    ):
        corrected += " " + previous_text
        corrections["node detail before label -> label before node detail"] = 1
    if (
        previous_text == "す。"
        and corrected.startswith("「構造化された関係性」")
        and corrected.endswith("特徴で")
    ):
        corrected += "す。"
        corrections["misordered sentence end -> sentence end"] = 1
    if corrected.endswith("BEL") and next_text.startswith("ベルや"):
        corrected = re.sub(r"BEL$", "段落レ", corrected)
        corrections["BEL/ベル -> 段落レベル"] = 1
    if corrected.endswith("比較レ") and next_text.startswith("て、"):
        corrected = re.sub(r"比較レ$", "比較し", corrected)
        corrections["比較レ/て -> 比較して"] = 1
    if previous_text.endswith("来てま"):
        corrected, count = re.subn(
            r"^ずよ」どという\s*\?\s*う形",
            "すよ」という形",
            corrected,
        )
        if count:
            corrections["来てま/ずよどという ? う形 -> 来てますよという形"] = count
    if previous_text.endswith("イメージで") and re.fullmatch(
        r"すず?\s*[:：]", corrected
    ):
        corrected = "す："
        corrections["すず : -> す： (after イメージで)"] = 1
    if corrected.endswith("パイプライ") and not next_text.startswith(
        ("ジです", "ンです")
    ):
        corrected += "ン"
        corrections["パイプライ -> パイプライン (missing continuation)"] = 1
    if re.fullmatch(
        r"\d+(?:\.\d+){2}\s+(?:Self-RAG|Agentic RAG|RAG-Reasoning)",
        corrected,
    ):
        corrected += "："
        corrections["section heading -> section heading："] = 1
    return BookLineCorrection(text=corrected, corrections=corrections)
