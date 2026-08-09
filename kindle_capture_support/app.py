#!/usr/bin/env python3
"""
kindle_capture.py — Kindle for Mac を自動スクリーンショットして PDF 化する

使い方:
    python kindle_capture.py                    # デフォルト設定
    python kindle_capture.py -o 本のタイトル.pdf  # 出力ファイル名を指定
    python kindle_capture.py -d 2.0             # ページめくり待機時間を 2 秒に
    python kindle_capture.py --max-pages 50     # 最大 50 ページで停止
    python kindle_capture.py --keep-images      # 中間 PNG を残す
    python kindle_capture.py --no-searchable    # OCR とテキスト出力を省略
    python kindle_capture.py --ocr-layout vertical --ocr-lang jpn_vert
    python kindle_capture.py --page-turn-method key  # 下矢印キーでページ送り

事前に必要な権限（システム設定 > プライバシーとセキュリティ）:
    - アクセシビリティ: ターミナル（またはVS Code等）
    - 画面収録:        ターミナル（またはVS Code等）

緊急停止:
    - マウスを画面の左上隅に素早く動かす（pyautogui フェイルセーフ）
    - または Ctrl+C
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path
from statistics import median
from typing import cast

from kindle_capture_support.file_utils import (
    create_sibling_temporary_path,
    ensure_distinct_paths,
)
from kindle_capture_support.ocr_config import (
    DEFAULT_OCR_CORRECTIONS_ENABLED,
    DEFAULT_FILTER_LOW_CONFIDENCE,
    DEFAULT_INCLUDE_FIGURE_TEXT,
    DEFAULT_INCLUDE_LIST_MARKERS,
    DEFAULT_MANGA_TEXT_SCOPE,
    DEFAULT_OCR_ADAPTIVE,
    DEFAULT_OCR_CONTENT_TYPE,
    DEFAULT_OCR_DICTIONARIES,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_OCR_LAYOUT,
    DEFAULT_OCR_MODEL,
    DEFAULT_OCR_OVERSAMPLE_DPI,
    DEFAULT_PDF_TEXT_LAYER,
    JAPANESE_OR_PUNCTUATION,
    PAGE_NUMBER_RE,
)
from kindle_capture_support.ocr_runner import (
    load_ocr_user_words,
    make_pdf_searchable,
    resolve_best_tessdata_dir,
)

DEFAULT_OUTPUT_NAME = "output/kindle_book.pdf"
AUTO_TITLE_PAGE_LIMIT = 5

# --- 依存ライブラリのチェック ---
try:
    from PIL import Image, ImageChops
except ImportError:
    print("エラー: Pillow がインストールされていません。\n  pip install Pillow")
    sys.exit(1)

try:
    import img2pdf
except ImportError:
    print("エラー: img2pdf がインストールされていません。\n  pip install img2pdf")
    sys.exit(1)

try:
    import pyautogui
except ImportError:
    print("エラー: pyautogui がインストールされていません。\n  pip install pyautogui")
    sys.exit(1)

try:
    import Quartz
except ImportError:
    print(
        "エラー: pyobjc-framework-Quartz がインストールされていません。\n"
        "  pip install pyobjc-framework-Quartz"
    )
    sys.exit(1)

# フェイルセーフ: マウスを左上隅に動かすと即停止
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


# ──────────────────────────────────────────
# ウィンドウ検出
# ──────────────────────────────────────────

def get_kindle_window_bounds() -> tuple[int, int, int, int] | None:
    """
    Kindle for Mac のウィンドウ座標（論理ピクセル）を返す。
    複数ウィンドウがある場合は最大のものを選択。見つからなければ None。
    """
    options = (
        Quartz.kCGWindowListOptionOnScreenOnly  # pyright: ignore[reportAttributeAccessIssue]
        | Quartz.kCGWindowListExcludeDesktopElements  # pyright: ignore[reportAttributeAccessIssue]
    )
    window_list = Quartz.CGWindowListCopyWindowInfo(  # pyright: ignore[reportAttributeAccessIssue]
        options, Quartz.kCGNullWindowID  # pyright: ignore[reportAttributeAccessIssue]
    )

    best = None
    best_area = 0

    for window in window_list:
        owner = window.get("kCGWindowOwnerName", "")
        if "Kindle" not in owner:
            continue

        bounds = window.get("kCGWindowBounds", {})
        x = int(bounds.get("X", 0))
        y = int(bounds.get("Y", 0))
        w = int(bounds.get("Width", 0))
        h = int(bounds.get("Height", 0))

        if w < 200 or h < 200:
            continue  # ツールバーなど小さいウィンドウを除外

        area = w * h
        if area > best_area:
            best_area = area
            best = (x, y, w, h)

    return best


# ──────────────────────────────────────────
# スクリーンショット
# ──────────────────────────────────────────

def take_screenshot(bounds: tuple[int, int, int, int]) -> Image.Image:
    """指定領域のスクリーンショットを RGB PIL Image として返す。"""
    x, y, w, h = bounds
    img = pyautogui.screenshot(region=(x, y, w, h))
    return img.convert("RGB")


def detect_dark_left_ui_boundary(
    img: Image.Image,
    top: int = 0,
    max_fraction: float = 0.25,
) -> int:
    """左端に連続する暗い Kindle UI 帯の右端を返す。見つからなければ 0。

    本文や暗い表紙を誤って切り取らないよう、画面幅の25%以内にある暗い帯と、
    その直後の明確な非暗色領域の両方が確認できた場合だけ検出する。
    """
    img = img.convert("RGB")
    w, h = img.size
    if w < 40 or h < 40:
        return 0

    top = max(0, min(top, h - 1))
    content_height = h - top
    y_start = top + int(content_height * 0.08)
    y_end = top + int(content_height * 0.92)
    y_step = max(1, (y_end - y_start) // 240)
    sample_ys = range(y_start, max(y_start + 1, y_end), y_step)
    pixels = img.load()
    assert pixels is not None

    def is_dark_column(x: int) -> bool:
        samples = [pixels[x, y] for y in sample_ys]
        dark_count = sum(max(pixel) <= 45 for pixel in samples)
        # 戻る矢印など帯内の小さな白いアイコンは暗い帯の一部として許容する。
        return dark_count / len(samples) >= 0.75

    x_step = max(1, w // 800)
    limit = max(x_step, int(w * max_fraction))
    dark_columns = [
        (x, is_dark_column(x))
        for x in range(0, min(limit + x_step, w), x_step)
    ]
    if not dark_columns or not dark_columns[0][1]:
        return 0

    minimum_width = max(8, int(w * 0.02))
    confirmation_width = max(8, int(w * 0.015))
    confirmation_count = max(2, confirmation_width // x_step)

    for index, (x, dark) in enumerate(dark_columns):
        if dark or x < minimum_width:
            continue
        following = dark_columns[index : index + confirmation_count]
        if (
            len(following) == confirmation_count
            and sum(flag for _, flag in following) <= confirmation_count // 5
        ):
            return x

    return 0


def detect_dark_right_ui_boundary(
    img: Image.Image,
    top: int = 0,
    max_fraction: float = 0.25,
) -> int:
    """右端の暗い Kindle UI 帯の左端を返す。見つからなければ画像幅を返す。"""
    img = img.convert("RGB")
    w, h = img.size
    if w < 40 or h < 40:
        return w

    top = max(0, min(top, h - 1))
    content_height = h - top
    y_start = top + int(content_height * 0.08)
    y_end = top + int(content_height * 0.92)
    y_step = max(1, (y_end - y_start) // 240)
    sample_ys = range(y_start, max(y_start + 1, y_end), y_step)
    pixels = img.load()
    assert pixels is not None

    def is_dark_column(x: int) -> bool:
        samples = [pixels[x, y] for y in sample_ys]
        dark_count = sum(max(pixel) <= 45 for pixel in samples)
        return dark_count / len(samples) >= 0.75

    x_step = max(1, w // 800)
    limit = max(x_step, int(w * max_fraction))
    dark_columns = [
        (x, is_dark_column(x))
        for x in range(w - 1, max(-1, w - limit - x_step), -x_step)
    ]
    if not dark_columns or not dark_columns[0][1]:
        return w

    minimum_width = max(8, int(w * 0.02))
    confirmation_width = max(8, int(w * 0.015))
    confirmation_count = max(2, confirmation_width // x_step)

    for index, (x, dark) in enumerate(dark_columns):
        if dark or (w - 1 - x) < minimum_width:
            continue
        following = dark_columns[index : index + confirmation_count]
        if (
            len(following) == confirmation_count
            and sum(flag for _, flag in following) <= confirmation_count // 5
        ):
            return x + 1

    return w


def detect_content_bounds(
    img: Image.Image,
    tolerance: int = 15,
    header_height: int | None = None,
    crop_left: int = 50,
    crop_right: int = 50,
    crop_bottom: int = 0,
    auto_crop_ui: bool = True,
) -> tuple[int, int, int, int]:
    """
    表紙画像から本のページ領域の境界を検出する。

    - 上端: タイトルバーと本文の境界を画像から検出する
    - 左右端: 指定幅に加え、暗い Kindle UI 帯を自動検出して除外する
    - 下端: 下から全行走査して最初の非背景行を探し、指定された幅を追加で切り取る

    Returns: (left, top, right, bottom) PIL crop box
    """
    w, h = img.size
    pixels = img.load()
    assert pixels is not None

    def max_diff(p1, p2) -> int:
        return max(abs(int(p1[c]) - int(p2[c])) for c in range(3))

    step = max(1, min(w, h) // 80)

    # macOS のタイトルバーは端末・表示倍率・Kindle の表示状態で高さが変わる。
    # 手動指定がなければ、上部の横方向の境界から本文の開始位置を検出する。
    top = header_height if header_height is not None else detect_header_bottom(img)
    top = max(0, min(top, h - 1))

    # 背景色: コンテンツ上端の左端からサンプリング
    bg = pixels[0, top]

    def is_bg(pixel) -> bool:
        return max_diff(pixel, bg) <= tolerance

    detected_left = (
        detect_dark_left_ui_boundary(img, top=top) if auto_crop_ui else 0
    )
    detected_right = (
        detect_dark_right_ui_boundary(img, top=top) if auto_crop_ui else w
    )
    left = max(0, min(max(crop_left, detected_left), w - 1))
    right = max(left + 1, min(w - crop_right, detected_right, w))

    # 指定された下余白を先に除き、その範囲内で最後の非背景行を探す。
    bottom_limit = max(top + 1, h - crop_bottom)
    bottom = bottom_limit
    for y in range(bottom_limit - 1, max(top, h // 2), -1):
        xs = range(left, right, step)
        if any(not is_bg(pixels[x, y]) for x in xs):
            bottom = y + 1
            break

    return (left, top, right, bottom)


def detect_header_bottom(img: Image.Image) -> int:
    """タイトルバーの下端を検出し、本文を開始する y 座標を返す。

    Kindle のウィンドウ画像では、タイトルバーと本文の境界が横方向に大きく
    変化する。画像の中央80%をサンプリングして各行の変化量を比較し、上部
    80px にある最初の明確な境界を探す。全画面表示などで境界が見つからない
    場合は 0 を返し、画像を上端から使う。
    """
    img = img.convert("RGB")
    w, h = img.size
    if w < 2 or h < 2:
        return 0

    # タイトルバーは通常 20〜80px 程度。本文の装飾を誤検出しにくいように
    # 検索範囲を上部に限定する。
    first_y = min(12, h - 1)
    last_y = min(80, h - 1)
    if last_y <= first_y:
        return 0

    left = int(w * 0.1)
    right = max(left + 1, int(w * 0.9))
    step = max(1, (right - left) // 160)
    pixels = img.load()
    assert pixels is not None

    def get_rgb(pixel: object) -> tuple[int, int, int]:
        return cast(tuple[int, int, int], pixel)

    row_changes: list[tuple[int, float]] = []
    for y in range(1, last_y + 1):
        diffs = [
            max(
                abs(
                    int(get_rgb(pixels[x, y])[channel])
                    - int(get_rgb(pixels[x, y - 1])[channel])
                )
                for channel in range(3)
            )
            for x in range(left, right, step)
        ]
        row_changes.append((y, sum(diffs) / len(diffs)))

    # 本文内の小さな画像変化ではなく、タイトルバー境界のように広い範囲で
    # 起きる変化だけを対象にする。最初の候補を採用して表紙の装飾を避ける。
    baseline = median(change for _, change in row_changes)
    threshold = max(12.0, baseline * 3)
    for y, change in row_changes:
        if y >= first_y and change >= threshold:
            return y

    return 0


# ──────────────────────────────────────────
# 画像比較（最終ページ判定）
# ──────────────────────────────────────────

def images_are_same(
    img1: Image.Image,
    img2: Image.Image,
    tolerance: int = 8,
    center_fraction: float = 0.75,
) -> bool:
    """
    2枚の画像が実質的に同じかどうかを判定する。

    - tolerance: 許容ピクセル差（RGB 各チャンネルの最大値）
    - center_fraction: 比較する中央領域の割合
      （上部メニューバーやプログレスバーなど UI 要素を除外するため）
    """
    if img1.size != img2.size:
        return False

    w, h = img1.size
    margin_x = int(w * (1 - center_fraction) / 2)
    margin_y = int(h * (1 - center_fraction) / 2)
    crop_box = (margin_x, margin_y, w - margin_x, h - margin_y)

    crop1 = img1.crop(crop_box)
    crop2 = img2.crop(crop_box)

    diff = ImageChops.difference(crop1, crop2)
    # RGB 画像同士の差分なので getextrema() は必ずバンドごとの (min, max) タプルを返す
    extrema = cast(tuple[tuple[int, int], ...], diff.getextrema())
    max_diff = max(ch[1] for ch in extrema)
    return max_diff <= tolerance


# ──────────────────────────────────────────
# ページめくり
# ──────────────────────────────────────────

def activate_kindle_window() -> bool:
    """AppleScript で Kindle ウィンドウをフォーカスする。クリックは行わない。"""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Amazon Kindle" to activate'],
            capture_output=True,
            timeout=3,
        )
        time.sleep(0.25)
        return True
    except Exception:
        return False


def go_to_next_page(
    bounds: tuple[int, int, int, int],
    method: str,
) -> bool:
    """Kindle のページを1枚進める。"""
    x, y, w, h = bounds
    try:
        if method == "key":
            # クリックせずに AppleScript でフォーカスしてからキー送信
            activate_kindle_window()
            pyautogui.press("down")
        else:
            pyautogui.click(x + int(w * 0.75), y + h // 2)
        return True
    except pyautogui.FailSafeException:
        raise
    except Exception as e:
        print(f"\nページ送りに失敗しました: {e}")
        return False


def try_turn_page_and_wait(
    prev_img: Image.Image,
    bounds: tuple[int, int, int, int],
    primary_method: str,
    retries: int,
    timeout: float,
    content_box: tuple[int, int, int, int] | None = None,
) -> Image.Image | None:
    """ページ送りを送信し、画面変化を待つ。変化なければ再送信。"""
    total_attempts = max(1, retries + 1)
    for attempt in range(total_attempts):
        current_bounds = get_kindle_window_bounds() or bounds
        if attempt > 0:
            print(f"\nページ送り再試行 {attempt}/{total_attempts - 1}")
        go_to_next_page(current_bounds, method=primary_method)
        new_img = wait_for_page_change(
            prev_img,
            timeout=max(1.5, timeout),
            content_box=content_box,
        )
        if new_img is not None:
            return new_img
    return None


def wait_for_page_change(
    prev_img: Image.Image,
    timeout: float,
    poll_interval: float = 0.3,
    stable_count: int = 2,
    content_box: tuple[int, int, int, int] | None = None,
) -> Image.Image | None:
    """画面が変化し安定するまでポーリングし、安定した画像を返す。

    アニメーション途中で返さないよう、変化検出後も連続して同じフレームが
    stable_count 回続くまで待機する。
    """
    deadline = time.monotonic() + timeout
    last_img: Image.Image | None = None
    stable = 0

    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        bounds = get_kindle_window_bounds()
        if not bounds:
            return None
        img = take_screenshot(bounds)
        if content_box is not None:
            img = img.crop(content_box)

        if last_img is None:
            # 変化を待つ段階
            if not images_are_same(img, prev_img):
                last_img = img
                stable = 1
        else:
            # 安定を待つ段階
            if images_are_same(img, last_img):
                stable += 1
                if stable >= stable_count:
                    return last_img
            else:
                last_img = img
                stable = 1

    return last_img  # タイムアウト時も変化があれば返す


# ──────────────────────────────────────────
# PDF 作成
# ──────────────────────────────────────────

def save_images_as_pdf(
    image_paths: list[Path],
    output_path: Path,
    dpi: int = 300,
) -> None:
    """PNG を再圧縮せず、指定 DPI の PDF にまとめる。"""
    if not image_paths:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    pdf_bytes = img2pdf.convert(
        [str(path) for path in image_paths],
        layout_fun=layout,
    )
    temporary_path = create_sibling_temporary_path(output_path)
    try:
        temporary_path.write_bytes(pdf_bytes)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)






def parse_page_ranges(value: str) -> set[int]:
    """Parse a 1-based page list such as ``1-3,7,10-12``."""
    pages: set[int] = set()
    if not value.strip():
        return pages

    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("空のページ指定があります")
        if "-" in part:
            start_value, end_value = part.split("-", 1)
            if not start_value.isdigit() or not end_value.isdigit():
                raise ValueError(f"ページ範囲が不正です: {part}")
            start = int(start_value)
            end = int(end_value)
            if start < 1 or end < start:
                raise ValueError(f"ページ範囲が不正です: {part}")
            pages.update(range(start, end + 1))
        else:
            if not part.isdigit() or int(part) < 1:
                raise ValueError(f"ページ番号が不正です: {part}")
            pages.add(int(part))
    return pages








def _remove_repeated_page_edges(pages: list[str]) -> list[str]:
    """複数ページで繰り返される先頭・末尾行をヘッダー／フッターとして除く。"""
    if len(pages) < 2:
        return pages

    edge_counts: Counter[str] = Counter()
    page_lines: list[list[str]] = []
    for page in pages:
        lines = [line.strip() for line in page.splitlines()]
        page_lines.append(lines)
        candidates = {
            line
            for line in lines[:2] + lines[-2:]
            if 4 <= len(line) <= 80 and not PAGE_NUMBER_RE.fullmatch(line)
        }
        edge_counts.update(candidates)

    repeated = {line for line, count in edge_counts.items() if count >= 2}
    cleaned_pages: list[str] = []
    for lines in page_lines:
        last_edge_index = max(0, len(lines) - 2)
        cleaned_pages.append(
            "\n".join(
                line
                for index, line in enumerate(lines)
                if line not in repeated
                or (index >= 2 and index < last_edge_index)
            )
        )
    return cleaned_pages


def _join_wrapped_lines(lines: list[str]) -> str:
    """OCR の表示上の折り返しを、読み上げに適した段落へまとめる。"""
    paragraphs: list[str] = []
    current = ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue
        if PAGE_NUMBER_RE.fullmatch(line):
            continue

        if not current:
            current = line
            continue

        if current.endswith("-") and re.match(r"^[A-Za-z]", line):
            current = current[:-1] + line
        elif re.search(f"[{JAPANESE_OR_PUNCTUATION}]$", current) and re.match(
            f"^[{JAPANESE_OR_PUNCTUATION}]", line
        ):
            current += line
        else:
            current += " " + line

    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs)


def _remove_spurious_japanese_blank_lines(lines: list[str]) -> list[str]:
    """日本語本文の各行間に OCR が挿入した空行を除く。"""
    cleaned: list[str] = []
    for index, line in enumerate(lines):
        if line.strip():
            cleaned.append(line)
            continue

        previous = next(
            (candidate.strip() for candidate in reversed(cleaned) if candidate.strip()),
            "",
        )
        following = next(
            (
                candidate.strip()
                for candidate in lines[index + 1 :]
                if candidate.strip()
            ),
            "",
        )
        if (
            previous
            and following
            and re.search(f"[{JAPANESE_OR_PUNCTUATION}]$", previous)
            and re.match(f"^[{JAPANESE_OR_PUNCTUATION}]", following)
        ):
            continue
        cleaned.append(line)
    return cleaned


def normalize_ocr_text_for_reading(
    text: str,
    skip_pages: set[int] | None = None,
) -> str:
    """OCR の空白・改ページ・繰り返し行を読み上げ用に整形する。"""
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    # 日本語文字と日本語句読点の間に OCR が挿入した空白だけを除去する。
    normalized = re.sub(
        f"(?<=[{JAPANESE_OR_PUNCTUATION}])[ \t]+"
        f"(?=[{JAPANESE_OR_PUNCTUATION}])",
        "",
        normalized,
    )
    excluded = skip_pages or set()
    pages = [
        page
        for page_number, page in enumerate(normalized.split("\f"), start=1)
        if page_number not in excluded
    ]
    pages = _remove_repeated_page_edges(pages)

    cleaned_pages: list[str] = []
    for page in pages:
        lines = _remove_spurious_japanese_blank_lines(page.splitlines())
        cleaned = _join_wrapped_lines(lines)
        if cleaned:
            cleaned_pages.append(cleaned)

    return "\n\n".join(cleaned_pages).strip() + ("\n" if cleaned_pages else "")


def create_readaloud_text(
    raw_text_path: Path,
    output_path: Path,
    skip_pages: set[int] | None = None,
) -> bool:
    """OCR sidecar を整形して読み上げ用 TXT を保存する。"""
    if raw_text_path.expanduser().resolve() == output_path.expanduser().resolve():
        print(
            "  エラー: OCR入力テキストと読み上げ用TXTに"
            "同じファイルが指定されています。"
        )
        return False
    try:
        raw_text = raw_text_path.read_text(encoding="utf-8")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = create_sibling_temporary_path(output_path)
        temporary_path.write_text(
            normalize_ocr_text_for_reading(raw_text, skip_pages=skip_pages),
            encoding="utf-8",
        )
        os.replace(temporary_path, output_path)
        return True
    except (OSError, UnicodeError) as error:
        print(f"  エラー: 読み上げ用テキストを作成できませんでした: {error}")
        return False
    finally:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)




def sanitize_book_title_for_filename(title: str, max_bytes: int = 180) -> str:
    """Return a filesystem-safe book title without silently changing its meaning."""
    normalized = unicodedata.normalize("NFKC", title)
    normalized = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    if not normalized:
        return ""

    encoded = normalized.encode("utf-8")
    if len(encoded) <= max_bytes:
        return normalized
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8").rstrip(" .")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def infer_book_title(
    ocr_text: str,
    page_limit: int = AUTO_TITLE_PAGE_LIMIT,
) -> tuple[str, float]:
    """Infer a likely title from the first OCR pages and return it with confidence."""
    pages = ocr_text.replace("\r\n", "\n").replace("\r", "\n").split("\f")
    candidates: list[tuple[float, str, bool]] = []
    excluded_patterns = (
        r"^(?:第[0-9０-９一二三四五六七八九十百]+[章節部]|序章|終章|目次)$",
        r"^(?:chapter|contents|目次)\s*[0-9０-９]*$",
        r"^(?:https?://|www\.)",
        r"^[0-9０-９\s./-]+$",
    )

    for page_index, page in enumerate(pages[:page_limit]):
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in page.splitlines()
        ]
        lines = [line for line in lines if line]
        has_book_credit = any(
            re.search(
                r"(?:著|作者|訳|編|監修|出版社|出版)\s*$",
                other_line,
            )
            for other_line in lines
        )
        for line_index, line in enumerate(lines[:20]):
            cleaned = line.strip("『』「」【】[] ")
            length = len(cleaned)
            if length < 2 or length > 80:
                continue
            if any(
                re.search(pattern, cleaned, re.IGNORECASE)
                for pattern in excluded_patterns
            ):
                continue
            if length > 30 and re.search(r"[。！？!?]$", cleaned):
                continue

            score = 5.0 - min(page_index, 4) * 0.75
            score += max(0.0, 2.0 - line_index * 0.2)
            if 4 <= length <= 40:
                score += 2.0
            elif length > 60:
                score -= 2.0
            if len(lines) <= 8:
                score += 1.5
            if re.search(r"[『』【】]", line):
                score += 1.0
            if re.search(r"(?:著|作者|訳|出版社|出版)$", cleaned):
                score -= 3.0
            has_title_marks = bool(re.search(r"[『』【】]", line))
            candidates.append(
                (score, cleaned, has_book_credit or has_title_marks)
            )

    if not candidates:
        return "", 0.0
    score, title, has_title_evidence = max(
        candidates,
        key=lambda candidate: candidate[0],
    )
    safe_title = sanitize_book_title_for_filename(title)
    if not safe_title:
        return "", 0.0
    confidence = max(0.0, min(1.0, (score - 4.0) / 7.0))
    if not has_title_evidence:
        confidence = min(confidence, 0.75)
    return safe_title, confidence


def confirm_inferred_book_title(title: str, confidence: float) -> bool:
    """Confirm an inferred title interactively; require high confidence otherwise."""
    if not sys.stdin.isatty():
        return confidence >= 0.8
    try:
        answer = input(
            f"書籍名を「{title}」として保存しますか？ [Y/n]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n書籍名によるリネームを行いません。")
        return False
    return answer in {"", "y", "yes"}


def auto_title_output_paths(
    paths: dict[str, Path],
    title: str,
    explicitly_named: set[str] | None = None,
) -> dict[str, Path]:
    """Rename default-named outputs to a collision-free title-based family."""
    explicit = explicitly_named or set()
    suffixes = {
        "output": ".pdf",
        "searchable": "_searchable.pdf",
        "ocr_text": "_ocr.txt",
        "readaloud": "_readaloud.txt",
        "quality": "_ocr_quality.json",
    }
    rename_keys = [key for key in paths if key not in explicit and key in suffixes]
    if not rename_keys:
        return paths

    directory = paths["output"].parent
    numbered_title = title
    number = 2
    source_paths = {paths[key].resolve() for key in rename_keys}
    while any(
        (directory / f"{numbered_title}{suffixes[key]}").exists()
        and (
            directory / f"{numbered_title}{suffixes[key]}"
        ).resolve() not in source_paths
        for key in rename_keys
    ):
        numbered_title = f"{title}（{number}）"
        number += 1

    updated = dict(paths)
    completed: list[tuple[Path, Path]] = []
    try:
        for key in rename_keys:
            source = paths[key]
            if not source.exists():
                continue
            target = directory / f"{numbered_title}{suffixes[key]}"
            source.replace(target)
            updated[key] = target
            completed.append((source, target))
    except OSError:
        for source, target in reversed(completed):
            if target.exists() and not source.exists():
                target.replace(source)
        raise
    return updated


def resolve_output_paths(
    output: str,
    searchable: bool,
    searchable_output: str = "",
    ocr_text_output: str = "",
    readaloud_output: str = "",
    ocr_quality_report: str = "",
) -> dict[str, Path]:
    """Resolve, validate, and return all user-visible output paths."""
    output_pdf = Path(output).expanduser().resolve()
    paths = {"output": output_pdf}
    expected_suffixes = {"output": ".pdf"}
    option_names = {"output": "--output"}

    if searchable:
        paths.update(
            {
                "searchable": (
                    Path(searchable_output).expanduser().resolve()
                    if searchable_output
                    else output_pdf.with_name(
                        f"{output_pdf.stem}_searchable.pdf"
                    )
                ),
                "ocr_text": (
                    Path(ocr_text_output).expanduser().resolve()
                    if ocr_text_output
                    else output_pdf.with_name(f"{output_pdf.stem}_ocr.txt")
                ),
                "readaloud": (
                    Path(readaloud_output).expanduser().resolve()
                    if readaloud_output
                    else output_pdf.with_name(
                        f"{output_pdf.stem}_readaloud.txt"
                    )
                ),
                "quality": (
                    Path(ocr_quality_report).expanduser().resolve()
                    if ocr_quality_report
                    else output_pdf.with_name(
                        f"{output_pdf.stem}_ocr_quality.json"
                    )
                ),
            }
        )
        expected_suffixes.update(
            {
                "searchable": ".pdf",
                "ocr_text": ".txt",
                "readaloud": ".txt",
                "quality": ".json",
            }
        )
        option_names.update(
            {
                "searchable": "--searchable-output",
                "ocr_text": "--ocr-text-output",
                "readaloud": "--readaloud-output",
                "quality": "--ocr-quality-report",
            }
        )

    ensure_distinct_paths(
        {option_names[name]: path for name, path in paths.items()}
    )
    for name, expected_suffix in expected_suffixes.items():
        path = paths[name]
        if path.suffix.lower() != expected_suffix:
            raise ValueError(
                f"{option_names[name]} は {expected_suffix} "
                f"ファイルを指定してください: {path}"
            )
    return paths


def confirm_output_overwrite(
    paths: dict[str, Path],
    overwrite: bool,
) -> bool:
    """Ask before replacing existing outputs, unless already authorized by flag."""
    existing = [path for path in paths.values() if path.exists()]
    if not existing:
        return True
    if overwrite:
        return True

    print("既存の出力ファイルがあります:")
    for path in existing:
        print(f"  - {path}")
    while True:
        try:
            answer = input("上書きしますか？ [Y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n上書きを行わず中止します。")
            return False
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            print("上書きを行わず中止します。")
            return False
        print("Y または N で入力してください。")


def exclude_last_captured_page(
    image_paths: list[Path],
    requested: bool,
) -> Path | None:
    """Remove the last capture when end-screen exclusion is enabled."""
    if not requested or len(image_paths) <= 1:
        return None
    removed = image_paths.pop()
    removed.unlink(missing_ok=True)
    return removed






# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def build_argument_parser() -> argparse.ArgumentParser:
    """Create the CLI parser without running the capture workflow."""
    parser = argparse.ArgumentParser(
        description="Kindle for Mac を自動スクリーンショットして PDF 化"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="出力 PDF ファイル名（デフォルト: output/kindle_book.pdf）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の出力ファイルを明示的に上書きする",
    )
    parser.add_argument(
        "-d", "--delay",
        type=float,
        default=5.0,
        help="ページ変化を待つ最大秒数（デフォルト: 5.0）",
    )
    parser.add_argument(
        "--start-delay",
        type=int,
        default=5,
        help="開始前のカウントダウン秒数（デフォルト: 5）",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="最大ページ数（0 = 制限なし）",
    )
    parser.add_argument(
        "--keep-images",
        action="store_true",
        help="中間 PNG ファイルを削除せず残す",
    )
    last_page_group = parser.add_mutually_exclusive_group()
    last_page_group.add_argument(
        "--exclude-last-page",
        action="store_true",
        dest="exclude_last_page",
        help=(
            "通常終了時に最後に保存したページを除外する（デフォルト）"
        ),
    )
    last_page_group.add_argument(
        "--keep-last-page",
        action="store_false",
        dest="exclude_last_page",
        help="通常終了時も最後に保存したページを残す",
    )
    parser.set_defaults(exclude_last_page=True)
    parser.add_argument(
        "--header-height",
        type=int,
        default=None,
        help="切り取るヘッダー高さ（px）。未指定時は自動検出",
    )
    parser.add_argument(
        "--crop-left",
        type=int,
        default=50,
        help="左端から切り取る最小幅（px。デフォルト: 50）",
    )
    parser.add_argument(
        "--no-auto-crop-ui",
        action="store_false",
        dest="auto_crop_ui",
        help="左右端の暗い Kindle UI 帯を自動検出せず、指定した切り取り幅を使う",
    )
    parser.add_argument(
        "--crop-right",
        type=int,
        default=50,
        help="右端から切り取る幅（px。デフォルト: 50）",
    )
    parser.add_argument(
        "--crop-bottom",
        type=int,
        default=0,
        help="下端から切り取る幅（px。デフォルト: 0）",
    )
    parser.add_argument(
        "--pdf-dpi",
        type=int,
        default=300,
        help="通常 PDF に記録する解像度（デフォルト: 300）",
    )
    parser.add_argument(
        "--searchable",
        action="store_true",
        default=True,
        help="OCR を実行して検索可能 PDF を作成する（ocrmypdf が必要）",
    )
    parser.add_argument(
        "--no-searchable",
        action="store_false",
        dest="searchable",
        help="OCR、検索可能 PDF、読み上げ用 TXT の作成を省略する",
    )
    parser.add_argument(
        "--searchable-output",
        default="",
        help="検索可能 PDF の出力先（未指定時は <output>_searchable.pdf）",
    )
    parser.add_argument(
        "--ocr-text-output",
        default="",
        help="OCR 生テキストの出力先（未指定時は <output>_ocr.txt）",
    )
    parser.add_argument(
        "--readaloud-output",
        default="",
        help="読み上げ用テキストの出力先（未指定時は <output>_readaloud.txt）",
    )
    parser.add_argument(
        "--readaloud-skip-pages",
        default="",
        help="読み上げ用 TXT から除外するページ（例: 1-2,5）",
    )
    parser.add_argument(
        "--ocr-lang",
        default=DEFAULT_OCR_LANGUAGE,
        help="OCR 言語（ocrmypdf -l に渡す値。デフォルト: jpn+eng）",
    )
    parser.add_argument(
        "--ocr-model",
        choices=["best", "standard"],
        default=DEFAULT_OCR_MODEL,
        help="OCR モデル（best: 高精度・既定 / standard: 標準・高速）",
    )
    parser.add_argument(
        "--tessdata-best-dir",
        default="",
        help="tessdata_best の保存先（通常は指定不要）",
    )
    parser.add_argument(
        "--ocr-layout",
        choices=["auto", "horizontal", "vertical"],
        default=DEFAULT_OCR_LAYOUT,
        help="本文レイアウト（デフォルト: horizontal）",
    )
    parser.add_argument(
        "--ocr-content-type",
        choices=["document", "manga"],
        default=DEFAULT_OCR_CONTENT_TYPE,
        help=(
            "OCR対象（document: 通常書籍・既定 / "
            "manga: 漫画向けmacOS Vision併用）"
        ),
    )
    parser.add_argument(
        "--manga-text-scope",
        choices=["narrative", "all"],
        default=DEFAULT_MANGA_TEXT_SCOPE,
        help=(
            "漫画OCRの対象（narrative: タイトル・吹き出しを優先して"
            "コード/UIを除外・既定 / all: 認識した全文）"
        ),
    )
    parser.add_argument(
        "--ocr-oversample",
        type=int,
        default=DEFAULT_OCR_OVERSAMPLE_DPI,
        help="OCR 前に補間する最低解像度（デフォルト: 300）",
    )
    parser.add_argument(
        "--ocr-dictionary",
        action="append",
        choices=["common", "ai", "none"],
        default=None,
        help=(
            "Tesseract専門用語辞書。複数指定可"
            "（未指定時: common / 無効化: none）"
        ),
    )
    parser.add_argument(
        "--ocr-user-words",
        action="append",
        default=[],
        metavar="FILE",
        help="追加のTesseract専門用語ファイル（1行1語・複数指定可）",
    )
    parser.add_argument(
        "--no-ocr-adaptive",
        action="store_false",
        dest="ocr_adaptive",
        help="低品質ページの選択的な再 OCR を無効にする",
    )
    parser.add_argument(
        "--no-filter-low-confidence",
        action="store_false",
        dest="filter_low_confidence",
        help="PDF テキスト層と読み上げ用 TXT の低信頼ノイズ除去を無効にする",
    )
    parser.add_argument(
        "--no-ocr-corrections",
        action="store_false",
        dest="ocr_corrections_enabled",
        help="PDF テキスト層へ埋め込む前のOCR補正を無効にする",
    )
    parser.add_argument(
        "--ocr-correction-profile",
        action="append",
        choices=["common", "ai-rag", "rag-accuracy-book", "none"],
        default=None,
        help=(
            "OCR補正プロファイル。複数指定可"
            "（未指定時: common / 無効化: none）"
        ),
    )
    parser.add_argument(
        "--include-figure-text",
        action="store_true",
        help="図表内の文字も検索可能 PDF と読み上げ用 TXT に含める",
    )
    parser.add_argument(
        "--include-list-markers",
        action="store_true",
        help="黒丸やチェック欄などのリスト記号もPDFテキスト層に含める",
    )
    parser.add_argument(
        "--ocr-quality-report",
        default="",
        help="OCR 品質レポートの出力先（未指定時は <output>_ocr_quality.json）",
    )
    parser.add_argument(
        "--pdf-text-layer",
        choices=["readaloud", "standard"],
        default=DEFAULT_PDF_TEXT_LAYER,
        help="検索可能 PDF のテキスト層（デフォルト: readaloud）",
    )
    parser.add_argument(
        "--page-turn-method",
        choices=["click", "key"],
        default="key",
        help="ページ送り方式（click: 右側クリック / key: 下矢印キー）",
    )
    parser.add_argument(
        "--page-turn-retries",
        type=int,
        default=2,
        help="ページ送り失敗時のリトライ回数（デフォルト: 2）",
    )
    parser.set_defaults(
        ocr_adaptive=DEFAULT_OCR_ADAPTIVE,
        filter_low_confidence=DEFAULT_FILTER_LOW_CONFIDENCE,
        ocr_corrections_enabled=DEFAULT_OCR_CORRECTIONS_ENABLED,
        include_figure_text=DEFAULT_INCLUDE_FIGURE_TEXT,
        include_list_markers=DEFAULT_INCLUDE_LIST_MARKERS,
    )
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    numeric_options = {
        "--crop-left": args.crop_left,
        "--crop-right": args.crop_right,
        "--crop-bottom": args.crop_bottom,
    }
    for option, value in numeric_options.items():
        if value < 0:
            parser.error(f"{option} は 0 以上で指定してください")
    if args.header_height is not None and args.header_height < 0:
        parser.error("--header-height は 0 以上で指定してください")
    if args.start_delay < 0:
        parser.error("--start-delay は 0 以上で指定してください")
    if args.max_pages < 0:
        parser.error("--max-pages は 0 以上で指定してください")
    if args.delay <= 0:
        parser.error("--delay は 0 より大きい値を指定してください")
    if args.page_turn_retries < 0:
        parser.error("--page-turn-retries は 0 以上で指定してください")
    if args.pdf_dpi <= 0:
        parser.error("--pdf-dpi は 1 以上で指定してください")
    if args.ocr_oversample <= 0:
        parser.error("--ocr-oversample は 1 以上で指定してください")
    if args.ocr_content_type == "manga" and args.pdf_text_layer != "readaloud":
        parser.error(
            "--ocr-content-type manga は "
            "--pdf-text-layer readaloud と組み合わせてください"
        )
    if args.ocr_dictionary and "none" in args.ocr_dictionary:
        if len(args.ocr_dictionary) > 1:
            parser.error("--ocr-dictionary none は他の辞書と併用できません")
        ocr_dictionaries: list[str] = []
    else:
        ocr_dictionaries = list(
            dict.fromkeys(
                [
                    *DEFAULT_OCR_DICTIONARIES,
                    *(args.ocr_dictionary or []),
                ]
            )
        )
    if args.ocr_correction_profile and "none" in args.ocr_correction_profile:
        if len(args.ocr_correction_profile) > 1:
            parser.error(
                "--ocr-correction-profile none は"
                "他のプロファイルと併用できません"
            )
        correction_profiles: list[str] = []
    else:
        correction_profiles = list(
            dict.fromkeys(args.ocr_correction_profile or ["common"])
        )
    try:
        load_ocr_user_words(
            ocr_dictionaries,
            args.ocr_user_words,
        )
    except (FileNotFoundError, OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    try:
        readaloud_skip_pages = parse_page_ranges(args.readaloud_skip_pages)
    except ValueError as error:
        parser.error(f"--readaloud-skip-pages: {error}")
    try:
        output_was_explicit = args.output is not None
        output_paths = resolve_output_paths(
            output=args.output or DEFAULT_OUTPUT_NAME,
            searchable=args.searchable,
            searchable_output=args.searchable_output,
            ocr_text_output=args.ocr_text_output,
            readaloud_output=args.readaloud_output,
            ocr_quality_report=args.ocr_quality_report,
        )
    except ValueError as error:
        parser.error(str(error))
    if not confirm_output_overwrite(output_paths, overwrite=args.overwrite):
        return 0
    if args.searchable and args.ocr_model == "best":
        try:
            resolve_best_tessdata_dir(
                list(
                    dict.fromkeys(
                        [
                            language
                            for language in args.ocr_lang.split("+")
                            if language
                        ]
                        + (
                            ["jpn_vert"]
                            if args.ocr_content_type == "manga"
                            else []
                        )
                    )
                ),
                configured_path=args.tessdata_best_dir,
            )
        except FileNotFoundError as error:
            parser.error(str(error))

    output_paths["output"].parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_paths["output"].parent / "kindle_tmp_pages"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_pdf = output_paths["output"]

    # ── ウィンドウ検出 ──
    print("Kindle ウィンドウを検索中...")
    bounds = get_kindle_window_bounds()
    if not bounds:
        print(
            "エラー: Kindle ウィンドウが見つかりません。\n"
            "Kindle for Mac を起動して本を開いてから再実行してください。"
        )
        sys.exit(1)

    x, y, w, h = bounds
    print(f"Kindle ウィンドウを検出: 位置=({x}, {y})  サイズ={w}×{h}")
    print()
    print(f"【準備】{args.start_delay} 秒後に開始します。")
    print("  ・Kindle を最前面に表示してください")
    print("  ・キャプチャしたい最初のページを開いてください")
    print(f"  ・ページ送り方式: {args.page_turn_method}")
    print("  ・緊急停止: マウスを画面左上隅に素早く動かす か Ctrl+C")
    print()

    for i in range(args.start_delay, 0, -1):
        print(f"  {i}...", end="\r", flush=True)
        time.sleep(1)

    print("キャプチャ開始！\n")

    # Kindle をフォーカス
    activate_kindle_window()

    image_paths: list[Path] = []
    page_num = 0
    stopped_early = False

    # 最初のページを取得し、表紙からコンテンツ領域を自動検出
    current_bounds = get_kindle_window_bounds()
    if not current_bounds:
        print("\nKindle ウィンドウが見つからなくなりました。")
        return 1
    cover_img = take_screenshot(current_bounds)
    content_box = detect_content_bounds(
        cover_img,
        header_height=args.header_height,
        crop_left=args.crop_left,
        crop_right=args.crop_right,
        crop_bottom=args.crop_bottom,
        auto_crop_ui=args.auto_crop_ui,
    )
    l, t, r, b = content_box
    cw, ch = cover_img.size
    print(f"ウィンドウサイズ: {cw}×{ch} px")
    header_source = "手動指定" if args.header_height is not None else "自動検出"
    left_source = (
        "UI自動検出"
        if args.auto_crop_ui and l > args.crop_left
        else "指定値"
    )
    right_source = (
        "UI自動検出"
        if args.auto_crop_ui and r < cw - args.crop_right
        else "指定値"
    )
    print(
        f"ページ領域を検出: 左={l} 上={t} 右={r} 下={b} "
        f"({r - l}×{b - t} px / 左端: {left_source}・"
        f"右端: {right_source}・"
        f"ヘッダー: {t}px・{header_source})\n"
    )
    img = cover_img.crop(content_box)

    try:
        while True:
            # 現在のページを保存
            page_num += 1
            img_path = tmp_dir / f"page_{page_num:04d}.png"
            img.save(img_path, format="PNG")
            image_paths.append(img_path)
            print(f"  ページ {page_num:4d} をキャプチャしました", end="\r", flush=True)

            # 指定枚数を保存した後は、不要なページ送りを行わずに停止する。
            if args.max_pages > 0 and page_num >= args.max_pages:
                print(f"\n最大ページ数 ({args.max_pages}) に達しました。")
                stopped_early = True
                break

            # ウィンドウ位置を再取得（ウィンドウ移動・リサイズ対応）
            current_bounds = get_kindle_window_bounds()
            if not current_bounds:
                print("\nKindle ウィンドウが見つからなくなりました。")
                stopped_early = True
                break

            # ページ送り + 変化確認（再試行付き）
            new_img = try_turn_page_and_wait(
                prev_img=img,
                bounds=current_bounds,
                primary_method=args.page_turn_method,
                retries=args.page_turn_retries,
                timeout=args.delay,
                content_box=content_box,
            )

            if new_img is None:
                # フォールバック後も変化なし → 最終ページ
                print(f"\n最終ページを検出しました（合計 {page_num} ページ）。")
                break

            img = new_img

    except (KeyboardInterrupt, pyautogui.FailSafeException):
        print(f"\n\n中断しました（{page_num} ページ取得済み）。")
        stopped_early = True

    if not image_paths:
        print("キャプチャされた画像がありません。")
        return 1

    # 通常の終端検出では Kindle の詳細画面が最後に残る可能性が高いため除外する。
    # 最大ページ数への到達や手動停止では本文を落とさないよう、最後のページを残す。
    if exclude_last_captured_page(
        image_paths,
        requested=args.exclude_last_page and not stopped_early,
    ) is not None:
        print("Kindle の詳細画面として最後のページを除外しました。")

    # ── PDF 作成 ──
    print(f"\nPDF を作成中... ({len(image_paths)} ページ)")
    save_images_as_pdf(image_paths, output_pdf, dpi=args.pdf_dpi)
    print(f"PDF を保存しました: {output_pdf.resolve()}")
    print(f"  解像度: {args.pdf_dpi} DPI / PNG をロスレス格納")
    had_errors = False

    # ── OCR による検索可能 PDF 作成 ──
    if args.searchable:
        searchable_output = output_paths["searchable"]
        raw_text_output = output_paths["ocr_text"]
        readaloud_output = output_paths["readaloud"]
        quality_report_output = output_paths["quality"]
        print(f"\n検索可能 PDF を作成中...")
        print(f"  言語: {args.ocr_lang}")
        print(f"  OCR モデル: {args.ocr_model}")
        print(f"  レイアウト: {args.ocr_layout}")
        print(f"  OCR対象: {args.ocr_content_type}")
        if args.ocr_content_type == "manga":
            print(f"  漫画テキスト範囲: {args.manga_text_scope}")
        print(f"  PDF テキスト層: {args.pdf_text_layer}")
        print(
            "  専門用語辞書: "
            + (
                ", ".join(ocr_dictionaries)
                if ocr_dictionaries
                else "無効"
            )
        )
        print(
            "  OCR補正: "
            + ("有効" if args.ocr_corrections_enabled else "無効")
        )
        print(
            "  OCR補正プロファイル: "
            + (
                ", ".join(correction_profiles)
                if correction_profiles
                else "無効"
            )
        )
        print(
            "  図表内テキスト: "
            + ("含める" if args.include_figure_text else "除外")
        )
        print(
            "  リスト記号: "
            + ("含める" if args.include_list_markers else "除外")
        )
        custom_text_layer = args.pdf_text_layer == "readaloud"
        if not custom_text_layer and (
            args.ocr_adaptive
            or args.filter_low_confidence
            or args.ocr_corrections_enabled
            or not args.include_figure_text
            or not args.include_list_markers
        ):
            print(
                "  注: standard テキスト層では選択的再OCRと"
                "低信頼ノイズ除去、OCR補正、"
                "図表内テキスト除外、リスト記号除外を適用しません。"
            )
        if readaloud_skip_pages:
            page_list = ", ".join(
                str(page) for page in sorted(readaloud_skip_pages)
            )
            print(f"  読み上げ除外ページ: {page_list}")

        with tempfile.TemporaryDirectory(
            prefix="kindle-readaloud-source-"
        ) as filtered_directory:
            filtered_text_output = (
                Path(filtered_directory) / "filtered_ocr.txt"
            )
            if make_pdf_searchable(
                output_pdf,
                searchable_output,
                raw_text_output,
                args.ocr_lang,
                ocr_layout=args.ocr_layout,
                oversample_dpi=args.ocr_oversample,
                readaloud_text_layer=custom_text_layer,
                ocr_model=args.ocr_model,
                tessdata_best_dir=args.tessdata_best_dir,
                adaptive_ocr=args.ocr_adaptive,
                filter_low_confidence=args.filter_low_confidence,
                ocr_corrections_enabled=args.ocr_corrections_enabled,
                include_figure_text=args.include_figure_text,
                include_list_markers=args.include_list_markers,
                correction_profiles=correction_profiles,
                ocr_dictionaries=ocr_dictionaries,
                ocr_user_word_paths=args.ocr_user_words,
                filtered_text_path=filtered_text_output,
                quality_report_path=quality_report_output,
                ocr_content_type=args.ocr_content_type,
                manga_text_scope=args.manga_text_scope,
            ):
                print(f"検索可能 PDF を保存しました: {searchable_output.resolve()}")
                print(f"OCR 生テキストを保存しました: {raw_text_output.resolve()}")
                print(
                    f"OCR 品質レポートを保存しました: "
                    f"{quality_report_output.resolve()}"
                )
                readaloud_source = (
                    filtered_text_output
                    if filtered_text_output.exists()
                    else raw_text_output
                )
                if create_readaloud_text(
                    readaloud_source,
                    readaloud_output,
                    skip_pages=readaloud_skip_pages,
                ):
                    print(
                        f"読み上げ用 TXT を保存しました: "
                        f"{readaloud_output.resolve()}"
                    )
                else:
                    had_errors = True
            else:
                had_errors = True
                print("検索可能 PDF の作成に失敗しました。")
                print(
                    "  対処: ocrmypdf と Tesseract の言語データを"
                    "確認してください。"
                )
                print(
                    "  OCRmyPDF: python -m pip install -r requirements.txt\n"
                    "  外部ツール: brew install ghostscript tesseract-lang\n"
                    "  高精度モデル: "
                    "python -m kindle_capture_support.install_ocr_models"
                )

        if not had_errors and not output_was_explicit:
            try:
                ocr_text = raw_text_output.read_text(encoding="utf-8")
                inferred_title, title_confidence = infer_book_title(ocr_text)
                if inferred_title and confirm_inferred_book_title(
                    inferred_title, title_confidence
                ):
                    explicitly_named = {
                        key
                        for key, value in {
                            "searchable": args.searchable_output,
                            "ocr_text": args.ocr_text_output,
                            "readaloud": args.readaloud_output,
                            "quality": args.ocr_quality_report,
                        }.items()
                        if value
                    }
                    output_paths = auto_title_output_paths(
                        output_paths,
                        inferred_title,
                        explicitly_named=explicitly_named,
                    )
                    output_pdf = output_paths["output"]
                    print(f"書籍名をファイル名に設定しました: {output_pdf.name}")
                elif not inferred_title:
                    print("書籍名を推定できなかったため、既定のファイル名を使用します。")
                else:
                    print("書籍名によるリネームを行いませんでした。")
            except (OSError, UnicodeError) as error:
                print(f"書籍名によるリネームを行えませんでした: {error}")

    # ── 中間ファイルの削除 ──
    if not args.keep_images:
        for p in image_paths:
            p.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        print("中間 PNG ファイルを削除しました。")

    if had_errors:
        print("\n一部の出力作成に失敗しました。通常 PDF は保存されています。")
    else:
        print("\n完了！")

    # ── 完了通知ポップアップ ──
    status_label = "一部失敗" if had_errors else "キャプチャ完了"
    msg = f"{status_label}\\n{len(image_paths)} ページ → {output_pdf.name}"
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display dialog "{msg}" buttons {{"OK"}} default button "OK" '
            'with title "kindle-capture"',
        ],
        capture_output=True,
    )
    return 1 if had_errors else 0
