from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget,
)

from .core import (
    Collection, FieldMapping, discover_collections, fields, guess_mapping,
    read_pointer, sample_records, sample_targets, target_list_fields,
)


class MappingDialog(QDialog):
    """Each import is explicitly mapped before it can change the workspace."""

    def __init__(self, document: Any, source_name: str, parent: QWidget | None = None,
                 initial: FieldMapping | None = None, source_kind: str = "算法结果"):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle(f"确认 JSON 字段映射 · {source_kind}")
        self.resize(820, 780)
        self.setMinimumSize(660, 640)
        self.collections = discover_collections(document)
        if initial and initial.collection not in self.collections:
            self.collections.insert(0, initial.collection)
        if not self.collections:
            raise ValueError("没有找到可导入的记录。支持检测记录数组、包含图片名和目标数组的记录，或以图片名为键的字典。")

        layout = QVBoxLayout(self)
        title = QLabel(f"将 JSON 字段链接到{source_kind}")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        source = QLabel(source_name)
        source.setWordWrap(True)
        layout.addWidget(source)
        note = QLabel("自动识别仅作为初始建议，请核对框格式。图片按最后一段文件名（含扩展名）匹配，忽略大小写。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

        record_box = QGroupBox("1  选择图片记录与目标列表")
        form = QFormLayout(record_box)
        self.collection_combo = QComboBox()
        for collection in self.collections:
            self.collection_combo.addItem(collection.label, collection)
        self.image_combo = QComboBox()
        self.targets_combo = QComboBox()
        form.addRow("记录所在位置", self.collection_combo)
        form.addRow("图片名 / 图片路径", self.image_combo)
        form.addRow("每张图的目标列表", self.targets_combo)
        layout.addWidget(record_box)

        target_box = QGroupBox("2  选择目标属性")
        target_form = QFormLayout(target_box)
        self.box_mode = QComboBox()
        self.box_mode.addItems(["一个数组字段，例如 bbox: [10, 20, 80, 90]", "四个独立字段，例如 xmin / ymin / xmax / ymax"])
        self.format_combo = QComboBox()
        self.format_combo.addItem("xyxy · 左上角 x、y，右下角 x、y", "xyxy")
        self.format_combo.addItem("xywh · 左上角 x、y，宽、高", "xywh")
        self.box_combo = QComboBox()
        self.coordinate_combos = [QComboBox() for _ in range(4)]
        self.coordinate_labels = [QLabel() for _ in range(4)]
        self.box_stack = QStackedWidget()
        array_widget = QWidget()
        array_form = QFormLayout(array_widget)
        array_form.setContentsMargins(0, 0, 0, 0)
        array_form.addRow("框数组字段", self.box_combo)
        coords_widget = QWidget()
        coords_form = QFormLayout(coords_widget)
        coords_form.setContentsMargins(0, 0, 0, 0)
        for label, combo in zip(self.coordinate_labels, self.coordinate_combos):
            coords_form.addRow(label, combo)
        self.box_stack.addWidget(array_widget)
        self.box_stack.addWidget(coords_widget)
        self.category_combo = QComboBox()
        self.score_combo = QComboBox()
        target_form.addRow("坐标字段结构", self.box_mode)
        target_form.addRow("框坐标含义", self.format_combo)
        target_form.addRow(self.box_stack)
        target_form.addRow("目标种类（可选）", self.category_combo)
        target_form.addRow("置信度（可选）", self.score_combo)
        warning = QLabel("普通 bbox 数组不能可靠推断 xyxy 或 xywh，默认 xyxy，请手动确认。仅支持像素坐标；置信度保留原始数值。")
        warning.setWordWrap(True)
        warning.setObjectName("warning")
        target_form.addRow(warning)
        layout.addWidget(target_box)

        self.sample_label = QLabel()
        layout.addWidget(self.sample_label)
        self.sample = QPlainTextEdit()
        self.sample.setReadOnly(True)
        self.sample.setMinimumHeight(95)
        self.sample.setMaximumHeight(190)
        layout.addWidget(self.sample, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName("error")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认映射并导入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.box_mode.currentIndexChanged.connect(self._box_mode_changed)
        self.format_combo.currentIndexChanged.connect(self._coordinate_labels)
        self.collection_combo.currentIndexChanged.connect(self._collection_changed)
        self.targets_combo.currentIndexChanged.connect(self._targets_changed)
        if initial:
            self.collection_combo.setCurrentIndex(self.collections.index(initial.collection))
        self._collection_changed()
        if initial:
            self._select(self.image_combo, initial.image_field)
            self._select(self.targets_combo, initial.detections_field)
            self._apply_target_mapping(initial)

    @staticmethod
    def _select(combo: QComboBox, data: Any) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    @staticmethod
    def _fill(combo: QComboBox, options: list[str], records: list[Any], placeholder: str = "请选择字段") -> None:
        combo.clear()
        combo.addItem(placeholder, None)
        for pointer in options:
            value = next((read_pointer(r, pointer) for r in records if read_pointer(r, pointer, None) is not None), None)
            if isinstance(value, list):
                example = f"数组 · {len(value)} 项" if value and isinstance(value[0], dict) else str(value)[:70]
            elif isinstance(value, dict):
                example = "对象"
            else:
                example = str(value)[:70]
            combo.addItem(f"{pointer}  →  {example}", pointer)
            combo.setItemData(combo.count() - 1, pointer, Qt.ItemDataRole.ToolTipRole)

    def _collection_changed(self) -> None:
        collection: Collection = self.collection_combo.currentData()
        records = sample_records(self.document, collection)
        options = fields(records)
        guessed = guess_mapping(self.document, collection)
        self._fill(self.image_combo, options, records)
        if collection.kind == "keyed":
            self.image_combo.insertItem(1, "使用字典的键作为图片名", "@key")
        self._select(self.image_combo, guessed.image_field)
        self.targets_combo.blockSignals(True)
        self.targets_combo.clear()
        self.targets_combo.addItem("每条记录本身就是一个检测目标（平铺列表）", None)
        for pointer in target_list_fields(records):
            self.targets_combo.addItem(pointer, pointer)
        self._select(self.targets_combo, guessed.detections_field)
        self.targets_combo.blockSignals(False)
        self.sample_label.setText("原始记录示例（第一条；长数组仅展示前 3 项）")
        def brief(value: Any, depth: int = 0) -> Any:
            if depth > 6:
                return "…"
            if isinstance(value, dict):
                return {k: brief(v, depth + 1) for k, v in list(value.items())[:30]}
            if isinstance(value, list):
                return [brief(v, depth + 1) for v in value[:3]] + (["…"] if len(value) > 3 else [])
            return value
        self.sample.setPlainText(json.dumps(brief(records[0]), ensure_ascii=False, indent=2)[:8000] if records else "空记录列表，可直接导入")
        self._targets_changed()

    def _targets_changed(self) -> None:
        collection = self.collection_combo.currentData()
        pointer = self.targets_combo.currentData()
        records = sample_records(self.document, collection)
        targets = sample_targets(records, pointer)
        options = fields(targets)
        for combo in (self.box_combo, *self.coordinate_combos, self.category_combo, self.score_combo):
            self._fill(combo, options, targets)
        self.category_combo.setItemText(0, "不导入目标种类")
        self.score_combo.setItemText(0, "不导入置信度")
        self._apply_target_mapping(guess_mapping(self.document, collection, pointer))

    def _apply_target_mapping(self, mapping: FieldMapping) -> None:
        self.box_mode.setCurrentIndex(1 if mapping.coordinates else 0)
        self._box_mode_changed()
        self._select(self.format_combo, mapping.box_format)
        self._select(self.box_combo, mapping.box_field)
        for i, combo in enumerate(self.coordinate_combos):
            self._select(combo, mapping.coordinates[i] if i < len(mapping.coordinates) else None)
        self._select(self.category_combo, mapping.category_field)
        self._select(self.score_combo, mapping.score_field)
        self._coordinate_labels()

    def _box_mode_changed(self) -> None:
        self.box_stack.setCurrentIndex(self.box_mode.currentIndex())
        self.box_stack.setFixedHeight(self.box_stack.currentWidget().sizeHint().height())

    def _coordinate_labels(self) -> None:
        names = ("xmin", "ymin", "xmax", "ymax") if self.format_combo.currentData() == "xyxy" else ("x", "y", "width", "height")
        for label, name in zip(self.coordinate_labels, names):
            label.setText(name)

    def mapping(self) -> FieldMapping:
        return FieldMapping(
            self.collection_combo.currentData(), self.image_combo.currentData() or "",
            self.targets_combo.currentData(), self.format_combo.currentData(),
            self.box_combo.currentData() if self.box_mode.currentIndex() == 0 else None,
            tuple(combo.currentData() or "" for combo in self.coordinate_combos) if self.box_mode.currentIndex() else (),
            self.category_combo.currentData(), self.score_combo.currentData(),
        )

    def accept(self) -> None:
        mapping = self.mapping()
        records = sample_records(self.document, mapping.collection)
        targets = sample_targets(records, mapping.detections_field)
        message = ""
        if records and not mapping.image_field:
            message = "请选择图片名 / 路径字段。"
        elif mapping.coordinates and (not all(mapping.coordinates) or len(set(mapping.coordinates)) != 4):
            message = "请选择四个不同的坐标字段。"
        elif targets and not mapping.box_field and not mapping.coordinates:
            message = "请选择框数组字段，或切换为四个独立坐标字段。"
        if message:
            self.error.setText(message)
            return
        self.error.clear()
        super().accept()
