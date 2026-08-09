"""Shared OCR configuration used by the CLI and OCRmyPDF plugin."""

from __future__ import annotations

import re
from pathlib import Path


SUPPORT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SUPPORT_DIR.parent

JAPANESE_CHARACTER = (
    r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff66-\uff9f"
)
JAPANESE_PUNCTUATION = r"、。，．・：；！？「」『』（）［］【】〈〉《》〔〕｛｝"
JAPANESE_OR_PUNCTUATION = JAPANESE_CHARACTER + JAPANESE_PUNCTUATION
PAGE_NUMBER_RE = re.compile(r"^[\s\-–—―]*\d+[\s\-–—―]*$")

DEFAULT_BEST_TESSDATA_DIR = SUPPORT_DIR / "ocr_models" / "tessdata_best"
DEFAULT_OCR_DICTIONARY_DIR = SUPPORT_DIR / "ocr_dictionaries"
DEFAULT_OCR_DICTIONARIES = ("common",)
DEFAULT_OCR_CORRECTION_PROFILES = ("common",)
DEFAULT_OCR_LANGUAGE = "jpn+eng"
DEFAULT_OCR_MODEL = "best"
DEFAULT_OCR_LAYOUT = "horizontal"
DEFAULT_OCR_CONTENT_TYPE = "document"
DEFAULT_MANGA_TEXT_SCOPE = "narrative"
DEFAULT_OCR_OVERSAMPLE_DPI = 300
DEFAULT_PDF_TEXT_LAYER = "readaloud"
DEFAULT_OCR_ADAPTIVE = True
DEFAULT_FILTER_LOW_CONFIDENCE = True
DEFAULT_OCR_CORRECTIONS_ENABLED = True
DEFAULT_INCLUDE_FIGURE_TEXT = False
DEFAULT_INCLUDE_LIST_MARKERS = False

OCR_TESSDATA_BEST_ENV = "KINDLE_OCR_TESSDATA_BEST"
OCR_VISION_HELPER_ENV = "KINDLE_OCR_VISION_HELPER"
OCR_CONTENT_TYPE_ENV = "KINDLE_OCR_CONTENT_TYPE"
OCR_MANGA_TEXT_SCOPE_ENV = "KINDLE_OCR_MANGA_TEXT_SCOPE"
OCR_ARTIFACT_DIR_ENV = "KINDLE_OCR_ARTIFACT_DIR"
OCR_ADAPTIVE_ENV = "KINDLE_OCR_ADAPTIVE"
OCR_FILTER_LOW_CONFIDENCE_ENV = "KINDLE_OCR_FILTER_LOW_CONFIDENCE"
OCR_CORRECTIONS_ENABLED_ENV = "KINDLE_OCR_CORRECTIONS_ENABLED"
OCR_CORRECTION_PROFILES_ENV = "KINDLE_OCR_CORRECTION_PROFILES"
OCR_INCLUDE_FIGURES_ENV = "KINDLE_OCR_INCLUDE_FIGURES"
OCR_INCLUDE_LIST_MARKERS_ENV = "KINDLE_OCR_INCLUDE_LIST_MARKERS"


def expand_correction_profiles(
    profiles: list[str] | set[str] | frozenset[str] | tuple[str, ...] | None,
) -> frozenset[str]:
    """Expand profile dependencies from a single shared definition."""
    expanded = set(
        DEFAULT_OCR_CORRECTION_PROFILES if profiles is None else profiles
    )
    if "rag-accuracy-book" in expanded:
        expanded.update({"common", "ai-rag"})
    if "ai-rag" in expanded:
        expanded.add("common")
    return frozenset(expanded)
