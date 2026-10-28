"""Small review dialogs for typed manual categories and export repairs."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QHeaderView, QAbstractItemView,
)

from .core import IMAGE_EXTENSIONS, scan_images
from .target_export import TargetExport, export_check_text
from .type_mapping import TypeMapping
from .widgets import batch_table_updates


class ManualCategoryDialog(QDialog):
    def __init__(self, parent, categories, mapping: TypeMapping):
        super().__init__(parent)
        self.setWindowTitle("人工补漏 · 选择原始种类")
        self.resize(520, 240)
        layout = QVBoxLayout(self)
        note = QLabel("从已知类别选择会保留原始 type 及数值/字符串类型。自定义类别默认是字符串；不根据显示名称反推代号。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItem("", None)
        known = set()
        for value in categories:
            if value is None:
                continue
            identity = (type(value), value)
            if identity in known:
                continue
            known.add(identity)
            kind = "字符串" if isinstance(value, str) else "数值"
            label = json.dumps(value, ensure_ascii=False)
            self.category.addItem(f"{mapping.display(value)} · type={label}（{kind}）", value)
        self.value_type = QComboBox()
        self.value_type.addItems(["字符串", "数值"])
        form.addRow("类别 / 自定义 type", self.category)
        form.addRow("自定义值的类型", self.value_type)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName("error")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.value = None

    def accept(self):
        text = self.category.currentText().strip()
        index = self.category.currentIndex()
        if index > 0 and self.category.currentText() == self.category.itemText(index):
            self.value = self.category.itemData(index)
        elif not text:
            self.value = None
        elif self.value_type.currentIndex() == 0:
            self.value = text
        else:
            try:
                value = json.loads(text)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError()
                self.value = value
            except (ValueError, OverflowError):
                self.error.setText("数值 type 需要有限数字，例如 0 或 1；纯数字字符串请选择“字符串”。")
                return
        super().accept()


def choose_manual_category(parent, categories, mapping):
    dialog = ManualCategoryDialog(parent, categories, mapping)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.value, accepted


class ExportCheckDialog(QDialog):
    FIX_SIZES, PARTIAL, REMAP = 2, 3, 4

    def __init__(self, window, export: TargetExport, report_path):
        super().__init__(window)
        self.window, self.export = window, export
        self.setWindowTitle("有效目标 JSON · 导出前检查")
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        title = QLabel(f"候选 {export.candidate_count} 个 · 可导出 {len(export.targets)} 个 · 阻断问题 {len(export.errors)} 项")
        title.setWordWrap(True)
        layout.addWidget(title)
        note = QLabel(f"当前未生成或替换 JSON。列表优先显示错误，最多预览 500 项；完整内容见检查报告或复制详情。\n检查报告：{report_path or '未能保存，可使用“复制检查详情”'}")
        note.setWordWrap(True)
        layout.addWidget(note)
        problems = QTableWidget(0, 4)
        problems.setHorizontalHeaderLabels(["级别", "图片", "目标", "问题与处理建议"])
        problems.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        with batch_table_updates(problems):
            problems.setRowCount(min(500, len(export.issues)))
            for row, issue in enumerate(sorted(export.issues, key=lambda item: item.severity != "error")[:500]):
                for column, value in enumerate(("错误" if issue.severity == "error" else "提示", issue.image,
                                                 issue.target_label or issue.target_id, issue.message)):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    problems.setItem(row, column, item)
        problems.setColumnWidth(0, 55)
        problems.setColumnWidth(1, 190)
        problems.setColumnWidth(2, 100)
        problems.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(problems, 1)
        self.missing = sorted({issue.image for issue in export.errors if issue.code == "dimensions"})
        self.dimension_spins = {}
        self.dimension_table = QTableWidget(len(self.missing), 3)
        self.dimension_table.setHorizontalHeaderLabels(["需要补充尺寸的图片", "图片宽度（像素）", "图片高度（像素）"])
        for row, image in enumerate(self.missing):
            item = QTableWidgetItem(image)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.dimension_table.setItem(row, 0, item)
            spins = []
            for column in (1, 2):
                spin = QSpinBox()
                spin.setRange(0, 2147483647)
                spin.setSpecialValueText("待补充")
                self.dimension_table.setCellWidget(row, column, spin)
                spins.append(spin)
            self.dimension_spins[image] = spins
        self.dimension_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.dimension_table.setMaximumHeight(180)
        self.dimension_table.setVisible(bool(self.missing))
        layout.addWidget(self.dimension_table)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName("error")
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        self.folder_button = QPushButton("从图片文件夹补充尺寸…")
        self.folder_button.setEnabled(bool(self.missing))
        self.folder_button.clicked.connect(self.choose_folder)
        actions.addWidget(self.folder_button)
        batch = QPushButton("批量填写尺寸…")
        batch.setEnabled(bool(self.missing))
        batch.clicked.connect(self.batch_dimensions)
        actions.addWidget(batch)
        copy = QPushButton("复制检查详情")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(export_check_text(export)))
        actions.addWidget(copy)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        remap = buttons.addButton("返回02检查字段映射", QDialogButtonBox.ButtonRole.ActionRole)
        remap.setEnabled(bool(window.sources["algorithm"].path))
        remap.clicked.connect(lambda: self.done(self.REMAP))
        fix = buttons.addButton("应用尺寸并重新检查", QDialogButtonBox.ButtonRole.ActionRole)
        fix.setEnabled(bool(self.missing))
        fix.clicked.connect(self.apply_dimensions)
        partial = buttons.addButton(f"仅导出有效目标（{len(export.targets)}个）", QDialogButtonBox.ButtonRole.ActionRole)
        partial.setEnabled(bool(export.targets))
        partial.clicked.connect(lambda: self.done(self.PARTIAL))
        layout.addWidget(buttons)

    def dimension_overrides(self):
        return {image: tuple(spin.value() for spin in spins) for image, spins in self.dimension_spins.items()
                if all(spin.value() > 0 for spin in spins)}

    def apply_dimensions(self):
        if not self.dimension_overrides():
            self.error.setText("请至少补充一张图片的正整数宽高；不是目标框 w / h。")
            return
        self.done(self.FIX_SIZES)

    def batch_dimensions(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("批量填入图片尺寸")
        form = QFormLayout(dialog)
        width, height = QSpinBox(), QSpinBox()
        for spin in (width, height):
            spin.setRange(1, 2147483647)
        form.addRow("图片宽度（像素）", width)
        form.addRow("图片高度（像素）", height)
        form.addRow(QLabel("应用到选中的图片行；未选择行时应用到列表全部图片。请确认这些图片具有相同尺寸。"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rows = {index.row() for index in self.dimension_table.selectedIndexes()} or set(range(len(self.missing)))
            for row in rows:
                for spin, value in zip(self.dimension_spins[self.missing[row]], (width.value(), height.value())):
                    spin.setValue(value)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择用于补充图片尺寸的文件夹")
        if not folder:
            return
        def read():
            index = {}
            for path in scan_images(Path(folder), IMAGE_EXTENSIONS, recursive=True):
                index.setdefault(path.name.casefold(), []).append(path)
            result = {}
            for image in self.missing:
                path = Path(folder) / image
                if not path.is_file():
                    matches = index.get(Path(image).name.casefold(), [])
                    if len(matches) != 1:
                        continue
                    path = matches[0]
                try:
                    result[image] = self.window._image_dimensions(path)
                except (OSError, ValueError):
                    pass
            return result
        def done(sizes):
            self.setEnabled(True)
            for image, values in sizes.items():
                for spin, value in zip(self.dimension_spins[image], values):
                    spin.setValue(value)
            self.error.setText(f"已自动取得 {len(sizes)} 张图片尺寸；其余请检查图片、宽高映射或手动填写。")
        self.setEnabled(False)
        self.window._run("正在补充导出所需的图片尺寸…", read, done, lambda: self.setEnabled(True))

    def reject(self):
        if not self.window.busy:
            super().reject()
