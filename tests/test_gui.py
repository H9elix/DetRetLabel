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

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QImage, QColor, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QGraphicsItem, QTableWidget

from detretlabel.app import MainWindow, STYLE
from detretlabel.core import Collection, import_results, guess_mapping
from detretlabel.mapping_dialog import MappingDialog
from detretlabel.box_style import BoxStyle, BoxStyleDialog, DEFAULT_BOX_STYLES
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
            self.assertEqual(group[0].pen().color().name(), DEFAULT_BOX_STYLES[role].color)
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
        self.assertEqual(view.overlay_items["algorithm"][0].pen().color().name(), style.color)
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
        styles = {"algorithm": BoxStyle("#a128e8", "dash_dot"), "ground_truth": BoxStyle("#e81343", "dot")}
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
