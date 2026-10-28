"""Render review controls against real sample images without altering their annotations."""
from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from detretlabel.app import MainWindow, STYLE
from detretlabel.core import basename, load_json
from detretlabel.mapping_dialog import MappingDialog
from detretlabel.type_mapping import TypeMapping
from detretlabel.review_report import write_excel


def main():
    app = QApplication([])
    if not QFontDatabase.families() and os.name == "nt":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    pack = ROOT / "test_data" / "coco128"
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    data = load_json(pack / "01_results_xyxy.json")
    first = next(r for r in data["results"] if "000000000064" in r["path"])
    second = next(r for r in data["results"] if len(r["detections"]) == 1)
    data = {"results": [first, second]}
    with tempfile.TemporaryDirectory(prefix="DetRetLabel-review-") as temp:
        folder = Path(temp) / "images"
        folder.mkdir()
        for record in data["results"]:
            shutil.copyfile(pack / "images" / basename(record["path"]), folder / basename(record["path"]))
        settings = QSettings(str(Path(temp) / "preview.ini"), QSettings.Format.IniFormat)
        window = MainWindow(settings)
        window.resize(1440, 960)
        window.show()
        def wait():
            deadline = time.monotonic() + 15
            while window.busy and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(5)
            if window.busy:
                raise RuntimeError("Preview timed out")
            app.processEvents()
        def wait_matches():
            deadline = time.monotonic() + 5
            reference = window.review.gt_reference
            while (reference.timer.isActive() or reference._job is not None or reference._pending is not None) and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(5)
            app.processEvents()
        window.scan_folder(folder)
        wait()
        gt = copy.deepcopy(data)
        for record in gt["results"]:
            for target in record["detections"]:
                target.pop("conf", None)
        for role, document in (("algorithm", data), ("ground_truth", gt)):
            with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
                window._map_document(document, Path(temp) / f"{role}.json", role=role)
            wait()
        window.search.setText("000000000064")
        window.target_tabs.setCurrentIndex(0)
        window.annotation_panel.auto_next.setChecked(False)
        app.processEvents()
        window.grab().save(str(output / "review-status.png"))
        window.review.start_review()
        app.processEvents()
        QTest.qWait(30)
        app.processEvents()
        window.grab().save(str(output / "review-partial-stage4.png"))
        window.review.finish_review()
        app.processEvents()
        window.grab().save(str(output / "review-partial-saved.png"))
        try:
            write_excel(window.review.report_snapshot(), output / "复核未完成示例.xlsx")
        except PermissionError:
            write_excel(window.review.report_snapshot(), output / f"复核未完成示例_{time.strftime('%Y%m%d_%H%M%S')}.xlsx")
        window.review.store.set_image_review(window.current_image().relative_to(folder).as_posix(), window.review.algorithm_signature(), False)
        window.review.changed()
        for role in ("algorithm", "ground_truth"):
            window.set_type_mapping(role, TypeMapping.load(pack / "type-mapping.txt"))
        app.processEvents()
        window.grab().save(str(output / "review-type-mapping.png"))
        wait_matches()
        window.auto_gt_overlay.setChecked(True)
        app.processEvents()
        window.grab().save(str(output / "review-gt-matching.png"))
        # Show both reference cues in a temporary, deliberately conflicting GT example.
        conflicting_gt = copy.deepcopy(gt)
        record = next(r for r in conflicting_gt["results"] if basename(r["path"]) == window.current_image().name)
        same_class = copy.deepcopy(record["detections"][0])
        same_class["bbox"] = [value + (5 if index < 2 else -5) for index, value in enumerate(same_class["bbox"])]
        record["detections"][0]["type"] = "truck"
        record["detections"].append(same_class)
        with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window._map_document(conflicting_gt, Path(temp) / "conflicting-gt-demo.json", role="ground_truth")
        wait()
        wait_matches()
        window.grab().save(str(output / "review-gt-different-class.png"))
        with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window._map_document(gt, Path(temp) / "ground_truth.json", role="ground_truth")
        wait()
        wait_matches()
        window.auto_gt_overlay.setChecked(False)
        window.annotation_panel.show_all_targets.setChecked(True)
        app.processEvents()
        window.grab().save(str(output / "review-all-boxes.png"))
        window.annotation_panel.show_noncurrent_labels.setChecked(True)
        app.processEvents()
        window.grab().save(str(output / "review-all-labels.png"))
        window.annotation_panel.gt_button.setChecked(True)
        app.processEvents()
        window.grab().save(str(output / "review-with-gt.png"))
        window.annotation_panel.show_noncurrent_labels.setChecked(False)
        window.annotation_panel.show_all_targets.setChecked(False)
        window.review.choose_status("false_positive")
        app.processEvents()
        QTest.qWait(30)
        app.processEvents()
        window.grab().save(str(output / "review-reasons.png"))
        window.annotation_panel.widget().grab().save(str(output / "review-panel-reasons.png"))
        window.review.choose_reason("种类检错")
        window.review.move_target(1)
        window.review.choose_status("true_positive")
        window.review.save(explicit=True)
        app.processEvents()
        window.grab().save(str(output / "review-annotated.png"))
        try:
            write_excel(window.review.report_snapshot(), output / "复核报告示例.xlsx")
        except PermissionError:
            write_excel(window.review.report_snapshot(), output / f"复核报告示例_{time.strftime('%Y%m%d_%H%M%S')}.xlsx")
        window.annotation_panel.gt_button.setChecked(False)
        window.review.all_positive()
        app.processEvents()
        QTest.qWait(30)
        app.processEvents()
        window.grab().save(str(output / "review-missed-check.png"))
        with patch("detretlabel.review_controller.choose_manual_category", return_value=("person", True)):
            window.review.add_manual((320, 350, 360, 510))
        app.processEvents()
        QTest.qWait(30)
        app.processEvents()
        window.grab().save(str(output / "review-manual-miss.png"))
        window.review.choose_reason("完全未检出")
        window.review.finish_review()
        app.processEvents()
        window.grab().save(str(output / "review-complete.png"))
        window.resize(1920, 1080)
        results = window.target_splitter.parentWidget()
        results.parentWidget().setSizes([460, 380])
        app.processEvents()
        QTest.qWait(30)
        app.processEvents()
        results.grab().save(str(output / "result-tables-compact.png"))
        window.resize(1040, 720)
        app.processEvents()
        QTest.qWait(30)
        app.processEvents()
        window.grab().save(str(output / "review-small.png"))
        print("Window:", window.width(), window.height(), "reason panel scroll:", window.annotation_panel.verticalScrollBar().maximum())
        window.close()
    print("Review screenshots:", output)


if __name__ == "__main__":
    main()
