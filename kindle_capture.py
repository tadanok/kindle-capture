#!/usr/bin/env python3
"""
kindle_capture.py — Kindle for Mac を自動スクリーンショットして PDF 化する

使い方:
    python kindle_capture.py                    # デフォルト設定
    python kindle_capture.py -o 本のタイトル.pdf  # 出力ファイル名を指定
    python kindle_capture.py -d 2.0             # ページめくり待機時間を 2 秒に
    python kindle_capture.py --max-pages 50     # 最大 50 ページで停止
    python kindle_capture.py --keep-images      # 中間 PNG を残す
    python kindle_capture.py --searchable       # OCR で検索可能 PDF を作成
    python kindle_capture.py --page-turn-method key  # 下矢印キーでページ送り

事前に必要な権限（システム設定 > プライバシーとセキュリティ）:
    - アクセシビリティ: ターミナル（またはVS Code等）
    - 画面収録:        ターミナル（またはVS Code等）

緊急停止:
    - マウスを画面の左上隅に素早く動かす（pyautogui フェイルセーフ）
    - または Ctrl+C
"""

import sys
import time
import argparse
import shutil
import subprocess
from pathlib import Path
from statistics import median
from typing import cast

# --- 依存ライブラリのチェック ---
try:
    from PIL import Image, ImageChops
except ImportError:
    print("エラー: Pillow がインストールされていません。\n  pip install Pillow")
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


def detect_content_bounds(
    img: Image.Image,
    tolerance: int = 15,
    header_height: int | None = None,
) -> tuple[int, int, int, int]:
    """
    表紙画像から本のページ領域の境界を検出する。

    - 上端: タイトルバーと本文の境界を画像から検出する
    - 左端: y=top の行を x=0 から右へ走査して背景色が終わる地点
    - 右端: y=top の行を x=w-1 から左へ走査して背景色に変わった地点
    - 下端: 下から全行走査して最初の非背景行

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

    # 左端: 固定値
    left = 50

    # 右端: 固定値（画像右端から 100）
    right = w - 50

    # 下端: 下から全行走査して最初の非背景行
    bottom = h
    for y in range(h - 1, h // 2, -1):
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
        new_img = wait_for_page_change(prev_img, timeout=max(1.5, timeout), content_box=content_box)
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

def save_images_as_pdf(image_paths: list[Path], output_path: Path) -> None:
    """PNG ファイルのリストを 1 つの PDF にまとめる。"""
    if not image_paths:
        return

    imgs = [Image.open(p).convert("RGB") for p in image_paths]
    imgs[0].save(
        output_path,
        format="PDF",
        save_all=True,
        append_images=imgs[1:],
    )
    for img in imgs:
        img.close()


def make_pdf_searchable(
    input_pdf: Path,
    output_pdf: Path,
    ocr_lang: str,
    force_ocr: bool = True,
) -> bool:
    """OCRmyPDF で検索可能 PDF を作成する。成功時は True、失敗時は False を返す。"""
    if shutil.which("ocrmypdf") is None:
        print(
            "エラー: ocrmypdf コマンドが見つかりません。\n"
            "インストール: brew install ocrmypdf"
        )
        return False

    if not input_pdf.exists() or input_pdf.stat().st_size == 0:
        print(f"  エラー: 入力 PDF が見つからないか空です: {input_pdf.resolve()}")
        return False

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    # Tesseract の言語データ確認
    requested_langs = [lang for lang in ocr_lang.split("+") if lang]
    try:
        lang_result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        installed_langs = {
            line.strip() for line in lang_result.stdout.splitlines() if line.strip()
        }
        missing_langs = [lang for lang in requested_langs if lang not in installed_langs]
        if missing_langs:
            print(
                "  エラー: Tesseract に以下の言語データがありません: "
                f"{', '.join(missing_langs)}\n"
                "  インストール例: brew install tesseract-lang"
            )
            return False
    except Exception:
        pass

    cmd = ["ocrmypdf", "-l", ocr_lang, "--tesseract-timeout=300"]
    if force_ocr:
        cmd.append("--force-ocr")
    cmd.extend([str(input_pdf), str(output_pdf)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode in (0, 1) and output_pdf.exists():
            print(f"  ✓ OCR 処理が完了しました ({output_pdf.stat().st_size} bytes)")
            return True
        print(f"  エラー: OCR 処理に失敗しました (終了コード {result.returncode})")
        if result.stderr:
            print(result.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("  エラー: OCR 処理がタイムアウトしました (900 秒以上)。")
        return False
    except FileNotFoundError:
        print("  エラー: ocrmypdf が実行できませんでした。")
        return False


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kindle for Mac を自動スクリーンショットして PDF 化"
    )
    parser.add_argument(
        "-o", "--output",
        default="kindle_book.pdf",
        help="出力 PDF ファイル名（デフォルト: kindle_book.pdf）",
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
    parser.add_argument(
        "--header-height",
        type=int,
        default=None,
        help="切り取るヘッダー高さ（px）。未指定時は自動検出",
    )
    parser.add_argument(
        "--searchable",
        action="store_true",
        default=True,
        help="OCR を実行して検索可能 PDF を作成する（ocrmypdf が必要）",
    )
    parser.add_argument(
        "--searchable-output",
        default="",
        help="検索可能 PDF の出力先（未指定時は <output>_searchable.pdf）",
    )
    parser.add_argument(
        "--ocr-lang",
        default="jpn+eng",
        help="OCR 言語（ocrmypdf -l に渡す値。デフォルト: jpn+eng）",
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
    args = parser.parse_args()

    tmp_dir = Path("kindle_tmp_pages")
    tmp_dir.mkdir(exist_ok=True)
    output_pdf = Path(args.output)

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

    # 最初のページを取得し、表紙からコンテンツ領域を自動検出
    current_bounds = get_kindle_window_bounds()
    if not current_bounds:
        print("\nKindle ウィンドウが見つからなくなりました。")
        return
    cover_img = take_screenshot(current_bounds)
    content_box = detect_content_bounds(cover_img, header_height=args.header_height)
    l, t, r, b = content_box
    cw, ch = cover_img.size
    print(f"ウィンドウサイズ: {cw}×{ch} px")
    header_source = "手動指定" if args.header_height is not None else "自動検出"
    print(
        f"ページ領域を検出: 左={l} 上={t} 右={r} 下={b} "
        f"({r - l}×{b - t} px / ヘッダー: {t}px・{header_source})\n"
    )
    img = cover_img.crop(content_box)

    try:
        while True:
            # 最大ページ数チェック
            if args.max_pages > 0 and page_num >= args.max_pages:
                print(f"\n最大ページ数 ({args.max_pages}) に達しました。")
                break

            # 現在のページを保存
            page_num += 1
            img_path = tmp_dir / f"page_{page_num:04d}.png"
            img.save(img_path, format="PNG")
            image_paths.append(img_path)
            print(f"  ページ {page_num:4d} をキャプチャしました", end="\r", flush=True)

            # ウィンドウ位置を再取得（ウィンドウ移動・リサイズ対応）
            current_bounds = get_kindle_window_bounds()
            if not current_bounds:
                print("\nKindle ウィンドウが見つからなくなりました。")
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

    except KeyboardInterrupt:
        print(f"\n\n中断しました（{page_num} ページ取得済み）。")

    if not image_paths:
        print("キャプチャされた画像がありません。")
        return

    # 最後のページは Kindle の詳細ウィンドウのため除去
    if len(image_paths) > 1:
        removed = image_paths.pop()
        removed.unlink(missing_ok=True)
        print(f"最終ページ（詳細ウィンドウ）を除去しました。")

    # ── PDF 作成 ──
    print(f"\nPDF を作成中... ({len(image_paths)} ページ)")
    save_images_as_pdf(image_paths, output_pdf)
    print(f"PDF を保存しました: {output_pdf.resolve()}")

    # ── OCR による検索可能 PDF 作成 ──
    if args.searchable:
        searchable_output = (
            Path(args.searchable_output)
            if args.searchable_output
            else output_pdf.with_name(f"{output_pdf.stem}_searchable.pdf")
        )
        print(f"\n検索可能 PDF を作成中...")
        print(f"  言語: {args.ocr_lang}")
        if make_pdf_searchable(output_pdf, searchable_output, args.ocr_lang):
            print(f"検索可能 PDF を保存しました: {searchable_output.resolve()}")
        else:
            print("検索可能 PDF の作成に失敗しました。")
            print("  対処: ocrmypdf と tesseract の言語データを確認してください。")
            print("  インストール: brew install ocrmypdf tesseract-lang")

    # ── 中間ファイルの削除 ──
    if not args.keep_images:
        for p in image_paths:
            p.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        print("中間 PNG ファイルを削除しました。")

    print("\n完了！")

    # ── 完了通知ポップアップ ──
    msg = f"キャプチャ完了\\n{len(image_paths)} ページ → {output_pdf.name}"
    subprocess.run(
        [
            "osascript", "-e",
            f'display dialog "{msg}" buttons {{"OK"}} default button "OK" with title "kindle-capture"',
        ],
        capture_output=True,
    )


if __name__ == "__main__":
    main()
