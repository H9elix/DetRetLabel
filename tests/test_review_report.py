import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from detretlabel.annotation import AnnotationStore, ReviewTarget
from detretlabel.review_report import build_report, write_excel
from detretlabel.type_mapping import TypeMapping


class ReviewReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.images = [self.folder / "one.jpg", self.folder / "two.png", self.folder / "empty.jpg"]
        self.a = ReviewTarget("a", "one.jpg", "algorithm", (1.25, 2, 31, 42), "001", .912345, "alg.json")
        self.b = replace(self.a, id="b", category="person")
        self.c = replace(self.a, id="c", image="two.png", category=0, score=90)
        self.gt = replace(self.a, id="gt", role="ground_truth")
        self.manual = ReviewTarget.manual("one.jpg", (10, 20, 30, 40), "汽车")
        self.pending = ReviewTarget.manual("two.png", (11, 22, 33, 44))
        self.by_image = {self.images[0]: [self.a, self.b, self.manual, self.gt], self.images[1]: [self.c, self.pending]}
        self.store = AnnotationStore(self.folder)
        self.store.set_annotation(self.a, "true_positive", note="=中文备注")
        self.store.set_annotation(self.b, "false_positive", "种类检错", "保留备注")
        self.store.set_annotation(self.manual, "false_negative", "完全未检出")
        self.store.set_annotation(self.pending, None)
        self.store.set_image_review("one.jpg", "sig", True)
        self.store.set_image_review("empty.jpg", "sig", True)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, **kwargs):
        return build_report(self.folder, self.images, self.by_image, self.store, "sig",
            {"algorithm": TypeMapping({"001": "行人", "0": "汽车"}), "ground_truth": TypeMapping()},
            {"algorithm": "alg.json", "ground_truth": "gt.json"},
            {self.images[2]: "空目标列表"}, **kwargs)

    def test_scope_statistics_completion_and_draft_are_independent_of_gt(self):
        old = replace(self.a, id="old-version", signature="old")
        self.store.set_annotation(old, "false_positive", "旧记录")
        self.store.set_image_review("one.jpg", "sig", True)
        report = self.snapshot(pending_reason=("b", "新原因草稿"))
        self.assertEqual(report.stats, dict(images=3, completed_images=2, pending_images=1,
            algorithm=3, true_positive=1, false_positive=1, unreviewed=1, false_negative=1, pending_manual=1,
            pending_reasons=0, algorithm_completed_images=2, miss_completed_images=2))
        self.assertEqual(len(report.targets), 5)
        self.assertEqual(report.images[0]["gt_count"], 1)
        self.assertEqual(report.images[0]["review_state"], "复核完成")
        self.assertEqual(report.images[2]["input_state"], "空目标列表")
        row = next(row for row in report.targets if row["target_id"] == "b")
        self.assertEqual((row["reason"], row["draft"], row["editing"]), ("种类检错", "新原因草稿", "正在选择原因"))
        self.assertEqual(report.targets[0]["raw_type"], "001")
        self.assertEqual(report.targets[0]["display_type"], "行人")
        self.assertEqual(sum(r["false_negative"] for r in report.categories), 1)
        self.store.set_annotation(self.b, "true_positive")
        self.assertEqual(row["status"], "误检")  # Export snapshot does not follow later UI edits.

    def test_legacy_algorithm_misses_and_mismatched_signatures_remain_pending(self):
        self.store.set_annotation(self.a, "false_negative", "旧数据")
        self.store.set_image_review("one.jpg", "sig", True)
        self.store.set_image_review("empty.jpg", "other", True)
        report = self.snapshot()
        self.assertEqual(report.stats["completed_images"], 0)
        self.assertEqual(report.stats["unreviewed"], 2)
        self.assertEqual(report.targets[0]["status"], "旧漏检，待重审")
        self.assertEqual(report.stats["false_negative"], 1)

    def test_excel_contains_all_sheets_literal_text_coordinates_and_filters(self):
        path = write_excel(self.snapshot(pending_reason=("b", "临时输入")), self.folder / "报告.xlsx")
        book = load_workbook(path)
        try:
            self.assertEqual(book.sheetnames, ["复核概览", "图片进度", "目标明细"])
            sheet = book["目标明细"]
            self.assertEqual(sheet["D5"].value, "001")
            self.assertEqual(sheet["D5"].data_type, "s")
            self.assertEqual(sheet["E5"].value, "行人")
            self.assertEqual(sheet["F5"].value, .912345)
            self.assertEqual(sheet["G5"].value, 1.25)
            self.assertEqual(sheet["O5"].value, "=中文备注")
            self.assertEqual(sheet["O5"].data_type, "s")
            self.assertEqual(sheet["M6"].value, "临时输入")
            self.assertEqual(sheet.freeze_panes, "D5")
            self.assertEqual(sheet.tables["TargetReviewDetails"].ref, "A4:U9")
            self.assertEqual(book["图片进度"].max_row, 7)
            self.assertEqual(book["复核概览"]["B4"].value, "复核进行中")
            self.assertIsNone(book["复核概览"].freeze_panes)
            self.assertEqual(book["图片进度"].freeze_panes, "D5")
            self.assertFalse(any(c.data_type == "f" for s in book for row in s for c in row))
        finally:
            book.close()

    def test_empty_results_still_export_headers_and_image_progress(self):
        report = build_report(self.folder, self.images, {}, AnnotationStore(), "sig", {}, {}, {})
        book = load_workbook(write_excel(report, self.folder / "empty.xlsx"))
        try:
            self.assertEqual(book["图片进度"].max_row, 7)
            self.assertEqual(book["目标明细"].max_row, 4)
            self.assertEqual(report.stats["pending_images"], 3)
        finally:
            book.close()

    def test_partial_algorithm_and_saved_reason_draft_are_distinct_from_completed_miss_review(self):
        self.store.set_annotation(self.b, "false_positive", note="仍需确认原因", reason_pending=True, reason_draft="暂定种类错误")
        self.store.set_image_review("one.jpg", "sig", True)
        report = self.snapshot()
        row = report.images[0]
        self.assertEqual(row["algorithm_state"], "未完成")
        self.assertEqual(row["miss_review_state"], "已完成")
        self.assertEqual(row["review_state"], "03未完成 / 04已完成")
        self.assertTrue(row["miss_completed_at"])
        self.assertFalse(row["completed_at"])
        self.assertEqual(report.stats["completed_images"], 1)
        self.assertEqual(report.stats["miss_completed_images"], 2)
        self.assertEqual(report.stats["pending_reasons"], 1)
        detail = next(r for r in report.targets if r["target_id"] == "b")
        self.assertEqual(detail["draft"], "暂定种类错误")
        self.assertEqual(detail["editing"], "原因待确认")
        book = load_workbook(write_excel(report, self.folder / "partial.xlsx"))
        try:
            progress = book["图片进度"]
            self.assertEqual(progress["H5"].value, "03未完成 / 04已完成")
            self.assertEqual(progress["L5"].value, "未完成")
            self.assertEqual(progress["M5"].value, 1)
            self.assertEqual(progress["N5"].value, "已完成")
            self.assertTrue(progress["O5"].value)
            self.assertEqual(book["目标明细"]["M6"].value, "暂定种类错误")
            self.assertEqual(book["目标明细"]["T6"].value, "未完成")
            self.assertEqual(book["目标明细"]["U6"].value, "已完成")
        finally:
            book.close()

    def test_atomic_failure_preserves_existing_report_and_cleans_temporary_file(self):
        path = self.folder / "existing.xlsx"
        path.write_bytes(b"old report")
        with patch("detretlabel.review_report.os.replace", side_effect=PermissionError("Excel is open")):
            with self.assertRaises(PermissionError):
                write_excel(self.snapshot(), path)
        self.assertEqual(path.read_bytes(), b"old report")
        self.assertFalse(list(self.folder.glob("review-report-*.xlsx")))

    def test_invalid_cell_data_is_reported_without_overwriting(self):
        path = self.folder / "existing.xlsx"
        path.write_bytes(b"old report")
        for invalid in ("text\x00", "长" * 32768):
            with self.subTest(invalid=invalid[:20]):
                self.store.set_annotation(self.a, "true_positive", note=invalid)
                with self.assertRaises(ValueError):
                    write_excel(self.snapshot(), path)
                self.assertEqual(path.read_bytes(), b"old report")
        report = self.snapshot()
        report.targets[0]["score"] = float("nan")
        with self.assertRaises(ValueError):
            write_excel(report, path)


if __name__ == "__main__":
    unittest.main()
