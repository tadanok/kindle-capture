#!/usr/bin/env python3
"""Install official tessdata_best models used by kindle_capture.py."""

from __future__ import annotations

import argparse
import hashlib
import re
import urllib.request
from pathlib import Path

from kindle_capture_support.ocr_config import DEFAULT_BEST_TESSDATA_DIR


DEFAULT_LANGUAGES = ("jpn", "jpn_vert", "eng")
MODEL_COMMIT = "e12c65a915945e4c28e237a9b52bc4a8f39a0cec"
MODEL_BASE_URL = (
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/"
    f"{MODEL_COMMIT}"
)
MODEL_SHA256 = {
    "jpn": "36bdf9ac823f5911e624c30d0553e890b8abc7c31a65b3ef14da943658c40b79",
    "jpn_vert": "1258be6eb2a9851f18043234ad18cca13ed32690bfff62b335c898bbea371548",
    "eng": "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(language: str, destination: Path, force: bool = False) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", language):
        raise ValueError(f"不正な言語名です: {language}")
    expected_sha256 = MODEL_SHA256.get(language)
    if expected_sha256 is None:
        raise ValueError(
            f"固定済みモデルではない言語です: {language} "
            f"(対応: {', '.join(MODEL_SHA256)})"
        )
    output_path = destination / f"{language}.traineddata"
    if output_path.is_file() and not force:
        if file_sha256(output_path) == expected_sha256:
            print(f"検証済み: {language} ({output_path})")
            return
        print(f"再取得: {language} (既存ファイルのSHA-256が不一致)")
    temporary_path = output_path.with_suffix(".traineddata.download")
    url = f"{MODEL_BASE_URL}/{language}.traineddata"
    print(f"取得中: {language} ({url})")
    try:
        urllib.request.urlretrieve(url, temporary_path)
        actual_sha256 = file_sha256(temporary_path)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"{language} のSHA-256が一致しません: {actual_sha256}"
            )
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="公式 tessdata_best OCR モデルをインストール"
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_BEST_TESSDATA_DIR,
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
