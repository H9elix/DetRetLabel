"""Offscreen Qt integration checks: actual widgets, worker tasks and image decoding."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, Qt, QUrl
from PySide6.QtGui import QImage, QColor, QFontDatabase, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QGraphicsItem, QHeaderView, QTableWidget

from detretlabel.app import MainWindow, STYLE
from detretlabel.core import Collection, import_results, guess_mapping
from detretlabel.mapping_dialog import MappingDialog
from detretlabel.box_style import BoxStyle, BoxStyleDialog, DEFAULT_BOX_STYLES, MANUAL_BOX_COLOR
from detretlabel.app_info import AUTHOR, BUILD


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if not QFontDatabase.families() and os.name == "nt":
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()
        image = QImage(640, 400, QImage.Format.Format_RGB32)
        image.fill(QColor("#bbd6e2"))
        self.assertTrue(image.save(str(self.folder / "0001.jpg")))
        self.assertTrue(image.save(str(self.folder / "0002.png")))
        settings = QSettings(str(self.folder / "test.ini"), QSettings.Format.IniFormat)
        self.window = MainWindow(settings)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait_task()
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_task(self):
        deadline = time.monotonic() + 10
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertFalse(self.window.busy, "background task timed out")
        self.app.processEvents()

    def data(self):
        return {"results": [{"path": "/tmp/0001.jpg", "detections": [
            {"bbox": [10, 20, 100, 200], "type": "person", "conf": .95}]},
            {"path": "0002.png", "detections": []}]}

    def gt_data(self):
        return [{"file_name": "C:\\labels\\0001.jpg", "objects": [
            {"xywh": [20, 30, 40, 50], "category": "reference person"}]},
            {"file_name": "0002.png", "objects": []}]

    def load_source(self, role="algorithm", data=None):
        if self.window.folder is None:
            self.window.scan_folder(self.folder)
            self.wait_task()
        if data is None:
            data = self.gt_data() if role == "ground_truth" else self.data()
        with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.window._map_document(data, self.folder / f"{role}.json", role=role)
        self.wait_task()

    def test_scan_decodes_images_and_populates_list(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.assertEqual(self.window.image_table.rowCount(), 2)
        self.assertEqual(self.window.image_view.dimensions, (640, 400))
        self.assertEqual(self.window.current_image().name, "0001.jpg")
        self.window.image_table.setFocus()
        self.window.move_image(1)
        self.assertEqual(self.window.current_image().name, "0002.png")
        self.window.search.setText("0001")
        self.assertEqual(self.window.current_image().name, "0001.jpg")
        self.window.search.setText("does-not-exist")
        self.assertIsNone(self.window.current_image())

    def test_help_menu_information_and_both_document_previews(self):
        actions = [action.text() for action in self.window.help_menu.actions()]
        self.assertIn("作者：H9elix", actions)
        self.assertEqual(AUTHOR, "H9elix")
        self.assertRegex(BUILD, r"^Build\d{8}$")
        self.assertIn(f"打包：{BUILD}", actions)
        self.window.readme_action.trigger()
        help_window = self.window._help_dialog
        self.app.processEvents()
        self.assertTrue(help_window.isVisible())
        self.assertFalse(help_window.isModal())
        self.assertIn("快速开始", help_window.tabs.currentWidget().toPlainText())
        self.assertIn(BUILD, help_window.info.text())
        self.window.guide_action.trigger()
        self.assertIs(self.window._help_dialog, help_window)
        self.assertEqual(help_window.tabs.currentIndex(), 1)
        self.assertIn("换电脑或交给别人", help_window.tabs.currentWidget().toPlainText())
        help_window.close()
        self.window.readme_action.trigger()
        self.assertIs(self.window._help_dialog, help_window)
        self.assertTrue(help_window.isVisible())
        self.assertEqual(help_window.tabs.currentIndex(), 0)
        help_window.close()

    def test_help_relative_links_heading_navigation_and_search(self):
        self.window.show_help(0)
        help_window = self.window._help_dialog
        readme = help_window.tabs.widget(0)
        help_window.open_link(readme, QUrl("docs/使用指南.md#8-保存恢复与换机交接"))
        self.app.processEvents()
        self.assertEqual(help_window.tabs.currentIndex(), 1)
        guide = help_window.tabs.currentWidget()
        self.assertGreater(guide.verticalScrollBar().value(), 0)
        help_window.search.setText("换机交接")
        help_window.search.setFocus()
        QTest.keyClick(help_window.search, Qt.Key.Key_Return)
        self.assertTrue(help_window.isVisible())
        self.assertEqual(guide.textCursor().selectedText(), "换机交接")
        help_window.search.setText("不存在的文档内容-123456789")
        help_window.find_next()
        self.assertIn("未找到", help_window.message.text())
        help_window.open_link(guide, QUrl("../README.md"))
        self.assertEqual(help_window.tabs.currentIndex(), 0)
        help_window.open_link(readme, QUrl("../../outside.txt"))
        self.assertIn("只打开程序内", help_window.message.text())
        self.assertEqual(help_window.tabs.count(), 2)
        help_window.close()

    def test_illustrated_help_loads_relative_images_and_fits_after_resizing(self):
        self.window.show_help(0)
        help_window = self.window._help_dialog

        def images(browser):
            formats = []
            block = browser.document().begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isImageFormat():
                        formats.append(fragment.charFormat().toImageFormat())
                    iterator += 1
                block = block.next()
            return formats

        for tab, expected in ((0, 2), (1, 7)):
            help_window.tabs.setCurrentIndex(tab)
            QTest.qWait(20)
            browser = help_window.tabs.currentWidget()
            self.assertEqual(len(images(browser)), expected)
            for format_ in images(browser):
                resource = browser.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(format_.name()))
                self.assertIsNotNone(resource)
                self.assertGreater(resource.width(), 0)
                self.assertGreater(format_.width(), 0)
                self.assertLessEqual(format_.width(), browser.viewport().width())
                self.assertAlmostEqual(format_.height() / format_.width(), resource.height() / resource.width())
        browser = help_window.tabs.currentWidget()
        wide_width = images(browser)[0].width()
        help_window.resize(740, 600)
        QTest.qWait(20)
        narrow_width = images(browser)[0].width()
        self.assertLess(narrow_width, wide_width)
        self.assertLessEqual(narrow_width, browser.viewport().width())
        help_window.resize(1200, 800)
        QTest.qWait(20)
        self.assertGreater(images(browser)[0].width(), narrow_width)
        self.assertTrue(browser.isReadOnly())
        help_window.close()

    def test_mapping_dialog_autodetects_and_can_select_xywh(self):
        dialog = MappingDialog(self.data(), "test.json", self.window)
        mapping = dialog.mapping()
        self.assertEqual(mapping.collection, Collection("/results"))
        self.assertEqual(mapping.image_field, "/path")
        self.assertEqual(mapping.detections_field, "/detections")
        self.assertEqual(mapping.box_field, "/bbox")
        dialog.format_combo.setCurrentIndex(1)
        self.assertEqual(dialog.mapping().box_format, "xywh")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_navigation_shortcuts_do_not_steal_search_editing_keys(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.window.search.setFocus()
        self.window.search.setText("000")
        self.window.search.setCursorPosition(2)
        QTest.keyClick(self.window.search, Qt.Key.Key_Left)
        self.assertEqual(self.window.search.cursorPosition(), 1)
        self.assertEqual(self.window.current_image().name, "0001.jpg")
        QTest.keyClick(self.window.search, Qt.Key.Key_F)
        self.assertEqual(self.window.search.text(), "0f00")
        # The mouse navigation buttons still work with the search field focused.
        self.window.search.setText("000")
        self.window.next_button.click()
        self.assertEqual(self.window.current_image().name, "0002.png")

    def test_manual_coordinate_mapping_is_used(self):
        data = [{"path": "0001.jpg", "L": 1, "T": 2, "R": 10, "B": 20}]
        dialog = MappingDialog(data, "custom.json", self.window)
        dialog.box_mode.setCurrentIndex(1)
        for combo, pointer in zip(dialog.coordinate_combos, ("/L", "/T", "/R", "/B")):
            dialog._select(combo, pointer)
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        result = import_results(data, dialog.mapping(), [self.folder / "0001.jpg"])
        self.assertEqual(result.matched_target_count, 1)

    def test_empty_results_mapping_is_valid(self):
        for data in ([], [{"name": "0001.jpg", "detections": []}]):
            dialog = MappingDialog(data, "empty.json", self.window)
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_four_coordinate_fields_do_not_overlap_after_style_resize_and_mode_switches(self):
        dialog = MappingDialog(self.data(), "test.json", self.window)
        dialog.show()
        for size in ((820, 780), (660, 640)):
            dialog.resize(*size)
            for mode in (1, 0, 1):
                dialog.box_mode.setCurrentIndex(mode)
                for box_format in ("xyxy", "xywh"):
                    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(box_format))
                    self.app.processEvents()
                    page = dialog.box_stack.currentWidget()
                    fields = dialog.coordinate_combos if mode else [dialog.box_combo]
                    self.assertGreaterEqual(dialog.box_stack.height(), page.minimumSizeHint().height())
                    for control in fields:
                        bottom = control.mapTo(dialog, QPoint(0, control.height())).y()
                        self.assertLessEqual(bottom, dialog.category_combo.mapTo(dialog, QPoint()).y())
                    self.assertLess(dialog.category_combo.mapTo(dialog, QPoint()).y(),
                                    dialog.score_combo.mapTo(dialog, QPoint()).y())
        dialog.close()

    def test_normalized_mapping_imports_real_image_dimensions_and_survives_rescan(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        data = [{"name": "0001.jpg", "width": 123, "height": 456,
                 "bbox": [.1, .2, .4, .4], "type": "car", "score": .9}]
        def accept_normalized(dialog):
            dialog.normalized_coordinates.setChecked(True)
            dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("xywh"))
            return QDialog.DialogCode.Accepted
        with patch.object(MappingDialog, "exec", accept_normalized):
            self.window._map_document(data, self.folder / "normalized.json")
        self.wait_task()
        source = self.window.sources["algorithm"]
        self.assertTrue(source.mapping.normalized)
        target = source.result.detections[self.folder / "0001.jpg"][0]
        for actual, expected in zip(target.box, (64, 80, 320, 240)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(self.window.image_view.overlay_items["algorithm"][0].rect().width(), 256)
        self.assertEqual(self.window.target_tables["algorithm"].item(0, 3).text(), "64")
        dialog = MappingDialog(data, "normalized.json", self.window, source.mapping)
        self.assertTrue(dialog.normalized_coordinates.isChecked())
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("xyxy"))
        self.assertTrue(dialog.mapping().normalized)
        signature = source.signature
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.assertEqual(source.signature, signature)
        self.assertEqual(source.result.detections[self.folder / "0001.jpg"][0].box, target.box)

    def test_normalized_gt_mapping_is_independent_and_matches_pixel_algorithm(self):
        self.load_source()
        data = [{"name": "0001.jpg", "xmin": 10 / 640, "ymin": 20 / 400,
                 "xmax": 100 / 640, "ymax": 200 / 400, "type": "person"}]
        def accept_normalized(dialog):
            dialog.normalized_coordinates.setChecked(True)
            return QDialog.DialogCode.Accepted
        with patch.object(MappingDialog, "exec", accept_normalized):
            self.window._map_document(data, self.folder / "normalized-gt.json", role="ground_truth")
        self.wait_task()
        reference = self.window.review.gt_reference
        deadline = time.monotonic() + 5
        while (reference.timer.isActive() or reference._job is not None or reference._pending is not None) and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertFalse(self.window.sources["algorithm"].mapping.normalized)
        self.assertTrue(self.window.sources["ground_truth"].mapping.normalized)
        self.assertEqual(self.window.sources["ground_truth"].result.detections[self.folder / "0001.jpg"][0].box,
                         (10, 20, 100, 200))
        self.assertIsNotNone(reference.result)
        self.assertAlmostEqual(reference.result.same_iou, 1)

    def test_refresh_loads_preview_once_and_restores_automatic_column_sizes(self):
        with patch.object(self.window, "_selection_changed", wraps=self.window._selection_changed) as selected:
            self.window.scan_folder(self.folder)
            self.wait_task()
        self.assertEqual(selected.call_count, 1)
        self.window.search.setText("0001")
        with patch.object(self.window, "_selection_changed", wraps=self.window._selection_changed) as selected:
            self.load_source()
        self.assertEqual(selected.call_count, 1)
        self.assertEqual(self.window.current_image().name, "0001.jpg")
        self.assertTrue(self.window.image_table.isRowHidden(1))
        header = self.window.image_table.horizontalHeader()
        for column in (1, 2, 3):
            self.assertEqual(header.sectionResizeMode(column), QHeaderView.ResizeMode.ResizeToContents)
        self.assertGreater(header.sectionSize(3), header.sectionSize(1))

    def test_new_gt_overlay_default_is_on_and_explicit_off_preference_is_restored(self):
        self.assertTrue(self.window.auto_gt_overlay.isChecked())
        self.window.auto_gt_overlay.setChecked(False)
        reopened = MainWindow(self.window.settings)
        self.assertFalse(reopened.auto_gt_overlay.isChecked())
        reopened.close()

    def test_import_workflow_and_rematching_when_format_changes(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        data = self.data()
        with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.window._map_document(data, self.folder / "test.json")
        self.wait_task()
        self.assertEqual(self.window.sources["algorithm"].result.matched_target_count, 1)
        self.assertEqual(self.window.target_table.rowCount(), 1)
        self.assertEqual(self.window.target_table.item(0, 1).text(), "person")
        self.window.formats[".jpg"].setChecked(False)
        self.window.filter_timer.stop()
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.assertEqual(len(self.window.images), 1)
        self.assertEqual(self.window.sources["algorithm"].result.issue_counts["图片未匹配"], 1)
        self.assertEqual(self.window.sources["algorithm"].result.matched_target_count, 0)
        self.window.formats[".jpg"].setChecked(True)
        self.window.filter_timer.stop()
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.assertEqual(self.window.sources["algorithm"].result.matched_target_count, 1)
        self.assertEqual(len(self.window.sources["algorithm"].result.referenced), 2)

    def test_cancel_does_not_replace_existing_source(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        data = self.data()
        self.window.sources["algorithm"].path = Path("old.json")
        self.window.sources["algorithm"].document = data
        with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.window._map_document([], Path("new.json"))
        self.assertEqual(self.window.sources["algorithm"].path, Path("old.json"))
        self.assertIs(self.window.sources["algorithm"].document, data)

    def test_two_import_buttons_load_independent_schemas(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        for role, data in (("algorithm", self.data()), ("ground_truth", self.gt_data())):
            path = self.folder / f"{role}.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch("detretlabel.app.QFileDialog.getOpenFileName", return_value=(str(path), "JSON")), \
                 patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
                self.window.source_panels[role].choose.click()
                self.wait_task()
        algorithm, gt = self.window.sources.values()
        self.assertEqual(algorithm.mapping.box_format, "xyxy")
        self.assertEqual(gt.mapping.box_format, "xywh")
        self.assertEqual(algorithm.result.matched_target_count, 1)
        self.assertEqual(gt.result.matched_target_count, 1)
        gt_target = gt.result.detections[self.folder / "0001.jpg"][0]
        self.assertEqual(gt_target.box, (20, 30, 60, 80))
        self.assertIsNone(gt_target.score)
        self.assertEqual(self.window.image_table.item(0, 1).text(), "1")
        self.assertEqual(self.window.image_table.item(0, 2).text(), "1")
        self.assertTrue(self.window.target_tables["algorithm"].isVisible())
        self.assertTrue(self.window.target_tables["ground_truth"].isVisible())
        self.assertIn("Ground Truth", self.window.gt_tabs.tabText(0))
        self.assertEqual(self.window.target_tables["ground_truth"].item(0, 1).text(), "reference person")
        self.assertEqual(self.window.target_tables["ground_truth"].item(0, 2).text(), "—")
        self.assertEqual(self.window.target_tables["algorithm"].item(0, 1).text(), "person")

    def test_replacing_remapping_and_removing_one_source_preserves_the_other(self):
        self.load_source()
        self.load_source("ground_truth")
        algorithm = self.window.sources["algorithm"]
        original = algorithm.result
        self.load_source("ground_truth", [{"name": "0002.png", "bbox": [1, 2, 20, 30], "type": "GT 2"}])
        self.assertIs(algorithm.result, original)
        self.assertEqual(self.window.target_tables["algorithm"].rowCount(), 1)
        with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.window.source_panels["ground_truth"].remap.click()
        self.wait_task()
        self.assertIs(algorithm.result, original)
        self.assertEqual(self.window.sources["ground_truth"].mapping.image_field, "/name")
        self.window.source_panels["ground_truth"].remove.click()
        self.assertIsNone(self.window.sources["ground_truth"].result)
        self.assertIs(algorithm.result, original)
        self.assertEqual(self.window.target_tables["ground_truth"].rowCount(), 0)
        self.assertEqual(self.window.image_table.item(0, 2).text(), "—")

    def test_cancelling_gt_replacement_preserves_both_sources(self):
        self.load_source()
        self.load_source("ground_truth")
        previous = {role: source.result for role, source in self.window.sources.items()}
        with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.window._map_document([], self.folder / "cancelled.json", role="ground_truth")
        for role, result in previous.items():
            self.assertIs(self.window.sources[role].result, result)

    def test_format_rescan_rematches_both_sources(self):
        self.load_source()
        self.load_source("ground_truth")
        self.window.formats[".jpg"].setChecked(False)
        self.window.scan_folder(self.folder)
        self.wait_task()
        for source in self.window.sources.values():
            self.assertEqual(source.result.matched_target_count, 0)
            self.assertEqual(source.result.issue_counts["图片未匹配"], 1)
        self.window.formats[".jpg"].setChecked(True)
        self.window.scan_folder(self.folder)
        self.wait_task()
        for source in self.window.sources.values():
            self.assertEqual(source.result.matched_target_count, 1)
            self.assertFalse(source.result.issues)

    def test_gt_only_import_survives_removing_algorithm(self):
        self.load_source("ground_truth")
        self.assertIsNone(self.window.sources["algorithm"].result)
        self.assertEqual(self.window.sources["ground_truth"].result.matched_target_count, 1)
        self.load_source()
        gt = self.window.sources["ground_truth"].result
        self.window.source_panels["algorithm"].remove.click()
        self.assertIs(self.window.sources["ground_truth"].result, gt)
        self.assertEqual(self.window.image_table.item(0, 1).text(), "—")
        self.assertEqual(self.window.image_table.item(0, 2).text(), "1")

    def test_bad_gt_file_preserves_both_sources(self):
        self.load_source()
        self.load_source("ground_truth")
        previous = {role: source.result for role, source in self.window.sources.items()}
        path = self.folder / "broken.json"
        path.write_text("{bad json", encoding="utf-8")
        with patch("detretlabel.app.QFileDialog.getOpenFileName", return_value=(str(path), "JSON")), \
             patch("detretlabel.app.QMessageBox.warning") as warning:
            self.window.source_panels["ground_truth"].choose.click()
            self.wait_task()
            warning.assert_called_once()
        for role, result in previous.items():
            self.assertIs(self.window.sources[role].result, result)

    def test_issue_report_identifies_each_source(self):
        for role in self.window.sources:
            data = copy.deepcopy(self.data())
            data["results"][0]["detections"][0]["bbox"] = [5, 5, 1, 1]
            self.load_source(role, data)
        captured = []
        def inspect(dialog):
            table = dialog.findChild(QTableWidget)
            captured.extend(table.item(row, 0).text() for row in range(table.rowCount()))
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, "exec", inspect):
            self.window.show_report()
        self.assertEqual(captured, ["算法结果", "Ground Truth"])

    def test_sections_collapse_without_losing_data_and_remember_state(self):
        self.load_source()
        self.load_source("ground_truth")
        previous = self.window.sources["algorithm"].result
        self.app.processEvents()
        expanded_height = self.window.settings_scroll.sizeHint().height()
        list_height = self.window.image_table.height()
        self.window.folder_section.header.click()
        self.window.data_section.header.click()
        self.app.processEvents()
        self.assertTrue(self.window.folder_section.body.isHidden())
        self.assertTrue(self.window.data_section.body.isHidden())
        self.assertLess(self.window.settings_scroll.sizeHint().height(), expanded_height - 150)
        self.assertGreater(self.window.image_table.height(), list_height)
        self.assertIn("2 张", self.window.folder_section.summary.full_text)
        self.assertIn("GT 1", self.window.data_section.summary.full_text)
        self.assertIs(self.window.sources["algorithm"].result, previous)
        self.window.close()
        restored = MainWindow(self.window.settings)
        self.assertFalse(restored.folder_section.is_expanded())
        self.assertFalse(restored.data_section.is_expanded())
        restored.close()
        self.window.folder_section.header.click()
        self.window.data_section.header.click()
        self.assertFalse(self.window.folder_section.body.isHidden())
        self.assertFalse(self.window.data_section.body.isHidden())
        self.assertTrue(self.window.source_panels["ground_truth"].choose.isEnabled())

    def test_pending_format_change_is_applied_after_another_task(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.window.formats[".jpg"].setChecked(False)
        self.assertTrue(self.window.filter_timer.isActive())
        self.window._run("test", lambda: None, lambda value: None)
        self.wait_task()
        self.assertEqual([path.name for path in self.window.images], ["0002.png"])

    def test_failure_keeps_previous_folder_and_filter_state(self):
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.window.formats[".jpg"].setChecked(False)
        self.window.filter_timer.stop()
        with patch("detretlabel.app.QMessageBox.warning") as warning:
            self.window.scan_folder(self.folder / "missing")
            self.wait_task()
            warning.assert_called_once()
        self.assertEqual(self.window.folder, self.folder)
        self.assertEqual(len(self.window.images), 2)
        self.assertTrue(self.window.formats[".jpg"].isChecked())

    def test_corrupt_image_does_not_break_json_matching(self):
        (self.folder / "bad.png").write_bytes(b"not a PNG")
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.window.search.setText("bad")
        self.assertIsNone(self.window.image_view.dimensions)
        self.assertIn("解码失败", self.window.image_title.text())

    def test_overlays_use_each_sources_coordinates_and_style(self):
        self.load_source()
        self.load_source("ground_truth")
        items = self.window.image_view.overlay_items
        self.assertEqual(len(items["algorithm"]), 1)
        self.assertEqual(len(items["ground_truth"]), 1)
        algorithm, gt = items["algorithm"][0], items["ground_truth"][0]
        self.assertEqual(algorithm.rect().getRect(), (10, 20, 90, 180))
        self.assertEqual(gt.rect().getRect(), (20, 30, 40, 50))
        self.assertEqual(algorithm.pen().style(), Qt.PenStyle.SolidLine)
        self.assertEqual(gt.pen().style(), Qt.PenStyle.DashLine)
        for role, group in items.items():
            self.assertEqual(group[0].pen().color().name(),
                             MANUAL_BOX_COLOR if role == "algorithm" else DEFAULT_BOX_STYLES[role].color)
            self.assertTrue(group[0].pen().isCosmetic())
            self.assertEqual(group[0].data(0), role)
        self.assertEqual(self.window.image_view.scene.sceneRect().getRect(), (0, 0, 640, 400))

    def test_style_button_changes_one_source_and_keeps_zoom_and_data(self):
        self.load_source()
        self.load_source("ground_truth")
        view = self.window.image_view
        view.auto_fit = False
        view.scale(1.7, 1.7)
        zoom = view.transform()
        previous = {role: source.result for role, source in self.window.sources.items()}
        style = BoxStyle("#ff00aa", "dot")
        with patch("detretlabel.app.BoxStyleDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.selected_style.return_value = style
            self.window.source_panels["algorithm"].box_style.click()
        self.assertEqual(view.transform(), zoom)
        self.assertEqual(self.window.box_styles["algorithm"], style)
        self.assertEqual(view.overlay_items["algorithm"][0].pen().color().name(), MANUAL_BOX_COLOR)
        self.assertEqual(view.overlay_items["algorithm"][0].pen().style(), Qt.PenStyle.DotLine)
        self.assertEqual(self.window.box_styles["ground_truth"], DEFAULT_BOX_STYLES["ground_truth"])
        self.assertEqual(view.overlay_items["ground_truth"][0].pen().style(), Qt.PenStyle.DashLine)
        self.assertEqual(self.window.settings.value("box_styles/algorithm/color"), style.color)
        for role, result in previous.items():
            self.assertIs(self.window.sources[role].result, result)

    def test_style_cancel_preserves_both_sources_styles(self):
        self.load_source()
        previous = dict(self.window.box_styles)
        with patch("detretlabel.app.BoxStyleDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.window.source_panels["ground_truth"].box_style.click()
            dialog.return_value.selected_style.assert_not_called()
        self.assertEqual(self.window.box_styles, previous)

    def test_styles_can_be_set_before_import_and_restored_at_restart(self):
        styles = {"algorithm": BoxStyle("#a128e8", "dash_dot"), "ground_truth": BoxStyle("#e81343", "dot", show_score=False)}
        for role, style in styles.items():
            with patch("detretlabel.app.BoxStyleDialog") as dialog:
                dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
                dialog.return_value.selected_style.return_value = style
                self.window.source_panels[role].box_style.click()
        self.assertFalse(self.window.image_view.overlay_items)
        restored = MainWindow(self.window.settings)
        self.assertEqual(restored.box_styles, styles)
        restored.close()
        self.load_source()
        self.assertEqual(self.window.image_view.overlay_items["algorithm"][0].pen().style(), Qt.PenStyle.DashDotLine)

    def test_style_dialog_live_preview_color_cancel_and_defaults(self):
        dialog = BoxStyleDialog("算法结果", BoxStyle("#ff00aa", "dot"), DEFAULT_BOX_STYLES["algorithm"], self.window)
        dialog.line_combo.setCurrentIndex(dialog.line_combo.findData("dash_dot_dot"))
        with patch("detretlabel.box_style.QColorDialog.getColor", return_value=QColor("#3498db")):
            dialog.color_button.click()
        style = BoxStyle("#3498db", "dash_dot_dot")
        self.assertEqual(dialog.selected_style(), style)
        self.assertEqual(dialog.preview.box_style, style)
        with patch("detretlabel.box_style.QColorDialog.getColor", return_value=QColor()):
            dialog.color_button.click()
        self.assertEqual(dialog.selected_style(), style)
        dialog.label_checks["show_category"].setChecked(False)
        dialog.label_checks["show_index"].setChecked(True)
        self.assertFalse(dialog.selected_style().show_category)
        self.assertTrue(dialog.selected_style().show_index)
        self.assertEqual(dialog.preview.box_style, dialog.selected_style())
        dialog.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).click()
        self.assertEqual(dialog.selected_style(), DEFAULT_BOX_STYLES["algorithm"])
        self.assertEqual(dialog.preview.box_style, DEFAULT_BOX_STYLES["algorithm"])

    def test_overlays_are_cleared_on_empty_image_removal_and_decode_failure(self):
        self.load_source()
        self.load_source("ground_truth")
        self.window.image_table.setCurrentCell(1, 0)
        self.assertFalse(any(self.window.image_view.overlay_items.values()))
        self.window.image_table.setCurrentCell(0, 0)
        self.window.clear_json("algorithm")
        self.assertEqual(len(self.window.image_view.overlay_items["ground_truth"]), 1)
        self.assertFalse(self.window.image_view.overlay_items["algorithm"])
        (self.folder / "bad.png").write_bytes(b"not an image")
        self.window.scan_folder(self.folder)
        self.wait_task()
        self.window.search.setText("bad")
        self.assertFalse(self.window.image_view.overlay_items)
        self.assertIsNone(self.window.image_view.overlay_root)
        self.assertFalse(self.window.image_view.label_items)
        self.assertFalse(self.window.image_view.label_anchors)

    def test_outside_boxes_are_only_clipped_for_drawing(self):
        data = self.data()
        data["results"][0]["detections"][0]["bbox"] = [-10, -20, 700, 500]
        self.load_source(data=data)
        view = self.window.image_view
        self.assertEqual(view.overlay_items["algorithm"][0].rect().getRect(), (-10, -20, 710, 520))
        self.assertTrue(view.overlay_root.flags() & QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        self.assertEqual(view.scene.sceneRect().getRect(), (0, 0, 640, 400))
        target = self.window.sources["algorithm"].result.detections[self.folder / "0001.jpg"][0]
        self.assertEqual(target.box, (-10, -20, 700, 500))

    def test_invalid_saved_styles_fall_back_to_defaults(self):
        self.window.settings.setValue("box_styles/algorithm/color", "invalid_color")
        self.window.settings.setValue("box_styles/algorithm/line_style", "invalid_line")
        restored = MainWindow(self.window.settings)
        self.assertEqual(restored.box_styles["algorithm"], DEFAULT_BOX_STYLES["algorithm"])
        restored.close()

    def test_labels_show_mapped_type_score_and_skip_missing_gt_score(self):
        self.load_source()
        self.load_source("ground_truth")
        labels = self.window.image_view.label_items
        self.assertEqual(labels["algorithm"][0].full_text, "person · 0.95")
        self.assertEqual(labels["ground_truth"][0].full_text, "reference person")
        self.assertNotIn("None", labels["ground_truth"][0].full_text)
        self.assertTrue(labels["algorithm"][0].flags() & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)

    def test_label_options_apply_independently_and_persist_false_values(self):
        self.load_source()
        self.load_source("ground_truth")
        style = replace(self.window.box_styles["algorithm"], show_category=False,
                        show_score=True, show_index=True, show_source=True)
        with patch("detretlabel.app.BoxStyleDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.selected_style.return_value = style
            self.window.source_panels["algorithm"].box_style.click()
        labels = self.window.image_view.label_items
        self.assertEqual(labels["algorithm"][0].full_text, "算法结果 · #1 · 0.95")
        self.assertEqual(labels["ground_truth"][0].full_text, "reference person")
        restored = MainWindow(self.window.settings)
        self.assertEqual(restored.box_styles["algorithm"], style)
        self.assertEqual(restored.box_styles["ground_truth"], DEFAULT_BOX_STYLES["ground_truth"])
        restored.close()

    def test_gt_overlay_labels_never_show_scores_even_with_legacy_style_enabled(self):
        data = self.data()
        self.load_source(data=data)
        self.window.box_styles["ground_truth"] = replace(DEFAULT_BOX_STYLES["ground_truth"], show_score=True)
        self.load_source("ground_truth", data)
        view = self.window.image_view
        self.window.annotation_panel.gt_button.setChecked(True)
        self.assertEqual(view.label_items["algorithm"][0].full_text, "person · 0.95")
        self.assertEqual(view.label_items["ground_truth"][0].full_text, "person")
        self.assertNotIn("置信度", view.label_items["ground_truth"][0].toolTip())
        self.assertEqual(self.window.sources["ground_truth"].result.detections[self.folder / "0001.jpg"][0].score, .95)
        self.window.box_styles["ground_truth"] = replace(self.window.box_styles["ground_truth"], show_category=False)
        self.window._update_overlays()
        self.assertFalse(view.label_items["ground_truth"])
        self.assertTrue(view.overlay_items["ground_truth"][0].isVisible())

    def test_gt_style_dialog_and_restored_preferences_cannot_enable_score_labels(self):
        self.window.settings.setValue("box_styles/ground_truth/show_score", True)
        reopened = MainWindow(self.window.settings)
        self.assertFalse(reopened.box_styles["ground_truth"].show_score)
        self.assertTrue(reopened.box_styles["algorithm"].show_score)
        reopened.close()
        legacy = replace(DEFAULT_BOX_STYLES["ground_truth"], show_score=True)
        dialog = BoxStyleDialog("Ground Truth", legacy, legacy, self.window, allow_score=False)
        self.assertNotIn("show_score", dialog.label_checks)
        self.assertFalse(dialog.selected_style().show_score)
        self.assertFalse(dialog.preview.box_style.show_score)
        dialog.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).click()
        self.assertFalse(dialog.selected_style().show_score)

    def test_disabling_all_text_keeps_rectangles_and_current_zoom(self):
        self.load_source()
        self.load_source("ground_truth")
        view = self.window.image_view
        view.auto_fit = False
        view.scale(1.6, 1.6)
        zoom = view.transform()
        style = replace(self.window.box_styles["algorithm"], show_category=False,
                        show_score=False, show_index=False, show_source=False)
        with patch("detretlabel.app.BoxStyleDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.selected_style.return_value = style
            self.window.source_panels["algorithm"].box_style.click()
        self.assertFalse(view.label_items["algorithm"])
        self.assertEqual(len(view.overlay_items["algorithm"]), 1)
        self.assertEqual(len(view.label_items["ground_truth"]), 1)
        self.assertEqual(view.transform(), zoom)
        self.assertEqual(self.window.target_tables["algorithm"].rowCount(), 1)

    def test_label_zero_values_and_optional_field_selection(self):
        data = self.data()
        data["results"][0]["detections"][0].update(type=0, conf=0)
        self.load_source(data=data)
        self.assertEqual(self.window.image_view.label_items["algorithm"][0].full_text, "0 · 0")
        all_fields = replace(DEFAULT_BOX_STYLES["algorithm"], show_index=True, show_source=True)
        self.assertEqual(all_fields.label_text(None, None, 3, "算法结果"), "算法结果 · #3")
        self.assertEqual(BoxStyle("#ff0000").label_text(None, None, 1, "算法结果"), "")

    def test_long_labels_stay_inside_image_and_keep_screen_font_size(self):
        self.window.annotation_panel.show_all_targets.setChecked(True)
        self.window.annotation_panel.show_noncurrent_labels.setChecked(True)
        self.window.annotation_panel.gt_button.setChecked(True)
        data = self.data()
        data["results"][0]["detections"][0].update(bbox=[630, 390, 640, 400], type="超长种类" * 100)
        self.load_source(data=data)
        self.load_source("ground_truth", data)
        view = self.window.image_view
        label = view.label_items["algorithm"][0]
        original_height = label.deviceTransform(view.viewportTransform()).mapRect(label.boundingRect()).height()
        self.assertLess(len(label.display_text), len(label.full_text))
        def check_bounds():
            image_rect = view.viewportTransform().mapRect(view.scene.sceneRect())
            for items in view.label_items.values():
                for item in items:
                    rect = item.deviceTransform(view.viewportTransform()).mapRect(item.boundingRect())
                    self.assertGreaterEqual(rect.left(), image_rect.left())
                    self.assertGreaterEqual(rect.top(), image_rect.top())
                    self.assertLessEqual(rect.right(), image_rect.right())
                    self.assertLessEqual(rect.bottom(), image_rect.bottom())
        check_bounds()
        view.auto_fit = False
        view.scale(1.6, 1.6)
        view._layout_overlay_labels()
        check_bounds()
        new_height = label.deviceTransform(view.viewportTransform()).mapRect(label.boundingRect()).height()
        self.assertAlmostEqual(original_height, new_height)

    def test_label_preferences_support_ini_booleans_and_invalid_value_fallback(self):
        default = DEFAULT_BOX_STYLES["algorithm"]
        style = BoxStyle.from_preferences(default.color, default.line_style, default,
            {"show_category": "false", "show_score": "0", "show_index": "true", "show_source": "1"})
        self.assertEqual(style, replace(default, show_category=False, show_score=False, show_index=True, show_source=True))
        invalid = BoxStyle.from_preferences(default.color, default.line_style, default,
                                           {"show_category": "unknown", "show_score": []})
        self.assertEqual(invalid, default)

    def test_coincident_boxes_keep_labels_separate_when_space_is_available(self):
        self.window.annotation_panel.show_all_targets.setChecked(True)
        self.window.annotation_panel.show_noncurrent_labels.setChecked(True)
        self.window.annotation_panel.gt_button.setChecked(True)
        data = self.data()
        target = data["results"][0]["detections"][0]
        data["results"][0]["detections"] = [copy.deepcopy(target) for _ in range(3)]
        self.load_source(data=data)
        self.load_source("ground_truth", data)
        view = self.window.image_view
        rectangles = [item.deviceTransform(view.viewportTransform()).mapRect(item.boundingRect())
                      for labels in view.label_items.values() for item in labels]
        self.assertEqual(len(rectangles), 6)
        for index, rect in enumerate(rectangles):
            self.assertFalse(any(rect.intersects(other) for other in rectangles[index + 1:]))


if __name__ == "__main__":
    unittest.main()
