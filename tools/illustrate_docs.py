"""Render real UI examples and original vector callouts for the Markdown guides.

Uses generated demo data and isolated preferences; no user images or progress are read.
SVG sources and unannotated captures stay in artifacts/, published PNGs in docs/images/.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import sys
import tempfile
import time
from html import escape
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRect, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from detretlabel.app import MainWindow, STYLE
from detretlabel.mapping_dialog import MappingDialog
from detretlabel.type_mapping import TypeMapping

OUTPUT = ROOT / "docs" / "images"
SOURCES = ROOT / "artifacts" / "documentation"
BLUE, INK, MUTED = "#2563eb", "#20314c", "#60718b"


class Figure:
    def __init__(self, width, height, title, subtitle):
        self.width, self.height = width, height
        self.items = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#2563eb"/></marker></defs>',
            f'<rect width="{width}" height="{height}" rx="18" fill="#f1f5fb"/>']
        self.text(28, 42, title, 28, INK, True)
        self.text(28, 72, subtitle, 17, MUTED)

    def text(self, x, y, text, size=19, color=INK, bold=False):
        self.items.append(f'<text x="{x}" y="{y}" font-family="Microsoft YaHei UI,Microsoft YaHei,Segoe UI,sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{escape(str(text))}</text>')

    def box(self, x, y, width, height, fill="white", stroke="#d8e2ef", radius=10):
        self.items.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')

    def image(self, pixmap, x, y, width=None):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        width = width or pixmap.width()
        scale = width / pixmap.width()
        height = pixmap.height() * scale
        self.box(x - 1, y - 1, width + 2, height + 2, stroke="#c3d0e1", radius=3)
        encoded = base64.b64encode(bytes(buffer.data())).decode()
        self.items.append(f'<image x="{x}" y="{y}" width="{width}" height="{height}" xlink:href="data:image/png;base64,{encoded}"/>')
        return scale

    def arrow(self, points):
        value = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        self.items.append(f'<polyline points="{value}" fill="none" stroke="{BLUE}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrow)"/>')

    def highlight(self, rect):
        self.items.append(f'<rect x="{rect.x():.1f}" y="{rect.y():.1f}" width="{rect.width():.1f}" height="{rect.height():.1f}" rx="5" fill="none" stroke="{BLUE}" stroke-width="2.5"/>')

    def label(self, number, x, y, title, lines, width=290):
        height = 55 + 25 * len(lines)
        self.box(x, y, width, height)
        self.items.append(f'<circle cx="{x + 23}" cy="{y + 26}" r="15" fill="{BLUE}"/>')
        self.text(x + 18, y + 32, number, 18, "white", True)
        self.text(x + 47, y + 33, title, 20, INK, True)
        for index, line in enumerate(lines):
            self.text(x + 17, y + 63 + index * 25, line, 17)
        return QRectF(x, y, width, height)

    def pin(self, number, x, y):
        self.items.append(f'<circle cx="{x}" cy="{y}" r="15" fill="{BLUE}" stroke="white" stroke-width="2"/>')
        self.text(x - 5, y + 6, number, 18, "white", True)

    def save(self, name):
        svg = "\n".join([*self.items, "</svg>"]).encode("utf-8")
        (SOURCES / f"{name}.svg").write_bytes(svg)
        renderer = QSvgRenderer(svg)
        if not renderer.isValid():
            raise RuntimeError(f"Invalid diagram: {name}")
        canvas = QImage(self.width, self.height, QImage.Format.Format_ARGB32)
        canvas.fill(QColor("white"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        if not canvas.save(str(OUTPUT / f"{name}.png")):
            raise RuntimeError(f"Could not save {name}")


def region(widget, root, crop=None):
    point = widget.mapTo(root, QPoint(0, 0))
    return QRectF(point.x() - (crop.x() if crop else 0), point.y() - (crop.y() if crop else 0), widget.width(), widget.height())

def placed(rect, x, y, scale=1):
    return QRectF(x + rect.x() * scale, y + rect.y() * scale, rect.width() * scale, rect.height() * scale)

def right_note(figure, number, x, y, title, lines, target, width=290):
    note = figure.label(number, x, y, title, lines, width)
    figure.highlight(target)
    figure.arrow([(note.left(), note.center().y()), (note.left() - 16, note.center().y()),
                  (target.right() + 16, target.center().y()), (target.right(), target.center().y())])


def create_scene(path):
    """Original schematic road image, built from geometric shapes."""
    image = QImage(960, 600, QImage.Format.Format_RGB32)
    image.fill(QColor("#e1f0f6"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(0, 330, 960, 270, QColor("#647286"))
    painter.fillRect(0, 290, 960, 40, QColor("#bfcbd8"))
    painter.fillRect(0, 260, 960, 30, QColor("#8bb795"))
    painter.setPen(QPen(QColor("#e8eef6"), 6, Qt.PenStyle.DashLine))
    painter.drawLine(0, 465, 960, 465)
    painter.setPen(Qt.PenStyle.NoPen)
    for x, color in ((290, "#58a6cd"), (550, "#edaf56")):
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(x, 335, 165, 77), 13, 13)
        painter.drawRoundedRect(QRectF(x + 35, 302, 92, 53), 12, 12)
        painter.setBrush(QColor("#e4f2fa"))
        painter.drawRect(x + 48, 311, 32, 29)
        painter.drawRect(x + 85, 311, 29, 29)
        painter.setBrush(QColor("#26364c"))
        painter.drawEllipse(QRectF(x + 23, 391, 36, 36))
        painter.drawEllipse(QRectF(x + 112, 391, 36, 36))
    for x, y, color in ((150, 175, "#b86181"), (805, 165, "#755ba7")):
        painter.setBrush(QColor("#d4ad88"))
        painter.drawEllipse(QRectF(x, y, 32, 32))
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(x - 7, y + 34, 46, 67), 10, 10)
        painter.setBrush(QColor("#384962"))
        painter.drawRect(x, y + 96, 11, 46)
        painter.drawRect(x + 20, y + 96, 11, 46)
    painter.setPen(QColor("#536982"))
    painter.setFont(QFont("Microsoft YaHei UI", 17))
    painter.drawText(QRect(25, 22, 900, 40), Qt.AlignmentFlag.AlignLeft, "演示图片 · 几何图形生成，供操作说明使用")
    painter.end()
    if not image.save(str(path)):
        raise RuntimeError("Could not create the demo image")


def workflow():
    figure = Figure(1240, 295, "一次审核的操作路线", "01 / 02 准备数据；03 审算法；04 查漏检；随时可以保存和导出。")
    entries = [("01", "选择图片", "格式 / 子目录"), ("02", "导入 JSON", "算法 + 可选 GT"),
               ("03", "审核正误", "1 正检 / 2 误检"), ("04", "漏检复核", "B 补框 / 确认完成"),
               ("导出", "Excel + JSON", "未完成时也能导出")]
    for index, (number, title, sub) in enumerate(entries):
        x = 28 + index * 244
        figure.box(x, 105, 208, 106, fill="#ffffff")
        figure.text(x + 15, 134, number, 20, BLUE, True)
        figure.text(x + 15, 166, title, 23, INK, True)
        figure.text(x + 15, 194, sub, 15, MUTED)
        if index < 4:
            figure.arrow([(x + 211, 158), (x + 238, 158)])
    figure.text(32, 251, "R：03 / 04 之间切换；可以先做 04。整图完成 = 03 已完成 + 04 已确认。", 20, INK, True)
    figure.text(32, 278, "GT 始终是参考，不会自动生成最终正检、误检或漏检结论。", 17, MUTED)
    figure.save("02-workflow")


def handoff():
    figure = Figure(1240, 395, "标到一半，如何交给另一台电脑", "先 Ctrl+S 保存，再复制整个图片目录和原始输入数据。")
    figure.box(28, 110, 325, 210)
    figure.text(48, 146, "电脑 A：当前进度", 23, INK, True)
    for index, (line, indent) in enumerate((("images/", 0), ("scene-01.png", 22), (".detretlabel/", 22), ("annotations.json", 44))):
        figure.text(48 + indent, 182 + index * 29, line, 19, BLUE if index > 1 else INK)
    figure.arrow([(365, 212), (425, 212)])
    figure.box(444, 110, 330, 210)
    figure.text(464, 146, "一起携带", 23, INK, True)
    for index, line in enumerate(("① 整个 images 文件夹", "② 相同算法 JSON", "③ 需要的 GT 与配置", "保持图片名、相对目录不变")):
        figure.text(464, 187 + index * 30, line, 18)
    figure.arrow([(786, 212), (846, 212)])
    figure.box(865, 110, 347, 210)
    figure.text(885, 146, "电脑 B：继续审核", 23, INK, True)
    for index, line in enumerate(("选择复制后的 images 目录", "导入同一算法 JSON", "采用相同字段映射", "按 M 找下一张未完成图片")):
        figure.text(885, 187 + index * 30, line, 18)
    figure.text(32, 361, "Excel 是报告快照；恢复标注需要 .detretlabel/annotations.json。", 20, INK, True)
    figure.save("07-handoff")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    SOURCES.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    if not QFontDatabase.families() and os.name == "nt":
        for name in ("msyh.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{name}")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    workflow()
    handoff()
    with tempfile.TemporaryDirectory(prefix="DetRetLabel-docs-") as temp:
        temp = Path(temp).resolve()
        images = temp / "images"
        images.mkdir()
        for number in (1, 2, 3):
            create_scene(images / f"scene-{number:02}.png")
        document = {"results": [{"path": "/tmp/scene-01.png", "detections": [
            {"bbox": [545, 300, 720, 430], "type": "truck", "conf": .55},
            {"bbox": [139, 170, 193, 320], "type": "person", "conf": .96},
            {"bbox": [285, 300, 460, 430], "type": "car", "conf": .96}]},
            {"path": "/tmp/scene-02.png", "detections": []},
            {"path": "/tmp/scene-03.png", "detections": []}]}
        gt = {"results": [{"path": "scene-01.png", "detections": [
            {"bbox": [139, 170, 193, 320], "type": "car", "conf": .7},
            {"bbox": [141, 172, 191, 318], "type": "person", "conf": .9},
            {"bbox": [285, 300, 460, 430], "type": "car", "conf": .85},
            {"bbox": [794, 160, 848, 310], "type": "person"}]}]}
        window = MainWindow(QSettings(str(temp / "settings.ini"), QSettings.Format.IniFormat))
        window.resize(1760, 1050)
        window.show()

        def settle():
            deadline = time.monotonic() + 20
            while window.busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            if window.busy:
                raise RuntimeError("Documentation import timed out")
            app.processEvents()

        def matches():
            reference = window.review.gt_reference
            deadline = time.monotonic() + 10
            while (reference.timer.isActive() or reference._job is not None or reference._pending is not None) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            app.processEvents()
            if reference._job is not None or reference._pending is not None:
                raise RuntimeError("Documentation GT matching timed out")

        def clean_paths():
            window.folder_label.setText("演示图片 / images（3 张）")
            for role, name in (("algorithm", "algorithm.json"), ("ground_truth", "gt-reference.json")):
                window.source_panels[role].path.setText(name)

        try:
            window.scan_folder(images)
            settle()
            for role, data in (("algorithm", document), ("ground_truth", gt)):
                path = temp / f"{role}.json"
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                with patch.object(MappingDialog, "exec", return_value=QDialog.DialogCode.Accepted):
                    window._map_document(data, path, role=role)
                settle()
                window.set_type_mapping(role, TypeMapping({"car": "车辆", "truck": "卡车", "person": "行人"}, Path("type-mapping.txt")))
            clean_paths()
            window.annotation_panel.show_all_targets.setChecked(True)
            window.annotation_panel.show_noncurrent_labels.setChecked(True)
            matches()
            window.grab().save(str(SOURCES / "overview-raw.png"))
            figure = Figure(1600, 900, "DetRetLabel：先认识这六个位置", "图中使用演示数据；按箭头找到对应区域。总览为便于观察，开启了全部算法框和标签。")
            scale = figure.image(window.grab(), 20, 98, 1210)
            targets = [(window.folder_button, "选图片", ["选择目录，勾选图片格式。", "01 / 02 的标题可以折叠。"]),
                       (window.source_panels["algorithm"], "算法 JSON", ["添加结果文件并核对字段。", "框样式与文字可分别配置。"]),
                       (window.source_panels["ground_truth"], "GT 参考 JSON", ["按需添加，字段独立映射。", "GT 不会替你作出判定。"]),
                       (window.annotation_panel.wheel, "03 审正误", ["1 正检 / 2 误检。", "误检后数字键切为原因。"]),
                       (window.target_splitter, "目标表联动", ["算法 / GT 分列显示。", "点算法行即切到对应目标。"]),
                       (window.annotation_panel.export_frame, "05 保存与导出", ["右侧底部固定显示。", "Excel 与 JSON 独立导出。"])]
            for i, (widget, title, lines) in enumerate(targets, 1):
                rect = placed(region(widget, window), 20, 98, scale)
                if i in (1, 2, 3, 5):
                    figure.label(i, 1265, 108 + (i - 1) * 118, title, lines, 310)
                    figure.highlight(rect)
                    if i == 5:
                        x, y = rect.right() - 8, rect.bottom() + 24
                        figure.pin(i, x, y)
                        figure.arrow([(x, y - 15), (x, rect.bottom())])
                    else:
                        x, y = rect.right() + 40, rect.center().y() + (30 if i == 1 else 0)
                        figure.pin(i, x, y)
                        figure.arrow([(x - 15, y), (rect.right(), rect.center().y())])
                else:
                    right_note(figure, i, 1265, 108 + (i - 1) * 118, title, lines, rect, 310)
            figure.text(28, 868, "图片预览支持滚轮缩放、拖动平移；下方表格和审核区的目标序号与叠加框保持一致。", 19, MUTED)
            figure.save("01-overview")

            dialog = MappingDialog(document, "algorithm.json（演示文件）", window)
            dialog.show()
            app.processEvents()
            figure = Figure(1180, 910, "导入 JSON：先核对字段，再确认框格式", "自动识别只是建议；普通 bbox 数组必须确认 xyxy / xywh 的含义。")
            figure.image(dialog.grab(), 20, 98)
            ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
            fields = [(dialog.image_combo, "图片名字段", ["例如 /path 或 /name。", "只用最后的文件名匹配。"]),
                      (dialog.targets_combo, "每图的目标数组", ["例如 /detections。", "平铺格式选“记录本身”。"]),
                      (dialog.format_combo, "框坐标含义", ["xyxy：左上与右下。", "xywh：左上与宽高。"]),
                      (dialog.normalized_coordinates, "是否归一化", ["比例坐标勾选；像素不勾选。", "按实际图片宽高换算。"]),
                      (dialog.score_combo, "种类与置信度", ["JSON 导出需要原始 type。", "没有置信度可不导入。"]),
                      (ok, "确认映射并导入", ["核对完成再点击。", "有问题可返回重新映射。"])]
            for i, (widget, title, lines) in enumerate(fields, 1):
                right_note(figure, i, 862, 110 + (i - 1) * 128, title, lines, placed(region(widget, dialog), 20, 98))
            figure.save("03-field-mapping")
            dialog.close()

            window.auto_gt_overlay.setChecked(True)
            matches()
            result_widget = window.target_splitter.parentWidget()
            figure = Figure(1260, 625, "算法结果 / GT：排序、点选与高亮一起联动", "两栏各自按置信度降序；同分保留原顺序。GT 没有置信度的排在最后。")
            scale = figure.image(result_widget.grab(), 22, 100, 1216)
            table = window.target_tables["ground_truth"]
            cells = [(window.target_tables["algorithm"], 0, 2, "算法表", ["点选行，切换审核对象。", "目标编号与图片中的框一致。"]),
                     (table, 0, 1, "淡绿色行", ["当前目标同类的 IoU 最大者。", "仅供参考，不自动标正检。"]),
                     (table, 2, 1, "淡黄色行", ["整体 IoU 最大，但种类不同。", "只提示，最终由人判断。"])]
            for i, (host, row, column, title, lines) in enumerate(cells, 1):
                cell = host.visualItemRect(host.item(row, column))
                offset = host.viewport().mapTo(result_widget, cell.topLeft())
                target = placed(QRectF(offset.x(), offset.y(), cell.width(), cell.height()), 22, 100, scale)
                label = figure.label(i, 24 + (i - 1) * 416, 475, title, lines, 390)
                figure.highlight(target)
                figure.arrow([(label.center().x(), label.top()), (label.center().x(), label.top() - 20), (target.center().x(), target.bottom())])
            figure.save("05-gt-linkage")
            window.auto_gt_overlay.setChecked(False)

            review, panel = window.review, window.annotation_panel
            review.select(next(t.id for t in review.queue() if t.category == "truck"))
            review.choose_status("false_positive")
            panel.reason_combo.setEditText("逆光造成类别混淆")
            app.processEvents()
            body = panel.widget()
            crop = region(panel.summary_frame, body).toRect().united(region(panel.reason_frame, body).toRect()).adjusted(-2, -3, 2, 4)
            figure = Figure(900, max(855, crop.height() + 125), "03：状态与原因共用数字键", "示例：先按 2 选误检，再按 1 选第一个预设原因；自定义原因用 Enter 确认。")
            figure.image(body.grab(crop), 25, 103)
            fields = [(panel.summary_frame, "先看当前目标", ["当前结果和备注固定在按钮前。", "这里能看到正在审核的对象。"]),
                      (panel.wheel, "按 1 / 2 判正误", ["中间显示目标序号与总数。", "上方 H 按住看全部，G 显示 GT。"]),
                      (panel.reason_buttons_widget, "数字键改选原因", ["原因按钮上的数字即时生效。", "原因确认后推进下一未审目标。"]),
                      (panel.reason_combo, "自定义也可以", ["输入新的说明后按 Enter。", "留空按 Enter 表示跳过原因。"])]
            for i, (widget, title, lines) in enumerate(fields, 1):
                right_note(figure, i, 420, 115 + (i - 1) * 180, title, lines,
                           placed(region(widget, body, crop), 25, 103), 450)
            figure.save("04-review-keys")
            review.choose_reason("种类检错")

            review.start_review()
            with patch("detretlabel.review_controller.choose_manual_category", return_value=("行人", True)):
                review.add_manual((794, 160, 848, 310))
            review.choose_reason("完全未检出")
            panel.show_noncurrent_labels.setChecked(False)
            window.gt_tabs.setCurrentWidget(window.target_tables["manual"])
            app.processEvents()
            figure = Figure(1260, 775, "04：人工补漏框，再确认本图复核", "按 R 可提前进入；03 未完成时，完成 04 仍会保留图片的未完成标记。")
            figure.image(window.image_view.grab(), 22, 102, 790)
            scale = figure.image(panel.review_frame.grab(), 850, 104, 380)
            for number, widget, title, lines, y in (
                    (1, panel.review_draw_button, "只在 04 中按 B 补框", ["拖动绘制紫色人工框。", "填写种类、原因和备注。"], 505),
                    (2, panel.finish_review_button, "确认本图 04", ["空格完成并保存。", "随后进入下一张未完成图片。"], 640)):
                target = placed(region(widget, panel.review_frame), 850, 104, scale)
                note = figure.label(number, 850, y, title, lines, 380)
                figure.highlight(target)
                figure.arrow([(note.left(), note.center().y()), (830, note.center().y()), (830, target.center().y()), (target.left(), target.center().y())])
            figure.label(3, 24, 610, "人工补框保持紫色", ["它是人工漏检结论，线型随 GT 配置。", "显示 GT 仍由 G 独立控制，不会自动决定漏检。"], 766)
            figure.save("06-missed-review")
        finally:
            window.close()
            app.processEvents()
    print("Documentation illustrations:", OUTPUT)


if __name__ == "__main__":
    main()
