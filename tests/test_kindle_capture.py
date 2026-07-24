import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pikepdf
import pyautogui
from PIL import Image, ImageDraw

from ocr_readaloud_plugin import (
    analyze_ocr_page,
    apply_common_ocr_corrections,
    choose_alternate_pagesegmode,
    correct_common_ocr_misrecognitions,
    filter_figure_lines,
    filter_list_marker_words,
    filter_low_confidence_lines,
    find_ocr_review_candidates,
    normalize_line_text,
    normalize_ocr_tree,
    reorder_ocr_tree_by_position,
    should_accept_line_retry,
    should_retry_ocr,
)
from ocrmypdf import BoundingBox, OcrClass, OcrElement
from kindle_capture import (
    confirm_output_overwrite,
    create_readaloud_text,
    detect_content_bounds,
    detect_dark_left_ui_boundary,
    detect_dark_right_ui_boundary,
    exclude_last_captured_page,
    find_post_ocr_candidates,
    load_ocr_user_words,
    main,
    normalize_ocr_text_for_reading,
    parse_page_ranges,
    resolve_output_paths,
    resolve_best_tessdata_dir,
    save_images_as_pdf,
)


class ContentBoundsTests(unittest.TestCase):
    def test_detects_dark_kindle_ui_strip_on_left(self) -> None:
        image = Image.new("RGB", (400, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 79, 299), fill="black")
        draw.polygon(((20, 150), (45, 130), (45, 170)), fill="white")

        self.assertEqual(detect_dark_left_ui_boundary(image), 80)
        self.assertEqual(
            detect_content_bounds(
                image,
                header_height=0,
                crop_left=20,
                crop_right=10,
            )[0],
            80,
        )

    def test_does_not_crop_dark_page_without_clear_boundary(self) -> None:
        image = Image.new("RGB", (400, 300), "black")

        self.assertEqual(detect_dark_left_ui_boundary(image), 0)
        self.assertEqual(detect_dark_right_ui_boundary(image), 400)

    def test_detects_dark_kindle_ui_strip_on_right(self) -> None:
        image = Image.new("RGB", (400, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((320, 0, 399, 299), fill="black")
        draw.polygon(((380, 150), (355, 130), (355, 170)), fill="white")

        self.assertEqual(detect_dark_right_ui_boundary(image), 320)
        self.assertEqual(
            detect_content_bounds(
                image,
                header_height=0,
                crop_left=10,
                crop_right=20,
            )[2],
            320,
        )

    def test_explicit_margins_exclude_footer_before_bottom_detection(self) -> None:
        image = Image.new("RGB", (200, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 40, 180, 249), fill="black")

        bounds = detect_content_bounds(
            image,
            header_height=20,
            crop_left=10,
            crop_right=15,
            crop_bottom=30,
            auto_crop_ui=False,
        )

        self.assertEqual(bounds, (10, 20, 185, 250))


class OutputSafetyTests(unittest.TestCase):
    def test_resolves_distinct_outputs_with_expected_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.pdf"

            paths = resolve_output_paths(str(output), searchable=True)

        self.assertEqual(paths["output"].name, "book.pdf")
        self.assertEqual(paths["searchable"].name, "book_searchable.pdf")
        self.assertEqual(paths["ocr_text"].name, "book_ocr.txt")
        self.assertEqual(paths["readaloud"].name, "book_readaloud.txt")
        self.assertEqual(paths["quality"].name, "book_ocr_quality.json")

    def test_rejects_colliding_or_invalid_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.pdf"
            with self.assertRaisesRegex(ValueError, "同じファイル"):
                resolve_output_paths(
                    str(output),
                    searchable=True,
                    readaloud_output=str(output),
                )
            with self.assertRaisesRegex(ValueError, "\\.txt"):
                resolve_output_paths(
                    str(output),
                    searchable=True,
                    readaloud_output=str(Path(directory) / "speech.pdf"),
                )

    def test_readaloud_output_cannot_overwrite_its_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ocr.txt"
            source.write_text("本文です。", encoding="utf-8")

            self.assertFalse(create_readaloud_text(source, source))
            self.assertEqual(source.read_text(encoding="utf-8"), "本文です。")

    def test_existing_outputs_require_interactive_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.pdf"
            output.write_bytes(b"existing")
            paths = {"output": output}

            with patch("builtins.input", return_value="Y") as prompt:
                self.assertTrue(
                    confirm_output_overwrite(paths, overwrite=False)
                )
                prompt.assert_called_once_with("上書きしますか？ [Y/N]: ")
            with patch("builtins.input", return_value="n"):
                self.assertFalse(
                    confirm_output_overwrite(paths, overwrite=False)
                )
            with patch("builtins.input", return_value=""):
                self.assertFalse(
                    confirm_output_overwrite(paths, overwrite=False)
                )
            with patch("builtins.input") as prompt:
                prompt.side_effect = ["invalid", "yes"]
                self.assertTrue(
                    confirm_output_overwrite(paths, overwrite=False)
                )
            with patch("builtins.input", side_effect=EOFError):
                self.assertFalse(
                    confirm_output_overwrite(paths, overwrite=False)
                )
            self.assertTrue(confirm_output_overwrite(paths, overwrite=True))
            self.assertEqual(output.read_bytes(), b"existing")

    def test_last_capture_can_be_excluded_or_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = [Path(directory) / f"page-{index}.png" for index in range(3)]
            for page in pages:
                page.touch()

            self.assertIsNone(
                exclude_last_captured_page(pages, requested=False)
            )
            self.assertEqual(len(pages), 3)
            removed = exclude_last_captured_page(pages, requested=True)

            self.assertIsNotNone(removed)
            self.assertEqual(len(pages), 2)
            assert removed is not None
            self.assertFalse(removed.exists())


class WorkflowSafetyTests(unittest.TestCase):
    @staticmethod
    def sample_page() -> Image.Image:
        return Image.new("RGB", (240, 320), "white")

    def test_failsafe_still_creates_captured_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.pdf"
            arguments = [
                "kindle_capture.py",
                "--no-searchable",
                "--start-delay",
                "0",
                "--crop-left",
                "0",
                "--crop-right",
                "0",
                "--output",
                str(output),
            ]
            with (
                patch("sys.argv", arguments),
                patch(
                    "kindle_capture.get_kindle_window_bounds",
                    return_value=(0, 0, 240, 320),
                ),
                patch(
                    "kindle_capture.take_screenshot",
                    return_value=self.sample_page(),
                ),
                patch("kindle_capture.activate_kindle_window", return_value=True),
                patch(
                    "kindle_capture.try_turn_page_and_wait",
                    side_effect=pyautogui.FailSafeException(),
                ),
                patch("kindle_capture.subprocess.run"),
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.is_file())

    def test_natural_end_excludes_last_page_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.pdf"
            arguments = [
                "kindle_capture.py",
                "--no-searchable",
                "--start-delay",
                "0",
                "--crop-left",
                "0",
                "--crop-right",
                "0",
                "--output",
                str(output),
            ]
            with (
                patch("sys.argv", arguments),
                patch(
                    "kindle_capture.get_kindle_window_bounds",
                    return_value=(0, 0, 240, 320),
                ),
                patch(
                    "kindle_capture.take_screenshot",
                    return_value=self.sample_page(),
                ),
                patch("kindle_capture.activate_kindle_window", return_value=True),
                patch(
                    "kindle_capture.try_turn_page_and_wait",
                    return_value=None,
                ),
                patch(
                    "kindle_capture.exclude_last_captured_page",
                    return_value=None,
                ) as exclude_last,
                patch("kindle_capture.subprocess.run"),
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            exclude_last.assert_called_once()
            self.assertTrue(exclude_last.call_args.kwargs["requested"])

    def test_keep_last_page_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.pdf"
            arguments = [
                "kindle_capture.py",
                "--no-searchable",
                "--keep-last-page",
                "--start-delay",
                "0",
                "--crop-left",
                "0",
                "--crop-right",
                "0",
                "--output",
                str(output),
            ]
            with (
                patch("sys.argv", arguments),
                patch(
                    "kindle_capture.get_kindle_window_bounds",
                    return_value=(0, 0, 240, 320),
                ),
                patch(
                    "kindle_capture.take_screenshot",
                    return_value=self.sample_page(),
                ),
                patch("kindle_capture.activate_kindle_window", return_value=True),
                patch(
                    "kindle_capture.try_turn_page_and_wait",
                    return_value=None,
                ),
                patch(
                    "kindle_capture.exclude_last_captured_page",
                    return_value=None,
                ) as exclude_last,
                patch("kindle_capture.subprocess.run"),
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            exclude_last.assert_called_once()
            self.assertFalse(exclude_last.call_args.kwargs["requested"])

    def test_ocr_failure_returns_partial_failure_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.pdf"
            arguments = [
                "kindle_capture.py",
                "--start-delay",
                "0",
                "--max-pages",
                "1",
                "--ocr-model",
                "standard",
                "--crop-left",
                "0",
                "--crop-right",
                "0",
                "--output",
                str(output),
            ]
            with (
                patch("sys.argv", arguments),
                patch(
                    "kindle_capture.get_kindle_window_bounds",
                    return_value=(0, 0, 240, 320),
                ),
                patch(
                    "kindle_capture.take_screenshot",
                    return_value=self.sample_page(),
                ),
                patch("kindle_capture.activate_kindle_window", return_value=True),
                patch(
                    "kindle_capture.try_turn_page_and_wait",
                    return_value=None,
                ),
                patch("kindle_capture.make_pdf_searchable", return_value=False),
                patch(
                    "kindle_capture.exclude_last_captured_page",
                    return_value=None,
                ) as exclude_last,
                patch("kindle_capture.subprocess.run"),
            ):
                exit_code = main()

            self.assertEqual(exit_code, 1)
            self.assertTrue(output.is_file())
            exclude_last.assert_called_once()
            self.assertFalse(exclude_last.call_args.kwargs["requested"])


class OcrDictionaryTests(unittest.TestCase):
    def test_loads_selected_dictionaries_and_deduplicates_words(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "common.txt").write_text(
                "# comment\nLLM\nOpenAI\n",
                encoding="utf-8",
            )
            custom = root / "custom.txt"
            custom.write_text("OpenAI\nGraphRAG\n", encoding="utf-8")

            words = load_ocr_user_words(
                ["common"],
                [str(custom)],
                dictionary_dir=root,
            )

        self.assertEqual(words, ["LLM", "OpenAI", "GraphRAG"])

    def test_rejects_missing_or_multiword_dictionary_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.txt").write_text(
                "Parent Page Retrieval\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_ocr_user_words(["bad"], dictionary_dir=root)
            with self.assertRaises(FileNotFoundError):
                load_ocr_user_words(["missing"], dictionary_dir=root)

    def test_allows_disabling_built_in_dictionaries(self) -> None:
        self.assertEqual(load_ocr_user_words([], []), [])

    def test_finds_post_ocr_candidates_with_page_context(self) -> None:
        candidates = find_post_ocr_candidates(
            [
                "正常なページです。",
                "UM を評価者として、Self-RAGLLM が判断します。",
                "BLEU といっつた指標です。",
            ]
        )

        self.assertEqual(
            [(item["page"], item["reason"]) for item in candidates],
            [
                (2, "llm_variant_um"),
                (2, "joined_heading"),
                (3, "confirmed_ocr_error"),
            ],
        )

    def test_finds_newly_confirmed_post_ocr_candidates(self) -> None:
        candidates = find_post_ocr_candidates(
            [
                "[Vv] 前処理",
                "ユューザーは根拠に思実か確認する。",
                "Anthropic が活用を提案して WET,",
            ]
        )

        self.assertEqual(
            [(item["page"], item["reason"]) for item in candidates],
            [
                (1, "checkbox_fragment"),
                (2, "confirmed_ocr_error"),
                (2, "confirmed_ocr_error"),
                (3, "confirmed_ocr_error"),
            ],
        )


class ReadaloudTextTests(unittest.TestCase):
    def test_parses_readaloud_page_ranges(self) -> None:
        self.assertEqual(parse_page_ranges("1-2,5,7-8"), {1, 2, 5, 7, 8})
        with self.assertRaises(ValueError):
            parse_page_ranges("3-1")

    def test_excludes_only_requested_readaloud_pages(self) -> None:
        cleaned = normalize_ocr_text_for_reading(
            "表紙\f奥付\f本文です。",
            skip_pages={1, 2},
        )

        self.assertEqual(cleaned, "本文です。\n")

    def test_removes_japanese_spaces_page_numbers_and_repeated_edges(self) -> None:
        raw_text = (
            "共通ヘッダー\n"
            "日 本 語 の 文 章\n"
            "で す。\n"
            "1\f"
            "共通ヘッダー\n"
            "次 の ページ\n"
            "で す。\n"
            "2"
        )

        cleaned = normalize_ocr_text_for_reading(raw_text)

        self.assertEqual(cleaned, "日本語の文章です。\n\n次のページです。\n")

    def test_preserves_english_word_spaces_and_joins_hyphenation(self) -> None:
        raw_text = "This is an exam-\nple.\n\nSecond paragraph."

        cleaned = normalize_ocr_text_for_reading(raw_text)

        self.assertEqual(cleaned, "This is an example.\n\nSecond paragraph.\n")

    def test_removes_blank_lines_inserted_between_japanese_ocr_lines(self) -> None:
        raw_text = "最初の行です。\n\n次の行です。\n\nEnglish paragraph."

        cleaned = normalize_ocr_text_for_reading(raw_text)

        self.assertEqual(
            cleaned,
            "最初の行です。次の行です。\n\nEnglish paragraph.\n",
        )


class PdfTextLayerTests(unittest.TestCase):
    @staticmethod
    def make_line(
        text: str,
        confidence: float,
        top: float,
        left: float = 10,
        right: float = 200,
        height: float = 20,
    ) -> OcrElement:
        return OcrElement(
            ocr_class=OcrClass.LINE,
            bbox=BoundingBox(left, top, right, top + height),
            children=[
                OcrElement(
                    ocr_class=OcrClass.WORD,
                    bbox=BoundingBox(left, top, right, top + height),
                    text=text,
                    confidence=confidence,
                )
            ],
        )

    @staticmethod
    def make_word_line(
        marker_text: str,
        body_text: str,
        marker_box: BoundingBox,
        body_box: BoundingBox,
    ) -> OcrElement:
        return OcrElement(
            ocr_class=OcrClass.LINE,
            bbox=BoundingBox(
                marker_box.left,
                min(marker_box.top, body_box.top),
                body_box.right,
                max(marker_box.bottom, body_box.bottom),
            ),
            children=[
                OcrElement(
                    ocr_class=OcrClass.WORD,
                    bbox=marker_box,
                    text=marker_text,
                    confidence=0.90,
                ),
                OcrElement(
                    ocr_class=OcrClass.WORD,
                    bbox=body_box,
                    text=body_text,
                    confidence=0.95,
                ),
            ],
        )

    def test_filters_only_low_confidence_symbol_heavy_lines(self) -> None:
        noise = self.make_line("KG $a RX [=", 0.15, 20)
        body = self.make_line("本書の本文です。", 0.92, 100)
        paragraph = OcrElement(
            ocr_class=OcrClass.PARAGRAPH,
            children=[noise, body],
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[paragraph],
        )

        removed = filter_low_confidence_lines(page)

        self.assertEqual(removed, 1)
        self.assertEqual(noise.children, [])
        self.assertNotEqual(body.children, [])

    def test_filters_solid_bullets_and_aligned_checkboxes_only(self) -> None:
        image = Image.new("RGB", (500, 500), "white")
        draw = ImageDraw.Draw(image)
        solid = self.make_word_line(
            "e",
            "最初の項目です。",
            BoundingBox(30, 30, 54, 54),
            BoundingBox(130, 25, 420, 60),
        )
        explicit = self.make_word_line(
            "・",
            "次の項目です。",
            BoundingBox(30, 90, 54, 114),
            BoundingBox(59, 85, 420, 120),
        )
        checkbox_one = self.make_word_line(
            "[V]",
            "前処理",
            BoundingBox(30, 160, 74, 204),
            BoundingBox(100, 155, 260, 210),
        )
        checkbox_two = self.make_word_line(
            "M",
            "検索",
            BoundingBox(30, 230, 74, 274),
            BoundingBox(100, 225, 260, 280),
        )
        numbered = self.make_word_line(
            "1.",
            "番号付き項目です。",
            BoundingBox(30, 310, 54, 334),
            BoundingBox(130, 305, 420, 340),
        )
        ordinary_e = self.make_word_line(
            "e",
            "という変数です。",
            BoundingBox(30, 380, 54, 404),
            BoundingBox(130, 375, 420, 410),
        )
        formula_dot = self.make_word_line(
            ".",
            "Number of claims",
            BoundingBox(30, 450, 35, 455),
            BoundingBox(130, 440, 420, 465),
        )
        for box in ((30, 30, 54, 54), (30, 90, 54, 114)):
            draw.ellipse(box, fill="black")
        for top in (160, 230):
            draw.rectangle((30, top, 74, top + 44), outline="black", width=5)
            draw.line((38, top + 22, 48, top + 34, 68, top + 10), fill="black", width=5)
        draw.ellipse((30, 310, 54, 334), fill="black")
        draw.line((36, 392, 48, 392), fill="black", width=2)
        draw.rectangle((30, 450, 35, 455), fill="black")
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 500, 500),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[
                        solid,
                        explicit,
                        checkbox_one,
                        checkbox_two,
                        numbered,
                        ordinary_e,
                        formula_dot,
                    ],
                )
            ],
        )

        reports = filter_list_marker_words(page, image)

        self.assertEqual(len(reports), 4)
        for line in (solid, explicit, checkbox_one, checkbox_two):
            self.assertEqual(len(line.children), 1)
        self.assertEqual(len(numbered.children), 2)
        self.assertEqual(len(ordinary_e.children), 2)
        self.assertEqual(len(formula_dot.children), 2)
        self.assertEqual(
            {report["detection"] for report in reports},
            {"solid_marker", "aligned_checkbox"},
        )

    def test_filters_aligned_checkboxes_split_into_separate_lines(self) -> None:
        image = Image.new("RGB", (500, 500), "white")
        draw = ImageDraw.Draw(image)
        lines: list[OcrElement] = []
        marker_lines: list[OcrElement] = []
        for top, token, body in (
            (50, "=", "前処理"),
            (170, "[Vf", "検索"),
            (290, "は", "生成"),
        ):
            marker_line = self.make_word_line(
                token,
                "",
                BoundingBox(30, top, 100, top + 80),
                BoundingBox(30, top, 30, top + 80),
            )
            marker_line.children = marker_line.children[:1]
            body_line = self.make_word_line(
                body,
                "",
                BoundingBox(140, top + 10, 300, top + 70),
                BoundingBox(300, top + 10, 300, top + 70),
            )
            body_line.children = body_line.children[:1]
            marker_lines.append(marker_line)
            lines.extend((marker_line, body_line))
            draw.rectangle((30, top, 100, top + 80), outline="black", width=8)
            draw.line(
                (42, top + 42, 58, top + 60, 88, top + 22),
                fill="black",
                width=8,
            )
        orphan_marker = self.make_word_line(
            "[VER",
            "",
            BoundingBox(30, 430, 250, 450),
            BoundingBox(30, 430, 30, 450),
        )
        orphan_marker.children = orphan_marker.children[:1]
        marker_lines.append(orphan_marker)
        lines.append(orphan_marker)
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 500, 500),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=lines,
                )
            ],
        )

        reports = filter_list_marker_words(page, image)

        self.assertEqual(len(reports), 4)
        self.assertTrue(all(not line.children for line in marker_lines))
        self.assertEqual(
            {report["detection"] for report in reports},
            {"aligned_checkbox"},
        )

    def test_filters_text_joined_checkbox_after_two_aligned_anchors(self) -> None:
        image = Image.new("RGB", (500, 500), "white")
        draw = ImageDraw.Draw(image)
        anchor_one = self.make_word_line(
            "[Vf",
            "検索",
            BoundingBox(30, 50, 100, 130),
            BoundingBox(140, 60, 300, 120),
        )
        anchor_two = self.make_word_line(
            "M",
            "生成",
            BoundingBox(30, 170, 100, 250),
            BoundingBox(140, 180, 300, 240),
        )
        residual = self.make_word_line(
            "[Vv]",
            "前処理",
            BoundingBox(30, 290, 150, 330),
            BoundingBox(170, 285, 330, 340),
        )
        for top in (50, 170):
            draw.rectangle((30, top, 100, top + 80), outline="black", width=8)
            draw.line(
                (42, top + 42, 58, top + 60, 88, top + 22),
                fill="black",
                width=8,
            )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 500, 500),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[anchor_one, anchor_two, residual],
                )
            ],
        )

        reports = filter_list_marker_words(page, image)

        self.assertEqual(len(reports), 3)
        self.assertEqual(
            [child.text for child in residual.children],
            ["前処理"],
        )
        self.assertIn(
            "[Vv]",
            {str(report["recognized_as"]) for report in reports},
        )

    def test_filters_colored_figure_region_but_preserves_body(self) -> None:
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((140, 250, 460, 450), fill=(180, 240, 210))
        draw.line((150, 280, 450, 420), fill=(20, 160, 100), width=12)

        before = self.make_line(
            "これは図の前に配置された通常の説明本文です。",
            0.95,
            100,
        )
        figure_lines = [
            self.make_line("Node A", 0.80, 280),
            self.make_line("Node B", 0.75, 340),
            self.make_line("Relation", 0.70, 400),
        ]
        stray_symbol = self.make_line("¢)", 0.80, 500)
        after = self.make_line(
            "これは図の後に配置された通常の説明本文です。",
            0.95,
            600,
        )
        paragraph = OcrElement(
            ocr_class=OcrClass.PARAGRAPH,
            children=[before, *figure_lines, stray_symbol, after],
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 600, 800),
            children=[paragraph],
        )

        removed = filter_figure_lines(page, image)

        self.assertEqual(removed, 4)
        self.assertNotEqual(before.children, [])
        self.assertTrue(all(not line.children for line in figure_lines))
        self.assertEqual(stray_symbol.children, [])
        self.assertNotEqual(after.children, [])

    def test_filters_sparse_screenshot_labels_but_preserves_body(self) -> None:
        image = Image.new("RGB", (600, 900), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 250, 520, 650), outline=(190, 190, 190), width=3)
        draw.rectangle((120, 300, 180, 340), fill=(80, 180, 120))
        draw.rectangle((400, 520, 470, 560), fill=(220, 150, 70))

        before = self.make_line(
            "これは画面例の前に配置された通常の説明本文です。",
            0.95,
            100,
            left=50,
            right=550,
        )
        first_label = self.make_line(
            "EE ニュ",
            0.37,
            310,
            left=110,
            right=200,
        )
        second_label = self.make_line(
            "ー =m",
            0.65,
            540,
            left=390,
            right=490,
        )
        after = self.make_line(
            "これは画面例の後に配置された通常の説明本文です。",
            0.95,
            760,
            left=50,
            right=550,
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 600, 900),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[before, first_label, second_label, after],
                )
            ],
        )

        removed = filter_figure_lines(page, image)

        self.assertEqual(removed, 2)
        self.assertNotEqual(before.children, [])
        self.assertEqual(first_label.children, [])
        self.assertEqual(second_label.children, [])
        self.assertNotEqual(after.children, [])

    def test_filters_monochrome_diagram_layout_but_preserves_body(self) -> None:
        image = Image.new("RGB", (600, 900), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((120, 260, 200, 340), outline="black", width=8)
        draw.line((200, 300, 420, 500), fill="black", width=8)
        draw.ellipse((380, 460, 500, 580), outline="black", width=8)

        before = self.make_line(
            "これは白黒図の前に配置された通常の説明本文です。",
            0.95,
            100,
            left=50,
            right=550,
        )
        diagram_lines = [
            OcrElement(
                ocr_class=OcrClass.LINE,
                bbox=BoundingBox(140, 270, 430, 330),
                children=[
                    OcrElement(
                        ocr_class=OcrClass.WORD,
                        bbox=BoundingBox(140, 270, 430, 330),
                        text="AR Go",
                        confidence=0.40,
                    )
                ],
            ),
            OcrElement(
                ocr_class=OcrClass.LINE,
                bbox=BoundingBox(220, 360, 500, 450),
                children=[
                    OcrElement(
                        ocr_class=OcrClass.WORD,
                        bbox=BoundingBox(220, 360, 500, 450),
                        text="=> F-@-9",
                        confidence=0.35,
                    )
                ],
            ),
            OcrElement(
                ocr_class=OcrClass.LINE,
                bbox=BoundingBox(160, 500, 450, 570),
                children=[
                    OcrElement(
                        ocr_class=OcrClass.WORD,
                        bbox=BoundingBox(160, 500, 450, 570),
                        text="(ざさ -§",
                        confidence=0.45,
                    )
                ],
            ),
        ]
        after = self.make_line(
            "これは白黒図の後に配置された通常の説明本文です。",
            0.95,
            760,
            left=50,
            right=550,
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 600, 900),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[before, *diagram_lines, after],
                )
            ],
        )

        removed = filter_figure_lines(page, image)

        self.assertEqual(removed, 3)
        self.assertNotEqual(before.children, [])
        self.assertTrue(all(not line.children for line in diagram_lines))
        self.assertNotEqual(after.children, [])

    def test_small_figure_text_does_not_make_body_look_oversized(self) -> None:
        image = Image.new("RGB", (600, 1000), "white")
        body_lines = [
            self.make_line(
                f"通常の日本語本文その{index}です。",
                0.95,
                100 + index * 60,
                left=50,
                right=550,
                height=40,
            )
            for index in range(4)
        ]
        small_figure_lines = [
            self.make_line(
                f"Figure label number {index}",
                0.90,
                500 + index * 35,
                left=100,
                right=500,
                height=15,
            )
            for index in range(8)
        ]
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 600, 1000),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[*body_lines, *small_figure_lines],
                )
            ],
        )

        filter_figure_lines(page, image)

        self.assertTrue(all(line.children for line in body_lines))

    def test_retries_sparse_low_confidence_page_once(self) -> None:
        line = self.make_line("KG $a RX [=", 0.20, 20)
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[line],
                )
            ],
        )

        metrics = analyze_ocr_page(page)

        self.assertTrue(should_retry_ocr(metrics))
        self.assertEqual(choose_alternate_pagesegmode(metrics, 6), 11)

    def test_accepts_only_materially_better_line_retry(self) -> None:
        original = {
            "text": "AELOET, EL, FOFEY THEE] TRH ERA, データの",
            "confidence": 0.42,
            "suspicious_ratio": 0.20,
        }
        improved = {
            "text": "をまとめます。ただし、どの手法も万能ではありません。",
            "confidence": 0.91,
            "suspicious_ratio": 0.0,
        }
        still_suspicious = {
            "text": "じてでておりまずすず:",
            "confidence": 0.94,
            "suspicious_ratio": 0.0,
        }

        self.assertTrue(should_accept_line_retry(original, improved))
        self.assertFalse(should_accept_line_retry(original, still_suspicious))

    def test_normalizes_japanese_line_but_preserves_english_spaces(self) -> None:
        self.assertEqual(
            normalize_line_text(["日", "本", "語", "OpenAI", "API"]),
            "日本語 OpenAI API",
        )

    def test_corrects_confirmed_common_ocr_errors_conservatively(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "生成 Al モデルで LIM を使い、ユュユーザーへ説明する。",
            profiles={"ai-rag"},
        )

        self.assertEqual(
            corrected,
            "生成 AI モデルで LLM を使い、ユーザーへ説明する。",
        )
        self.assertEqual(sum(corrections.values()), 3)

    def test_does_not_replace_partial_or_unrelated_tokens(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "LIMIT と lim、および Al 合金を扱う。"
        )

        self.assertEqual(corrected, "LIMIT と lim、および Al 合金を扱う。")
        self.assertEqual(corrections, {})

    def test_book_specific_corrections_are_opt_in(self) -> None:
        source = "LIM を使う。HIE RAG 精度改善。宮崎験監督。"

        common_text, _ = correct_common_ocr_misrecognitions(source)
        ai_text, _ = correct_common_ocr_misrecognitions(
            source,
            profiles={"ai-rag"},
        )
        book_text, _ = correct_common_ocr_misrecognitions(
            source,
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(common_text, source)
        self.assertEqual(
            ai_text,
            "LLM を使う。HIE RAG 精度改善。宮崎験監督。",
        )
        self.assertEqual(
            book_text,
            "LLM を使う。第3章 RAG 精度改善。宮崎駿監督。",
        )

    def test_corrects_ai_terms_but_preserves_aluminum(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "Al スタートアップと Al 分野、AL 分野、OpenAl、OpenAT、Al 合金",
            profiles={"ai-rag"},
        )

        self.assertEqual(
            corrected,
            "AI スタートアップと AI 分野、AI 分野、OpenAI、OpenAI、Al 合金",
        )
        self.assertEqual(sum(corrections.values()), 5)

    def test_corrects_confirmed_llm_variants_by_context(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "LILM や RAG。UM を評価者とし、LLMLas-a-Judge を使う。"
            "LM で回答し、LM に渡す。LM の一般論。",
            profiles={"ai-rag"},
        )

        self.assertEqual(
            corrected,
            "LLM や RAG。LLM を評価者とし、LLM-as-a-Judge を使う。"
            "LLM で回答し、LLM に渡す。LM の一般論。",
        )
        self.assertEqual(sum(corrections.values()), 5)

    def test_corrects_confirmed_review_candidates(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "じてでておりまずすず:",
            profiles={"rag-accuracy-book"},
        )
        self.assertEqual(corrected, "しております：")
        self.assertEqual(sum(corrections.values()), 1)

        corrected, corrections = correct_common_ocr_misrecognitions(
            "AELOET, EL, FOFEY THEE] TRH ERA, データの特性",
            profiles={"rag-accuracy-book"},
        )
        self.assertEqual(
            corrected,
            "をまとめます。ただし、どの手法も「万能」ではありません。"
            "データの特性",
        )
        self.assertEqual(sum(corrections.values()), 1)

        corrected, corrections = correct_common_ocr_misrecognitions(
            "ユーザーのクニエリをチャイルドチャンジンクの埋め込みと"
            "比較レて、親ページプン親チャンクから来てまずよ」"
            "どという ? う形。",
            profiles={"rag-accuracy-book"},
        )
        self.assertEqual(
            corrected,
            "ユーザーのクエリをチャイルドチャンクの埋め込みと"
            "比較して、親ページ／親チャンクから来てますよ」という形。",
        )
        self.assertEqual(sum(corrections.values()), 5)

    def test_corrects_confirmed_residual_errors(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "自分の翼境でモニタリンググ基盤を比較レし、"
            "検索記推論の機能をりリリースする。"
        )

        self.assertEqual(
            corrected,
            "自分の環境でモニタリング基盤を比較レし、"
            "検索&推論の機能をリリースする。",
        )
        self.assertEqual(sum(corrections.values()), 4)

    def test_corrects_newly_confirmed_readaloud_errors(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "ソツールが変更されている場合なあります。"
            "心より人歓迎します。HIE RAG 精度改善。"
            "ユューザーは根拠に思実か確認し、下がりやすぐなります。"
            "マルチモーダレルは画像トナテキストを扱う。"
            "物在のよりモダンなブフォーマシトと宮崎験監督。"
            "幻覚 (Hallucination) J。精度改番。",
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(
            corrected,
            "ツールが変更されている場合があります。"
            "心より歓迎します。第3章 RAG 精度改善。"
            "ユーザーは根拠に忠実か確認し、下がりやすくなります。"
            "マルチモーダルは画像やテキストを扱う。"
            "現在のよりモダンなフォーマットと宮崎駿監督。"
            "幻覚 (Hallucination)」。精度改善。",
        )
        self.assertEqual(sum(corrections.values()), 14)

    def test_corrects_confirmed_list_and_graph_artifacts(self) -> None:
        corrected, corrections = correct_common_ocr_misrecognitions(
            "1. _ 前処理。e _ LanceDB。宮崎験一 (会社. 創設者 )",
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(
            corrected,
            "1. 前処理。e LanceDB。宮崎駿 — (会社. 創設者 )",
        )
        self.assertEqual(sum(corrections.values()), 4)

    def test_reports_suspicious_text_without_changing_it(self) -> None:
        suspicious = self.make_line("じてでておりまずすず :", 0.52, 20)
        body = self.make_line("通常の本文です。", 0.95, 80)
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[suspicious, body],
                )
            ],
        )

        candidates = find_ocr_review_candidates(page)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["text"], "じてでておりまずすず :")
        self.assertIn("repeated_kana", candidates[0]["reasons"])
        self.assertEqual(suspicious.children[0].text, "じてでておりまずすず :")

    def test_applies_correction_to_positioned_pdf_text_line(self) -> None:
        line = self.make_line(
            "クエリに登場しレたエンティティのノフード間を調べる。",
            0.80,
            20,
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[line],
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(
            line.children[0].text,
            "クエリに登場したエンティティのノード間を調べる。",
        )
        self.assertEqual(line.children[0].bbox, line.bbox)
        self.assertEqual(sum(corrections.values()), 2)

    def test_corrects_split_pipeline_only_with_previous_line_context(self) -> None:
        first_line = self.make_line(
            "以下は評価パイプライ",
            0.80,
            20,
        )
        second_line = self.make_line(
            "ジです。",
            0.75,
            50,
        )
        unrelated_line = self.make_line(
            "ジです。",
            0.90,
            100,
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[first_line, second_line, unrelated_line],
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(first_line.children[0].text, "以下は評価パイプライ")
        self.assertEqual(second_line.children[0].text, "ンです。")
        self.assertEqual(unrelated_line.children[0].text, "ジです。")
        self.assertEqual(
            corrections,
            {"ジ -> ン (after パイプライ)": 1},
        )

    def test_corrects_confirmed_errors_split_across_lines(self) -> None:
        lines = [
            self.make_line("BLEU や ROUGE とい", 0.85, 20),
            self.make_line("っつた自動スコアです。", 0.80, 50),
            self.make_line("例 : 100...500 トークンくらい、 BEL", 0.75, 80),
            self.make_line("ベルやセンテンスレベル", 0.85, 110),
            self.make_line("埋め込みと比較レ", 0.88, 140),
            self.make_line("て、関連性を確認します。", 0.90, 170),
            self.make_line("このチャイルドから来てま", 0.80, 200),
            self.make_line("ずよ」どという ? う形。", 0.70, 230),
        ]
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=lines,
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"rag-accuracy-book"},
        )
        combined = "".join(line.children[0].text for line in lines)

        self.assertIn("といった自動スコア", combined)
        self.assertIn("段落レベルやセンテンスレベル", combined)
        self.assertIn("埋め込みと比較して、", combined)
        self.assertIn("来てますよ」という形。", combined)
        self.assertEqual(sum(corrections.values()), 4)

    def test_corrects_newly_confirmed_errors_split_across_lines(self) -> None:
        lines = [
            self.make_line("キャリアの一歩を中", 0.80, 20),
            self.make_line("み出したい方", 0.80, 50),
            self.make_line("適合しやすくなりま", 0.80, 80),
            self.make_line("To ARAL のブラックボックス性", 0.60, 110),
            self.make_line("プロダクションで定番で", 0.80, 140),
            self.make_line("Te", 0.20, 170),
            self.make_line("内積と =", 0.80, 200),
            self.make_line("ークリッド距離", 0.80, 230),
            self.make_line("活用を提案して", 0.80, 260),
            self.make_line("WET,", 0.20, 290),
            self.make_line("Faithfulness が下がり", 0.80, 320),
            self.make_line("やすぐなります。", 0.80, 350),
        ]
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 400),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=lines,
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"rag-accuracy-book"},
        )
        combined = "".join(
            line.children[0].text for line in lines if line.children
        )

        self.assertIn("一歩を踏み出したい方", combined)
        self.assertIn("なります。生成 AI のブラックボックス性", combined)
        self.assertIn("プロダクションで定番です。", combined)
        self.assertNotIn("Te", combined)
        self.assertIn("内積と ユークリッド距離", combined)
        self.assertIn("活用を提案しています。", combined)
        self.assertIn("Faithfulness が下がりやすくなります。", combined)
        self.assertEqual(sum(corrections.values()), 9)

    def test_repairs_confirmed_lines_misordered_around_figure(self) -> None:
        detail = self.make_line("(人名、組織、場所、出来事", 0.85, 20)
        label = self.make_line(
            "e ノード: 文中に現れるエンティティ",
            0.90,
            50,
        )
        sentence_end = self.make_line("す。", 0.95, 80)
        sentence = self.make_line(
            "「構造化された関係性」を活かす点が最大の特徴で",
            0.92,
            110,
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[detail, label, sentence_end, sentence],
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(detail.children, [])
        self.assertEqual(
            label.children[0].text,
            "e ノード: 文中に現れるエンティティ "
            "(人名、組織、場所、出来事",
        )
        self.assertEqual(sentence_end.children, [])
        self.assertEqual(
            sentence.children[0].text,
            "「構造化された関係性」を活かす点が最大の特徴です。",
        )
        self.assertEqual(sum(corrections.values()), 2)

    def test_corrects_ai_before_next_line_context(self) -> None:
        first_line = self.make_line("知識を持ちながらも Al", 0.85, 20)
        second_line = self.make_line("の実務経験がない。", 0.90, 50)
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[first_line, second_line],
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"ai-rag"},
        )

        self.assertEqual(first_line.children[0].text, "知識を持ちながらも AI")
        self.assertEqual(
            corrections,
            {"Al -> AI (before next-line の実務経験)": 1},
        )

    def test_merges_confirmed_split_title_and_sentence(self) -> None:
        title = self.make_line("2.2 LLM-as-a-", 0.80, 20)
        title_continuation = self.make_line("J udge)", 0.70, 50)
        sentence = self.make_line("以下の流れで構成されま", 0.85, 90)
        sentence_continuation = self.make_line("i :", 0.30, 120)
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[
                        title,
                        title_continuation,
                        sentence,
                        sentence_continuation,
                    ],
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(title.children[0].text, "2.2 LLM-as-a-Judge)")
        self.assertEqual(title_continuation.children, [])
        self.assertEqual(
            sentence.children[0].text,
            "以下の流れで構成されます：",
        )
        self.assertEqual(sentence_continuation.children, [])
        self.assertEqual(sum(corrections.values()), 2)

    def test_completes_pipeline_when_continuation_was_filtered(self) -> None:
        line = self.make_line(
            "以下は評価パイプライ",
            0.80,
            20,
        )
        following_body = self.make_line(
            "引用元を示します。",
            0.90,
            50,
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[
                OcrElement(
                    ocr_class=OcrClass.PARAGRAPH,
                    children=[line, following_body],
                )
            ],
        )

        corrections = apply_common_ocr_corrections(
            page,
            profiles={"rag-accuracy-book"},
        )

        self.assertEqual(line.children[0].text, "以下は評価パイプライン")
        self.assertEqual(
            corrections,
            {"パイプライ -> パイプライン (missing continuation)": 1},
        )

    def test_merges_positioned_words_and_removes_page_number_line(self) -> None:
        text_line = OcrElement(
            ocr_class=OcrClass.LINE,
            bbox=BoundingBox(10, 20, 200, 50),
            children=[
                OcrElement(
                    ocr_class=OcrClass.WORD,
                    bbox=BoundingBox(10, 20, 30, 50),
                    text="日",
                ),
                OcrElement(
                    ocr_class=OcrClass.WORD,
                    bbox=BoundingBox(40, 20, 60, 50),
                    text="本",
                ),
                OcrElement(
                    ocr_class=OcrClass.WORD,
                    bbox=BoundingBox(70, 20, 90, 50),
                    text="語",
                ),
            ],
        )
        page_number_line = OcrElement(
            ocr_class=OcrClass.LINE,
            bbox=BoundingBox(95, 270, 105, 290),
            children=[
                OcrElement(
                    ocr_class=OcrClass.WORD,
                    bbox=BoundingBox(95, 270, 105, 290),
                    text="12",
                )
            ],
        )
        paragraph = OcrElement(
            ocr_class=OcrClass.PARAGRAPH,
            children=[text_line, page_number_line],
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[paragraph],
        )

        normalize_ocr_tree(page)

        self.assertEqual(len(text_line.children), 1)
        self.assertEqual(text_line.children[0].text, "日本語")
        self.assertEqual(text_line.children[0].bbox, text_line.bbox)
        self.assertEqual(page_number_line.children, [])

    def test_reorders_paragraphs_and_lines_by_visual_position(self) -> None:
        top = self.make_line("最初の行です。", 0.95, 20)
        middle = self.make_line("途中の行です。", 0.95, 60)
        bottom = self.make_line("最後の行です。", 0.95, 100)
        later_paragraph = OcrElement(
            ocr_class=OcrClass.PARAGRAPH,
            bbox=BoundingBox(10, 60, 200, 120),
            children=[bottom, middle],
        )
        earlier_paragraph = OcrElement(
            ocr_class=OcrClass.PARAGRAPH,
            bbox=BoundingBox(10, 20, 200, 40),
            children=[top],
        )
        page = OcrElement(
            ocr_class=OcrClass.PAGE,
            bbox=BoundingBox(0, 0, 300, 300),
            children=[later_paragraph, earlier_paragraph],
        )

        moved = reorder_ocr_tree_by_position(page)

        self.assertGreater(moved, 0)
        self.assertEqual(
            [
                normalize_line_text(
                    [word.text for word in line.children if word.text]
                )
                for line in page.lines
            ],
            ["最初の行です。", "途中の行です。", "最後の行です。"],
        )


class PdfCreationTests(unittest.TestCase):
    def test_resolves_complete_best_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "jpn.traineddata").touch()
            (directory / "eng.traineddata").touch()
            (directory / "configs").mkdir()
            (directory / "configs" / "hocr").write_text(
                "tessedit_create_hocr 1\n",
                encoding="ascii",
            )
            (directory / "configs" / "txt").write_text(
                "tessedit_create_txt 1\n",
                encoding="ascii",
            )

            resolved = resolve_best_tessdata_dir(
                ["jpn", "eng"],
                configured_path=str(directory),
            )

            self.assertEqual(resolved, directory.resolve())

    def test_embeds_pngs_losslessly_at_requested_dpi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            image_paths = []
            for index, color in enumerate(("white", "lightgray"), start=1):
                path = directory / f"page_{index}.png"
                Image.new("RGB", (100, 200), color).save(path, format="PNG")
                image_paths.append(path)

            output_path = directory / "book.pdf"
            save_images_as_pdf(image_paths, output_path, dpi=300)

            with pikepdf.Pdf.open(output_path) as pdf:
                self.assertEqual(len(pdf.pages), 2)
                page_width = float(pdf.pages[0].MediaBox[2])
                self.assertAlmostEqual(page_width, 24.0, places=2)

            pdf_bytes = output_path.read_bytes()
            self.assertIn(b"/FlateDecode", pdf_bytes)
            self.assertNotIn(b"/DCTDecode", pdf_bytes)


if __name__ == "__main__":
    unittest.main()
