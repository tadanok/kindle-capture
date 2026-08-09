"""Opt-in corrections for the RAG accuracy source book.

These replacements encode wording and layout from one specific book.  They are
kept outside the generic OCR rules so that the default profile can never apply
them to unrelated content.
"""

from __future__ import annotations

import re


RAG_ACCURACY_BOOK_CORRECTIONS = (
    (
        "HIE RAG -> 第3章 RAG",
        re.compile(r"(?<![A-Za-z])HIE(?=\s*RAG\s*精度改善)"),
        "第3章",
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
        re.compile(r"^AELOET,\s*EL,\s*FOFEY\s+THEE\]\s*TRH\s+ERA,\s*データの"),
        "をまとめます。ただし、どの手法も「万能」ではありません。データの",
    ),
    (
        "来てまずよ/という ? う形 -> 来てますよ/という形",
        re.compile(r"来てまずよ」どという\s*\?\s*う形"),
        "来てますよ」という形",
    ),
)

RAG_ACCURACY_BOOK_CORRECTION_NAMES = frozenset(
    name for name, _pattern, _replacement in RAG_ACCURACY_BOOK_CORRECTIONS
)
