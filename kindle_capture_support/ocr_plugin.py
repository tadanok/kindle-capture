"""OCRmyPDF plugin for adaptive, read-aloud-friendly Japanese OCR."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from statistics import median

from ocrmypdf import BoundingBox, OcrClass, OcrElement, hookimpl
from ocrmypdf._exec import tesseract
from ocrmypdf.builtin_plugins.tesseract_ocr import TesseractOcrEngine
from ocrmypdf.hocrtransform import HocrParser
from PIL import Image

from kindle_capture_support.book_corrections import (
    RAG_ACCURACY_BOOK_CORRECTIONS,
    RAG_ACCURACY_BOOK_OCR_REVIEW_RULES,
    correct_book_line_context,
)
from kindle_capture_support.correction_rules import (
    OCR_CORRECTION_RULES,
    OCR_REVIEW_RULES,
)
from kindle_capture_support.ocr_config import (
    DEFAULT_OCR_CORRECTIONS_ENABLED,
    DEFAULT_FILTER_LOW_CONFIDENCE,
    DEFAULT_INCLUDE_FIGURE_TEXT,
    DEFAULT_INCLUDE_LIST_MARKERS,
    DEFAULT_MANGA_TEXT_SCOPE,
    DEFAULT_OCR_ADAPTIVE,
    DEFAULT_OCR_CONTENT_TYPE,
    DEFAULT_OCR_CORRECTION_PROFILES,
    JAPANESE_CHARACTER,
    JAPANESE_OR_PUNCTUATION,
    PAGE_NUMBER_RE,
    OCR_ADAPTIVE_ENV,
    OCR_ARTIFACT_DIR_ENV,
    OCR_CONTENT_TYPE_ENV,
    OCR_CORRECTIONS_ENABLED_ENV,
    OCR_CORRECTION_PROFILES_ENV,
    OCR_FILTER_LOW_CONFIDENCE_ENV,
    OCR_INCLUDE_FIGURES_ENV,
    OCR_INCLUDE_LIST_MARKERS_ENV,
    OCR_MANGA_TEXT_SCOPE_ENV,
    OCR_VISION_HELPER_ENV,
    expand_correction_profiles,
)

MEANINGFUL_CHARACTER_RE = re.compile(f"[A-Za-z0-9{JAPANESE_CHARACTER}]")
SAFE_PUNCTUATION = set("、。，．・：；！？「」『』（）［］【】〈〉《》〔〕｛｝.,:;!?()[]{}<>+-/%&@#'\"")
ALL_OCR_CORRECTIONS = (
    OCR_CORRECTION_RULES + RAG_ACCURACY_BOOK_CORRECTIONS
)
ALL_OCR_REVIEW_RULES = OCR_REVIEW_RULES + RAG_ACCURACY_BOOK_OCR_REVIEW_RULES


def correction_profiles_from_environment() -> frozenset[str]:
    value = os.environ.get(
        OCR_CORRECTION_PROFILES_ENV,
        ",".join(DEFAULT_OCR_CORRECTION_PROFILES),
    )
    return expand_correction_profiles(
        {profile.strip() for profile in value.split(",") if profile.strip()}
    )


def environment_flag(name: str, default: bool) -> bool:
    """Read an OCR boolean from the shared environment protocol."""
    return os.environ.get(name, "1" if default else "0") == "1"


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
PROGRAMMING_TEXT_RE = re.compile(
    r"(?ix)"
    r"(?:^|[^A-Za-z0-9_])"
    r"(?:import|from|def|class|return|const|let|var|function|export|"
    r"interface|system\.out|print|async|await|SELECT|INSERT|UPDATE|"
    r"DELETE|CREATE|SQL\s+DUMP|CODE\s+BASE)"
    r"(?:$|[^A-Za-z0-9_])"
    r"|=>|==|!=|</?[A-Za-z]|[{}]"
)
COMPACT_UI_TEXT_RE = re.compile(
    r"(?ix)^(?:editor\s+ui|user\s+question|asking\s+ai|"
    r"ai\s+responds(?:\s+with\s+code)?|terminal|codebase|"
    r"legacy\s+repo|function\s+logs|java\s+class|sql\s+dump|"
    r"documentation|pull\s+request|processing|queued)$"
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


def correct_ocr_misrecognitions(
    text: str,
    profiles: set[str] | frozenset[str] | None = None,
) -> tuple[str, dict[str, int]]:
    """Correct only exact or narrowly contextualized, recurring OCR errors."""
    corrected = text
    corrections: Counter[str] = Counter()
    active_profiles = expand_correction_profiles(profiles)
    for rule in ALL_OCR_CORRECTIONS:
        if rule.profile not in active_profiles:
            continue
        corrected, count = rule.pattern.subn(rule.replacement, corrected)
        if count:
            corrections[rule.name] += count
    return corrected, dict(sorted(corrections.items()))


def apply_ocr_corrections(
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
        if split_title_continuation:
            line.children = []
            continue

        corrected_text, line_corrections = correct_ocr_misrecognitions(
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
            ai_rag_enabled
            and re.search(r"(?<![A-Za-z0-9])Al$", corrected_text)
            and re.match(r"^の実務経験", next_line_text)
        ):
            corrected_text = re.sub(r"Al$", "AI", corrected_text)
            line_corrections["Al -> AI (before next-line の実務経験)"] = 1
        if book_profile_enabled:
            book_result = correct_book_line_context(
                previous_line_text,
                corrected_text,
                next_line_text,
            )
            if book_result.remove:
                line.children = []
                corrections.update(book_result.corrections)
                continue
            corrected_text = book_result.text
            line_corrections.update(book_result.corrections)
        if not line_corrections:
            continue

        replace_line_text(line, words, corrected_text)
        corrections.update(line_corrections)
    return dict(sorted(corrections.items()))


def find_ocr_review_candidates(
    page: OcrElement,
    profiles: set[str] | frozenset[str] | None = None,
) -> list[dict[str, object]]:
    """Return suspicious surviving lines without changing their text."""
    candidates: list[dict[str, object]] = []
    for line in page.lines:
        words = _line_words(line)
        if not words:
            continue
        item = _line_metrics(line)
        reasons = _line_review_reasons(item, profiles=profiles)
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
    profiles: set[str] | frozenset[str] | None = None,
) -> list[str]:
    text = str(item["text"])
    active_profiles = expand_correction_profiles(profiles)
    reasons = [
        rule.reason
        for rule in ALL_OCR_REVIEW_RULES
        if rule.profile in active_profiles and rule.pattern.search(text)
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
    profiles: set[str] | frozenset[str] | None = None,
) -> bool:
    """Accept a line retry only when it is materially safer than the original."""
    original_text = str(original["text"])
    alternative_text = str(alternative["text"])
    if _line_review_reasons(alternative, profiles=profiles):
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
    profiles: set[str] | frozenset[str] | None = None,
) -> int:
    """Retry only suspicious lines using a scaled single-line crop."""
    retried = 0
    image = image.convert("RGB")
    for line_index, line in enumerate(list(page.lines)):
        words = _line_words(line)
        if not words or line.bbox is None:
            continue
        original = _line_metrics(line)
        if not _line_review_reasons(original, profiles=profiles):
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
        if not should_accept_line_retry(
            original,
            alternative,
            profiles=profiles,
        ):
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
        "score": round(max(0.0, min(100.0, score)), 2),
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


def detect_manga_narrative_regions(image: Image.Image) -> list[BoundingBox]:
    """Detect compact enclosed light/dark regions that resemble speech bubbles."""
    working = image.convert("RGB")
    working.thumbnail((300, 480))
    width, height = working.size
    get_pixels = getattr(working, "get_flattened_data", working.getdata)
    pixels = list(get_pixels())
    regions: list[BoundingBox] = []

    def collect(mask: list[bool]) -> None:
        seen = bytearray(width * height)
        for start, enabled in enumerate(mask):
            if not enabled or seen[start]:
                continue
            queue = [start]
            seen[start] = 1
            cursor = 0
            area = 0
            min_x = width
            min_y = height
            max_x = 0
            max_y = 0
            while cursor < len(queue):
                index = queue[cursor]
                cursor += 1
                y, x = divmod(index, width)
                area += 1
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                for neighbor in (index - 1, index + 1, index - width, index + width):
                    if (
                        neighbor < 0
                        or neighbor >= width * height
                        or seen[neighbor]
                        or not mask[neighbor]
                    ):
                        continue
                    neighbor_y, neighbor_x = divmod(neighbor, width)
                    if abs(neighbor_x - x) + abs(neighbor_y - y) != 1:
                        continue
                    seen[neighbor] = 1
                    queue.append(neighbor)
            box_width = max_x - min_x + 1
            box_height = max_y - min_y + 1
            width_ratio = box_width / width
            height_ratio = box_height / height
            fill_ratio = area / (box_width * box_height)
            if (
                area >= width * height * 0.002
                and 0.08 <= width_ratio <= 0.50
                and 0.05 <= height_ratio <= 0.42
                and 0.30 <= fill_ratio <= 0.82
            ):
                scale_x = image.width / width
                scale_y = image.height / height
                regions.append(
                    BoundingBox(
                        min_x * scale_x,
                        min_y * scale_y,
                        (max_x + 1) * scale_x,
                        (max_y + 1) * scale_y,
                    )
                )

    collect(
        [
            min(pixel) >= 225 and max(pixel) - min(pixel) <= 32
            for pixel in pixels
        ]
    )
    collect([max(pixel) <= 55 for pixel in pixels])
    return regions


def filter_manga_non_narrative_lines(
    page: OcrElement,
    image: Image.Image,
) -> int:
    """Keep manga titles and speech-bubble text while removing screen content."""
    if page.bbox is None or page.bbox.height <= 0:
        return 0
    narrative_regions = detect_manga_narrative_regions(image)
    entries: list[
        tuple[
            OcrElement,
            str,
            float,
            bool,
            bool,
            bool,
            list[int],
        ]
    ] = []
    contaminated_regions: set[int] = set()
    for line in page.lines:
        words = _line_words(line)
        if not words or line.bbox is None:
            continue
        text = normalize_line_text([word.text for word in words])
        normalized = unicodedata.normalize("NFKC", text)
        visible = [character for character in normalized if not character.isspace()]
        if not visible:
            continue
        relative_height = line.bbox.height / page.bbox.height
        japanese_count = len(re.findall(f"[{JAPANESE_CHARACTER}]", normalized))
        ascii_count = len(re.findall(r"[A-Za-z0-9]", normalized))
        ascii_ratio = ascii_count / len(visible)
        code_like = bool(PROGRAMMING_TEXT_RE.search(normalized))
        compact_ui = bool(COMPACT_UI_TEXT_RE.fullmatch(normalized.strip()))
        ascii_ui = (
            ascii_ratio >= 0.65
            and japanese_count <= 1
            and relative_height < 0.04
        )
        center_x = (line.bbox.left + line.bbox.right) / 2
        center_y = (line.bbox.top + line.bbox.bottom) / 2
        region_indexes = [
            index
            for index, region in enumerate(narrative_regions)
            if region.left <= center_x <= region.right
            and region.top <= center_y <= region.bottom
        ]
        if code_like or compact_ui or ascii_ui:
            contaminated_regions.update(region_indexes)
        entries.append(
            (
                line,
                normalized,
                relative_height,
                code_like,
                compact_ui,
                ascii_ui,
                region_indexes,
            )
        )

    removed = 0
    for (
        line,
        normalized,
        relative_height,
        code_like,
        compact_ui,
        ascii_ui,
        region_indexes,
    ) in entries:
        assert line.bbox is not None
        japanese_count = len(re.findall(f"[{JAPANESE_CHARACTER}]", normalized))
        ascii_count = len(re.findall(r"[A-Za-z0-9]", normalized))
        visible_count = len(
            [character for character in normalized if not character.isspace()]
        )
        ascii_ratio = ascii_count / visible_count if visible_count else 0.0
        large_title = (
            relative_height >= 0.04
            and (
                line.bbox.width >= line.bbox.height * 1.2
                or japanese_count >= 2
            )
        )
        title_pattern = bool(
            re.search(r"[『「【].+[』」】]|第\s*\d+\s*[章世代]", normalized)
        )
        upper_title = (
            line.bbox.top <= page.bbox.height * 0.20
            and relative_height >= 0.025
        )
        inside_narrative_region = any(
            index not in contaminated_regions for index in region_indexes
        )
        keep = (
            large_title
            or title_pattern
            or upper_title
            or inside_narrative_region
        )
        ascii_screen_text = (
            ascii_ratio >= 0.65
            and japanese_count <= 1
            and not large_title
        )
        if (
            not keep
            or code_like
            or compact_ui
            or ascii_ui
            or ascii_screen_text
        ):
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
    languages=None,
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
        languages=languages if languages is not None else options.languages,
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


def parse_vision_ocr_output(
    output: str,
    image_width: int,
    image_height: int,
) -> tuple[OcrElement, str]:
    """Convert the native Vision helper's normalized TSV into OCR elements."""
    lines: list[OcrElement] = []
    text_lines: list[str] = []
    for raw_line in output.splitlines():
        fields = raw_line.split("\t", 5)
        if len(fields) != 6:
            continue
        try:
            x, y, width, height, confidence = (
                float(value) for value in fields[:5]
            )
        except ValueError:
            continue
        text = fields[5].strip()
        if not text or not MEANINGFUL_CHARACTER_RE.search(text):
            continue
        left = max(0.0, min(float(image_width), x * image_width))
        right = max(left, min(float(image_width), (x + width) * image_width))
        top = max(
            0.0,
            min(float(image_height), (1.0 - y - height) * image_height),
        )
        bottom = max(
            top,
            min(float(image_height), (1.0 - y) * image_height),
        )
        if right <= left or bottom <= top:
            continue
        box = BoundingBox(left, top, right, bottom)
        lines.append(
            OcrElement(
                ocr_class=OcrClass.LINE,
                bbox=box,
                children=[
                    OcrElement(
                        ocr_class=OcrClass.WORD,
                        bbox=box,
                        text=text,
                        confidence=max(0.0, min(1.0, confidence)),
                        language="ja",
                    )
                ],
            )
        )
        text_lines.append(text)
    large_lines = [
        line
        for line in lines
        if line.bbox is not None
        and line.bbox.height >= image_height * 0.04
    ]
    if (
        len(large_lines) >= 3
        and sum(
            len(word.text or "")
            for line in large_lines
            for word in _line_words(line)
        )
        >= 15
    ):
        lines = large_lines
        text_lines = [
            normalize_line_text([word.text or "" for word in _line_words(line)])
            for line in lines
        ]
    page = OcrElement(
        ocr_class=OcrClass.PAGE,
        bbox=BoundingBox(0, 0, image_width, image_height),
        children=[
            OcrElement(
                ocr_class=OcrClass.PARAGRAPH,
                children=lines,
            )
        ],
    )
    return page, "\n".join(text_lines).strip()


def run_vision_ocr(
    input_file: Path,
    image_width: int,
    image_height: int,
) -> tuple[OcrElement, str] | None:
    """Run the opt-in, local macOS Vision helper for manga pages."""
    helper_value = os.environ.get(OCR_VISION_HELPER_ENV, "")
    if not helper_value:
        return None
    try:
        result = subprocess.run(
            [helper_value, str(input_file)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    page, text = parse_vision_ocr_output(
        result.stdout,
        image_width,
        image_height,
    )
    metrics = analyze_ocr_page(page)
    if (
        int(metrics["line_count"]) < 2
        or int(metrics["character_count"]) < 10
    ):
        return None
    return page, text


def clean_manga_region_text(text: str) -> str:
    """Normalize a cropped speech-bubble result and drop isolated OCR debris."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"(?<![A-Za-z0-9])Al(?![A-Za-z0-9])", "AI", normalized)
    tokens: list[str] = []
    for token in re.split(r"\s+", normalized):
        token = token.strip()
        if not token:
            continue
        if re.fullmatch(r"[A-Za-z]{1,2}", token) and token.upper() not in KNOWN_ACRONYMS:
            continue
        if not MEANINGFUL_CHARACTER_RE.search(token):
            continue
        tokens.append(token)
    joined = " ".join(tokens)
    return re.sub(
        f"(?<=[{JAPANESE_OR_PUNCTUATION}])[ \t]+"
        f"(?=[{JAPANESE_OR_PUNCTUATION}])",
        "",
        joined,
    ).strip()


def run_manga_region_ocr(
    input_file: Path,
    input_image: Image.Image,
    options,
    page_number: int,
) -> tuple[OcrElement, str, int, int]:
    """OCR detected manga bubbles individually with vertical/horizontal models."""
    regions = detect_manga_narrative_regions(input_image)
    accepted_lines: list[OcrElement] = []
    accepted_text: list[str] = []
    padding = max(8, round(min(input_image.size) * 0.012))
    with tempfile.TemporaryDirectory(prefix="kindle-manga-regions-") as directory:
        root = Path(directory)
        for index, region in enumerate(regions):
            crop_box = (
                max(0, round(region.left) - padding),
                max(0, round(region.top) - padding),
                min(input_image.width, round(region.right) + padding),
                min(input_image.height, round(region.bottom) + padding),
            )
            if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                continue
            crop = input_image.crop(crop_box).convert("RGB")
            crop_path = root / f"region-{index:03d}.png"
            crop.save(crop_path, format="PNG")
            vertical = crop.height > crop.width * 1.08
            languages = (
                ["jpn_vert", "eng"] if vertical else ["jpn", "eng"]
            )
            pagesegmode = 5 if vertical else 6
            try:
                crop_page, crop_text = _run_hocr(
                    crop_path,
                    options,
                    page_number,
                    f"region-{index:03d}",
                    pagesegmode,
                    languages=languages,
                )
            except (OSError, subprocess.SubprocessError, UnicodeError):
                continue
            filter_low_confidence_lines(crop_page)
            text = clean_manga_region_text(
                filtered_page_text(crop_page) or crop_text
            )
            metrics = analyze_ocr_page(crop_page)
            japanese_count = len(re.findall(f"[{JAPANESE_CHARACTER}]", text))
            if (
                japanese_count < 2
                or len(text) < 2
                or float(metrics["mean_confidence"]) < 0.20
                or float(metrics["suspicious_ratio"]) > 0.35
                or PROGRAMMING_TEXT_RE.search(text)
            ):
                continue
            box = BoundingBox(*crop_box)
            accepted_lines.append(
                OcrElement(
                    ocr_class=OcrClass.LINE,
                    bbox=box,
                    children=[
                        OcrElement(
                            ocr_class=OcrClass.WORD,
                            bbox=box,
                            text=text,
                            confidence=float(metrics["mean_confidence"]),
                            language="ja",
                        )
                    ],
                )
            )
            accepted_text.append(text)
    page = OcrElement(
        ocr_class=OcrClass.PAGE,
        bbox=BoundingBox(0, 0, input_image.width, input_image.height),
        children=[
            OcrElement(
                ocr_class=OcrClass.PARAGRAPH,
                children=accepted_lines,
            )
        ],
    )
    return page, "\n".join(accepted_text), len(regions), len(accepted_lines)


def filter_manga_vision_titles(page: OcrElement) -> int:
    """Retain only explicit or upper-page titles from full-page Vision OCR."""
    if page.bbox is None or page.bbox.height <= 0:
        return 0
    removed = 0
    for line in page.lines:
        words = _line_words(line)
        if not words or line.bbox is None:
            continue
        text = normalize_line_text([word.text for word in words])
        normalized = unicodedata.normalize("NFKC", text)
        relative_height = line.bbox.height / page.bbox.height
        japanese_count = len(
            re.findall(f"[{JAPANESE_CHARACTER}]", normalized)
        )
        title_pattern = bool(
            re.search(r"[『「【].+[』」】]|第\s*\d+\s*[章世代]", normalized)
        )
        upper_title = (
            line.bbox.top <= page.bbox.height * 0.20
            and relative_height >= 0.025
            and japanese_count >= 2
            and len(normalized) <= 100
        )
        if not (title_pattern or upper_title):
            line.children = []
            removed += 1
    return removed


def merge_manga_ocr_pages(
    pages: list[OcrElement],
    image_width: int,
    image_height: int,
) -> OcrElement:
    """Merge title and bubble OCR, remove duplicates, and apply manga order."""
    lines: list[OcrElement] = []
    normalized_texts: list[str] = []
    for page in pages:
        for line in page.lines:
            words = _line_words(line)
            if not words or line.bbox is None:
                continue
            text = normalize_line_text([word.text for word in words])
            comparable = re.sub(r"\W+", "", unicodedata.normalize("NFKC", text))
            if not comparable:
                continue
            if any(
                comparable == existing
                or (
                    len(comparable) >= 6
                    and (
                        comparable in existing
                        or existing in comparable
                    )
                )
                for existing in normalized_texts
            ):
                continue
            normalized_texts.append(comparable)
            lines.append(line)
    band_height = max(1.0, image_height * 0.08)
    lines.sort(
        key=lambda line: (
            int((line.bbox.top if line.bbox is not None else image_height) / band_height),
            -(line.bbox.left if line.bbox is not None else 0),
        )
    )
    return OcrElement(
        ocr_class=OcrClass.PAGE,
        bbox=BoundingBox(0, 0, image_width, image_height),
        children=[
            OcrElement(
                ocr_class=OcrClass.PARAGRAPH,
                children=lines,
            )
        ],
    )


def _write_page_artifacts(
    page_number: int,
    text: str,
    report: dict[str, object],
) -> None:
    artifact_value = os.environ.get(OCR_ARTIFACT_DIR_ENV, "")
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
        selected_engine = "tesseract"
        retried = False
        manga_regions_detected = 0
        manga_regions_accepted = 0
        non_narrative_lines = 0
        narrative_already_filtered = False

        adaptive = environment_flag(OCR_ADAPTIVE_ENV, DEFAULT_OCR_ADAPTIVE)
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

        if os.environ.get(OCR_CONTENT_TYPE_ENV, DEFAULT_OCR_CONTENT_TYPE) == "manga":
            with Image.open(input_file) as input_image:
                input_rgb = input_image.convert("RGB")
                vision_result = run_vision_ocr(
                    input_file,
                    input_image.width,
                    input_image.height,
                )
                manga_page, manga_text, manga_regions_detected, manga_regions_accepted = (
                    run_manga_region_ocr(
                        input_file,
                        input_rgb,
                        options,
                        page_number,
                    )
                )
                merge_pages: list[OcrElement] = []
                vision_text = ""
                vision_added = False
                narrative_scope = (
                    os.environ.get(
                        OCR_MANGA_TEXT_SCOPE_ENV,
                        DEFAULT_MANGA_TEXT_SCOPE,
                    )
                    == "narrative"
                )
                if vision_result is not None:
                    vision_page, vision_text = vision_result
                    if narrative_scope and manga_regions_accepted:
                        non_narrative_lines += filter_manga_vision_titles(
                            vision_page
                        )
                        narrative_already_filtered = True
                        if any(
                            _line_words(line) for line in vision_page.lines
                        ):
                            merge_pages.append(vision_page)
                            vision_added = True
                    elif narrative_scope:
                        non_narrative_lines += filter_manga_non_narrative_lines(
                            vision_page,
                            input_rgb,
                        )
                        narrative_already_filtered = True
                        merge_pages.append(vision_page)
                        vision_added = True
                    else:
                        merge_pages.append(vision_page)
                        vision_added = True
                if manga_regions_accepted:
                    merge_pages.append(manga_page)
                if merge_pages:
                    page = merge_manga_ocr_pages(
                        merge_pages,
                        input_image.width,
                        input_image.height,
                    )
                    raw_text = "\n".join(
                        text for text in (vision_text, manga_text) if text
                    )
                    selected_metrics = analyze_ocr_page(page)
                    if (
                        vision_added
                        and manga_regions_accepted
                    ):
                        selected_engine = "vision+tesseract_regions"
                    elif manga_regions_accepted:
                        selected_engine = "tesseract_regions"
                    else:
                        selected_engine = "vision"
                    narrative_already_filtered = True
                selected_mode = None

        if (
            os.environ.get(OCR_CONTENT_TYPE_ENV, DEFAULT_OCR_CONTENT_TYPE)
            == "manga"
            and os.environ.get(OCR_MANGA_TEXT_SCOPE_ENV, DEFAULT_MANGA_TEXT_SCOPE)
            == "narrative"
            and not narrative_already_filtered
        ):
            with Image.open(input_file) as input_image:
                non_narrative_lines = filter_manga_non_narrative_lines(
                    page,
                    input_image.convert("RGB"),
                )

        removed_lines = 0
        if environment_flag(
            OCR_FILTER_LOW_CONFIDENCE_ENV,
            DEFAULT_FILTER_LOW_CONFIDENCE,
        ):
            removed_lines = filter_low_confidence_lines(page)
        figure_lines = 0
        filtered_list_markers: list[dict[str, object]] = []
        with Image.open(input_file) as input_image:
            input_rgb = input_image.convert("RGB")
            if (
                selected_engine == "tesseract"
                and not environment_flag(
                    OCR_INCLUDE_FIGURES_ENV,
                    DEFAULT_INCLUDE_FIGURE_TEXT,
                )
            ):
                figure_lines = filter_figure_lines(
                    page,
                    input_rgb,
                )
            if not environment_flag(
                OCR_INCLUDE_LIST_MARKERS_ENV,
                DEFAULT_INCLUDE_LIST_MARKERS,
            ):
                filtered_list_markers = filter_list_marker_words(
                    page,
                    input_rgb,
                )
        reordered_elements = (
            reorder_ocr_tree_by_position(page)
            if selected_engine == "tesseract"
            else 0
        )
        corrections: dict[str, int] = {}
        correction_profiles = correction_profiles_from_environment()
        if environment_flag(
            OCR_CORRECTIONS_ENABLED_ENV,
            DEFAULT_OCR_CORRECTIONS_ENABLED,
        ):
            corrections = apply_ocr_corrections(
                page,
                profiles=correction_profiles,
            )
        retried_lines = 0
        if adaptive and selected_engine == "tesseract":
            with Image.open(input_file) as input_image:
                retried_lines = retry_review_candidate_lines(
                    page,
                    input_image,
                    options,
                    page_number,
                    profiles=correction_profiles,
                )
        if (
            retried_lines
            and environment_flag(
                OCR_CORRECTIONS_ENABLED_ENV,
                DEFAULT_OCR_CORRECTIONS_ENABLED,
            )
        ):
            retry_corrections = apply_ocr_corrections(
                page,
                profiles=correction_profiles,
            )
            corrections = dict(
                sorted((Counter(corrections) + Counter(retry_corrections)).items())
            )
        review_candidates = find_ocr_review_candidates(
            page,
            profiles=correction_profiles,
        )
        filtered_text = filtered_page_text(page)
        report: dict[str, object] = {
            "page": page_number + 1,
            "primary_pagesegmode": primary_mode,
            "selected_pagesegmode": selected_mode,
            "selected_engine": selected_engine,
            "retried": retried,
            "filtered_lines": removed_lines,
            "filtered_non_narrative_lines": non_narrative_lines,
            "manga_regions_detected": manga_regions_detected,
            "manga_regions_accepted": manga_regions_accepted,
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
