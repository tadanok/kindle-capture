"""OCR model validation and OCRmyPDF orchestration."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from pdfminer.high_level import extract_text as extract_pdf_text

from kindle_capture_support.book_corrections import (
    RAG_ACCURACY_BOOK_POST_OCR_REVIEW_RULES,
)
from kindle_capture_support.correction_rules import POST_OCR_REVIEW_RULES
from kindle_capture_support.file_utils import (
    create_sibling_temporary_path,
    ensure_distinct_paths,
)
from kindle_capture_support.ocr_config import (
    DEFAULT_BEST_TESSDATA_DIR,
    DEFAULT_OCR_CORRECTIONS_ENABLED,
    DEFAULT_FILTER_LOW_CONFIDENCE,
    DEFAULT_INCLUDE_FIGURE_TEXT,
    DEFAULT_INCLUDE_LIST_MARKERS,
    DEFAULT_MANGA_TEXT_SCOPE,
    DEFAULT_OCR_ADAPTIVE,
    DEFAULT_OCR_CONTENT_TYPE,
    DEFAULT_OCR_CORRECTION_PROFILES,
    DEFAULT_OCR_DICTIONARIES,
    DEFAULT_OCR_DICTIONARY_DIR,
    DEFAULT_OCR_LAYOUT,
    DEFAULT_OCR_MODEL,
    DEFAULT_OCR_OVERSAMPLE_DPI,
    OCR_ADAPTIVE_ENV,
    OCR_ARTIFACT_DIR_ENV,
    OCR_CONTENT_TYPE_ENV,
    OCR_CORRECTIONS_ENABLED_ENV,
    OCR_CORRECTION_PROFILES_ENV,
    OCR_FILTER_LOW_CONFIDENCE_ENV,
    OCR_INCLUDE_FIGURES_ENV,
    OCR_INCLUDE_LIST_MARKERS_ENV,
    OCR_MANGA_TEXT_SCOPE_ENV,
    OCR_TESSDATA_BEST_ENV,
    OCR_VISION_HELPER_ENV,
    SUPPORT_DIR,
    expand_correction_profiles,
)


ALL_POST_OCR_REVIEW_RULES = (
    POST_OCR_REVIEW_RULES + RAG_ACCURACY_BOOK_POST_OCR_REVIEW_RULES
)


def resolve_best_tessdata_dir(
    requested_languages: list[str],
    configured_path: str = "",
) -> Path:
    """Resolve and validate the project-local tessdata_best installation."""
    configured = configured_path or os.environ.get(
        OCR_TESSDATA_BEST_ENV,
        "",
    )
    directory = (
        Path(configured).expanduser()
        if configured
        else DEFAULT_BEST_TESSDATA_DIR
    ).resolve()
    missing = [
        language
        for language in requested_languages
        if not (directory / f"{language}.traineddata").is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "高精度OCRモデルがありません: "
            f"{', '.join(missing)}\n"
            "  セットアップ: python -m kindle_capture_support.install_ocr_models"
        )
    missing_configs = [
        name
        for name in ("hocr", "txt")
        if not (directory / "configs" / name).is_file()
    ]
    if missing_configs:
        raise FileNotFoundError(
            "高精度OCRモデルの設定がありません: "
            f"{', '.join(missing_configs)}\n"
            "  セットアップ: python -m kindle_capture_support.install_ocr_models"
        )
    return directory


def load_ocr_user_words(
    dictionary_names: list[str] | None = None,
    custom_paths: list[str] | None = None,
    dictionary_dir: Path = DEFAULT_OCR_DICTIONARY_DIR,
) -> list[str]:
    """Load and deduplicate per-run Tesseract user words."""
    names = (
        list(DEFAULT_OCR_DICTIONARIES)
        if dictionary_names is None
        else dictionary_names
    )
    paths: list[Path] = []
    for name in names:
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError(f"OCR 辞書名が不正です: {name}")
        paths.append(dictionary_dir / f"{name}.txt")
    paths.extend(Path(value).expanduser() for value in (custom_paths or []))

    words: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"OCR 専門用語辞書が見つかりません: {path}")
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            word = raw_line.split("#", 1)[0].strip()
            if not word:
                continue
            if any(character.isspace() for character in word):
                raise ValueError(
                    "Tesseract の専門用語は1行に1語で指定してください: "
                    f"{path}: {word}"
                )
            if word not in seen:
                seen.add(word)
                words.append(word)
    return words


def find_post_ocr_candidates(
    pages: list[str],
    profiles: list[str] | set[str] | frozenset[str] | None = None,
) -> list[dict[str, object]]:
    """Find known suspicious strings in the completed PDF text layer."""
    candidates: list[dict[str, object]] = []
    active_profiles = expand_correction_profiles(profiles)
    for page_number, page in enumerate(pages, start=1):
        for rule in ALL_POST_OCR_REVIEW_RULES:
            if rule.profile not in active_profiles:
                continue
            for match in rule.pattern.finditer(page):
                context_start = max(0, match.start() - 60)
                context_end = min(len(page), match.end() + 60)
                candidates.append(
                    {
                        "page": page_number,
                        "text": match.group(),
                        "reason": rule.reason,
                        "context": re.sub(
                            r"\s+",
                            " ",
                            page[context_start:context_end],
                        ).strip(),
                    }
                )
    return candidates


def build_vision_ocr_helper(output_path: Path) -> None:
    """Build the local macOS Vision OCR helper used only by manga mode."""
    if sys.platform != "darwin":
        raise RuntimeError("漫画OCRモードは現在macOSでのみ利用できます。")
    compiler = shutil.which("clang")
    if compiler is None:
        raise RuntimeError(
            "漫画OCRモードにはXcode Command Line Toolsのclangが必要です。"
        )
    source_path = SUPPORT_DIR / "native" / "vision_ocr.m"
    if not source_path.exists():
        raise RuntimeError(f"Vision OCRソースが見つかりません: {source_path}")
    result = subprocess.run(
        [
            compiler,
            "-fobjc-arc",
            "-framework",
            "Foundation",
            "-framework",
            "Vision",
            "-framework",
            "ImageIO",
            "-framework",
            "CoreGraphics",
            str(source_path),
            "-o",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists():
        detail = result.stderr.strip() or "原因不明"
        raise RuntimeError(f"Vision OCRヘルパーをビルドできませんでした: {detail}")


def make_pdf_searchable(
    input_pdf: Path,
    output_pdf: Path,
    raw_text_path: Path,
    ocr_lang: str,
    ocr_layout: str = DEFAULT_OCR_LAYOUT,
    oversample_dpi: int = DEFAULT_OCR_OVERSAMPLE_DPI,
    readaloud_text_layer: bool = True,
    ocr_model: str = DEFAULT_OCR_MODEL,
    tessdata_best_dir: str = "",
    adaptive_ocr: bool = DEFAULT_OCR_ADAPTIVE,
    filter_low_confidence: bool = DEFAULT_FILTER_LOW_CONFIDENCE,
    ocr_corrections_enabled: bool = DEFAULT_OCR_CORRECTIONS_ENABLED,
    include_figure_text: bool = DEFAULT_INCLUDE_FIGURE_TEXT,
    include_list_markers: bool = DEFAULT_INCLUDE_LIST_MARKERS,
    correction_profiles: list[str] | None = None,
    ocr_dictionaries: list[str] | None = None,
    ocr_user_word_paths: list[str] | None = None,
    filtered_text_path: Path | None = None,
    quality_report_path: Path | None = None,
    ocr_content_type: str = DEFAULT_OCR_CONTENT_TYPE,
    manga_text_scope: str = DEFAULT_MANGA_TEXT_SCOPE,
) -> bool:
    """OCRmyPDF で検索可能 PDF を作成する。成功時は True、失敗時は False を返す。"""
    effective_correction_profiles = sorted(
        expand_correction_profiles(correction_profiles)
    )
    try:
        ensure_distinct_paths(
            {
                "入力PDF": input_pdf,
                "検索可能PDF": output_pdf,
                "OCR生テキスト": raw_text_path,
                "整形済みOCRテキスト": filtered_text_path,
                "OCR品質レポート": quality_report_path,
            }
        )
    except ValueError as error:
        print(f"  エラー: {error}")
        return False

    if shutil.which("ocrmypdf") is None:
        print(
            "エラー: ocrmypdf コマンドが見つかりません。\n"
                "インストール: python -m pip install -r requirements.txt"
        )
        return False

    if not input_pdf.exists() or input_pdf.stat().st_size == 0:
        print(f"  エラー: 入力 PDF が見つからないか空です: {input_pdf.resolve()}")
        return False

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    raw_text_path.parent.mkdir(parents=True, exist_ok=True)
    if quality_report_path is not None:
        quality_report_path.parent.mkdir(parents=True, exist_ok=True)

    requested_langs = [lang for lang in ocr_lang.split("+") if lang]
    required_model_langs = list(
        dict.fromkeys(
            requested_langs
            + (["jpn_vert"] if ocr_content_type == "manga" else [])
        )
    )
    ocr_environment = os.environ.copy()
    if ocr_model == "best":
        try:
            model_dir = resolve_best_tessdata_dir(
                required_model_langs,
                configured_path=tessdata_best_dir,
            )
        except FileNotFoundError as error:
            print(f"  エラー: {error}")
            return False
        ocr_environment["TESSDATA_PREFIX"] = str(model_dir)

    # 選択した Tesseract モデル内の言語データを確認する。
    try:
        lang_result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=ocr_environment,
        )
        installed_langs = {
            line.strip() for line in lang_result.stdout.splitlines() if line.strip()
        }
        missing_langs = [
            lang for lang in required_model_langs if lang not in installed_langs
        ]
        if missing_langs:
            print(
                "  エラー: Tesseract に以下の言語データがありません: "
                f"{', '.join(missing_langs)}\n"
                + (
                    "  セットアップ: "
                    "python -m kindle_capture_support.install_ocr_models"
                    if ocr_model == "best"
                    else "  インストール例: brew install tesseract-lang"
                )
            )
            return False
    except Exception:
        pass

    cmd = [
        "ocrmypdf",
        "-l",
        ocr_lang,
        "--tesseract-timeout=300",
        "--oversample",
        str(oversample_dpi),
        "--output-type",
        "pdf",
        "--sidecar",
        str(raw_text_path),
    ]
    if ocr_model == "best":
        cmd.extend(["--tesseract-oem", "1"])
    if readaloud_text_layer:
        plugin_path = SUPPORT_DIR / "ocr_plugin.py"
        if not plugin_path.exists():
            print(f"  エラー: OCR プラグインが見つかりません: {plugin_path}")
            return False
        cmd.extend(["--plugin", str(plugin_path)])
        project_root = str(SUPPORT_DIR.parent)
        existing_python_path = ocr_environment.get("PYTHONPATH", "")
        ocr_environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (project_root, existing_python_path) if value
        )
    page_segmentation_modes = {"horizontal": "6", "vertical": "5"}
    if ocr_layout in page_segmentation_modes:
        cmd.extend(
            ["--tesseract-pagesegmode", page_segmentation_modes[ocr_layout]]
        )
    started_at = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="kindle-ocr-artifacts-") as artifact_value:
        artifact_dir = Path(artifact_value)
        staged_output_pdf = create_sibling_temporary_path(output_pdf)
        staged_raw_text = create_sibling_temporary_path(raw_text_path)
        staged_quality_report = (
            create_sibling_temporary_path(quality_report_path)
            if quality_report_path is not None
            else None
        )

        def cleanup_staged_outputs() -> None:
            for staged_path in (
                staged_output_pdf,
                staged_raw_text,
                staged_quality_report,
            ):
                if staged_path is not None:
                    staged_path.unlink(missing_ok=True)

        try:
            user_words = load_ocr_user_words(
                ocr_dictionaries,
                ocr_user_word_paths,
            )
        except (FileNotFoundError, OSError, UnicodeError, ValueError) as error:
            print(f"  エラー: {error}")
            cleanup_staged_outputs()
            return False

        if ocr_content_type == "manga":
            if not readaloud_text_layer:
                print(
                    "  エラー: 漫画OCRモードには "
                    "--pdf-text-layer readaloud が必要です。"
                )
                cleanup_staged_outputs()
                return False
            vision_helper = artifact_dir / "kindle-vision-ocr"
            try:
                build_vision_ocr_helper(vision_helper)
            except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                print(f"  エラー: {error}")
                cleanup_staged_outputs()
                return False
            ocr_environment[OCR_VISION_HELPER_ENV] = str(vision_helper)

        run_cmd = [
            str(staged_raw_text) if value == str(raw_text_path) else value
            for value in cmd
        ]
        if user_words:
            user_words_path = artifact_dir / "tesseract-user-words.txt"
            user_words_path.write_text(
                "\n".join(user_words) + "\n",
                encoding="utf-8",
            )
            run_cmd.extend(["--user-words", str(user_words_path)])
        run_cmd.extend([str(input_pdf), str(staged_output_pdf)])

        if readaloud_text_layer:
            ocr_environment[OCR_CONTENT_TYPE_ENV] = ocr_content_type
            ocr_environment[OCR_MANGA_TEXT_SCOPE_ENV] = manga_text_scope
            ocr_environment[OCR_ARTIFACT_DIR_ENV] = str(artifact_dir)
            ocr_environment[OCR_ADAPTIVE_ENV] = "1" if adaptive_ocr else "0"
            ocr_environment[OCR_FILTER_LOW_CONFIDENCE_ENV] = (
                "1" if filter_low_confidence else "0"
            )
            ocr_environment[OCR_CORRECTIONS_ENABLED_ENV] = (
                "1" if ocr_corrections_enabled else "0"
            )
            ocr_environment[OCR_CORRECTION_PROFILES_ENV] = ",".join(
                effective_correction_profiles
            )
            ocr_environment[OCR_INCLUDE_FIGURES_ENV] = (
                "1" if include_figure_text else "0"
            )
            ocr_environment[OCR_INCLUDE_LIST_MARKERS_ENV] = (
                "1" if include_list_markers else "0"
            )

        try:
            result = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                timeout=1800,
                env=ocr_environment,
            )
        except subprocess.TimeoutExpired:
            print("  エラー: OCR 処理がタイムアウトしました (1800 秒以上)。")
            cleanup_staged_outputs()
            return False
        except FileNotFoundError:
            print("  エラー: ocrmypdf が実行できませんでした。")
            cleanup_staged_outputs()
            return False

        if result.returncode != 0 or not staged_output_pdf.exists():
            print(f"  エラー: OCR 処理に失敗しました (終了コード {result.returncode})")
            if result.stderr:
                print(result.stderr)
            cleanup_staged_outputs()
            return False

        elapsed_seconds = time.monotonic() - started_at
        filtered_files = sorted(artifact_dir.glob("*.filtered.txt"))
        filtered_page_texts = [
            path.read_text(encoding="utf-8") for path in filtered_files
        ]
        if filtered_text_path is not None and filtered_files:
            filtered_text_path.parent.mkdir(parents=True, exist_ok=True)
            filtered_text_path.write_text(
                "\f".join(filtered_page_texts),
                encoding="utf-8",
            )
        try:
            completed_pdf_pages = extract_pdf_text(str(staged_output_pdf)).split("\f")
        except (OSError, UnicodeError, ValueError):
            completed_pdf_pages = filtered_page_texts
        post_validation_candidates = find_post_ocr_candidates(
            completed_pdf_pages,
            profiles=effective_correction_profiles,
        )

        quality_files = sorted(artifact_dir.glob("*.quality.json"))
        quality_pages = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in quality_files
        ]
        if staged_quality_report is not None:
            correction_totals: dict[str, int] = {}
            review_candidates: list[dict[str, object]] = []
            filtered_list_markers: list[dict[str, object]] = []
            for page in quality_pages:
                for name, count in page.get("corrections", {}).items():
                    correction_totals[name] = (
                        correction_totals.get(name, 0) + int(count)
                    )
                for candidate in page.get("review_candidates", []):
                    review_candidates.append(
                        {
                            "page": page.get("page"),
                            **candidate,
                        }
                    )
                for marker in page.get("filtered_list_markers", []):
                    filtered_list_markers.append(
                        {
                            "page": page.get("page"),
                            **marker,
                        }
                    )
            report = {
                "ocr_model": ocr_model,
                "ocr_languages": requested_langs,
                "ocr_layout": ocr_layout,
                "ocr_content_type": ocr_content_type,
                "manga_text_scope": manga_text_scope,
                "ocr_dictionaries": (
                    ocr_dictionaries
                    if ocr_dictionaries is not None
                    else list(DEFAULT_OCR_DICTIONARIES)
                ),
                "ocr_user_word_count": len(user_words),
                "adaptive_ocr": adaptive_ocr and readaloud_text_layer,
                "filter_low_confidence": (
                    filter_low_confidence and readaloud_text_layer
                ),
                "ocr_corrections_enabled": (
                    ocr_corrections_enabled and readaloud_text_layer
                ),
                "requested_correction_profiles": (
                    DEFAULT_OCR_CORRECTION_PROFILES
                    if correction_profiles is None
                    else correction_profiles
                ),
                "correction_profiles": effective_correction_profiles,
                "include_figure_text": (
                    include_figure_text or not readaloud_text_layer
                ),
                "include_list_markers": (
                    include_list_markers or not readaloud_text_layer
                ),
                "elapsed_seconds": round(elapsed_seconds, 2),
                "retried_pages": sum(
                    bool(page.get("retried")) for page in quality_pages
                ),
                "filtered_lines": sum(
                    int(page.get("filtered_lines", 0)) for page in quality_pages
                ),
                "filtered_non_narrative_lines": sum(
                    int(page.get("filtered_non_narrative_lines", 0))
                    for page in quality_pages
                ),
                "manga_regions_detected": sum(
                    int(page.get("manga_regions_detected", 0))
                    for page in quality_pages
                ),
                "manga_regions_accepted": sum(
                    int(page.get("manga_regions_accepted", 0))
                    for page in quality_pages
                ),
                "filtered_figure_lines": sum(
                    int(page.get("filtered_figure_lines", 0))
                    for page in quality_pages
                ),
                "filtered_list_marker_count": len(
                    filtered_list_markers
                ),
                "filtered_list_markers": filtered_list_markers,
                "reordered_elements": sum(
                    int(page.get("reordered_elements", 0))
                    for page in quality_pages
                ),
                "selected_engines": dict(
                    sorted(
                        Counter(
                            str(page.get("selected_engine", "tesseract"))
                            for page in quality_pages
                        ).items()
                    )
                ),
                "retried_lines": sum(
                    int(page.get("retried_lines", 0))
                    for page in quality_pages
                ),
                "correction_count": sum(correction_totals.values()),
                "corrections": dict(sorted(correction_totals.items())),
                "review_candidate_count": len(review_candidates),
                "automated_review_candidate_count": len(
                    review_candidates
                ),
                "review_candidates": review_candidates,
                "post_validation_candidate_count": len(
                    post_validation_candidates
                ),
                "post_validation_candidates": post_validation_candidates,
                "quality_verification": {
                    "method": "heuristic",
                    "ground_truth_compared": False,
                    "verified_error_free": False,
                    "automated_checks_passed": not (
                        review_candidates or post_validation_candidates
                    ),
                    "note": (
                        "候補0件は原画像との完全一致を保証しません。"
                        "代表ページは目視または正解データで確認してください。"
                    ),
                },
                "pages": quality_pages,
            }
            staged_quality_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        if not staged_raw_text.exists():
            print("  エラー: OCR 生テキストが生成されませんでした。")
            cleanup_staged_outputs()
            return False
        os.replace(staged_output_pdf, output_pdf)
        os.replace(staged_raw_text, raw_text_path)
        if staged_quality_report is not None and quality_report_path is not None:
            os.replace(staged_quality_report, quality_report_path)
        print(
            f"  ✓ OCR 処理が完了しました "
            f"({output_pdf.stat().st_size} bytes / {elapsed_seconds:.1f} 秒)"
        )
        return True
