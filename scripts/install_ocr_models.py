#!/usr/bin/env python3
"""Install official tessdata_best models used by kindle_capture.py."""

from __future__ import annotations

import argparse
import re
import urllib.request
from pathlib import Path


DEFAULT_LANGUAGES = ("jpn", "jpn_vert", "eng")
MODEL_BASE_URL = (
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main"
)


def download_model(language: str, destination: Path, force: bool = False) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", language):
        raise ValueError(f"不正な言語名です: {language}")
    output_path = destination / f"{language}.traineddata"
    if output_path.is_file() and output_path.stat().st_size > 0 and not force:
        print(f"取得済み: {language} ({output_path})")
        return
    temporary_path = output_path.with_suffix(".traineddata.download")
    url = f"{MODEL_BASE_URL}/{language}.traineddata"
    print(f"取得中: {language} ({url})")
    try:
        urllib.request.urlretrieve(url, temporary_path)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="公式 tessdata_best OCR モデルをインストール"
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=project_root / "ocr_models" / "tessdata_best",
        help="モデルの保存先",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=list(DEFAULT_LANGUAGES),
        help="取得する言語（デフォルト: jpn jpn_vert eng）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="取得済みモデルも再ダウンロードする",
    )
    args = parser.parse_args()

    destination = args.destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for language in dict.fromkeys(args.languages):
        download_model(language, destination, force=args.force)

    config_dir = destination / "configs"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "hocr").write_text(
        "tessedit_create_hocr 1\nhocr_font_info 0\n",
        encoding="ascii",
    )
    (config_dir / "txt").write_text(
        "tessedit_create_txt 1\n",
        encoding="ascii",
    )
    print(f"高精度OCRモデルをインストールしました: {destination}")


if __name__ == "__main__":
    main()
