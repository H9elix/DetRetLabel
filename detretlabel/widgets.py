"""Compact data preparation controls shared by both JSON sources."""
from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHeaderView, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)


@contextmanager
def batch_table_updates(table):
    """Defer content-based column sizing until a whole batch is populated."""
    header = table.horizontalHeader()
    automatic = [column for column in range(table.columnCount())
                 if header.sectionResizeMode(column) == QHeaderView.ResizeMode.ResizeToContents]
    updates_enabled = table.updatesEnabled()
    table.setUpdatesEnabled(False)
    for column in automatic:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
    try:
        yield
    finally:
        for column in automatic:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.setUpdatesEnabled(updates_enabled)


class ElidedLabel(QLabel):
    """Keep full paths in tooltips without widening or growing a side panel."""

    def __init__(self, text: str = ""):
        super().__init__()
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self.full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self) -> None:
        single_line = " ".join(self.full_text.splitlines())
        super().setText(self.fontMetrics().elidedText(single_line, Qt.TextElideMode.ElideMiddle,
                                                     max(0, self.contentsRect().width())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()


class CollapsibleSection(QFrame):
    toggled = Signal(bool)

    def __init__(self, title: str, summary: str):
        super().__init__()
        self.setObjectName("section")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QToolButton()
        self.header.setObjectName("sectionHeader")
        self.header.setText(title)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setArrowType(Qt.ArrowType.DownArrow)
        self.header.setToolTip("点击折叠")
        self.header.setAccessibleName(title + "，展开或折叠")
        layout.addWidget(self.header)
        self.summary = ElidedLabel(summary)
        self.summary.setObjectName("sectionSummary")
        self.summary.hide()
        layout.addWidget(self.summary)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10, 2, 10, 10)
        self.body_layout.setSpacing(7)
        layout.addWidget(self.body)
        self.header.toggled.connect(self._toggle)

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)

    def _toggle(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.summary.setVisible(not expanded)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.header.setToolTip("点击折叠" if expanded else "点击展开")
        self.updateGeometry()
        self.toggled.emit(expanded)


class SettingsScrollArea(QScrollArea):
    """Use the controls' natural height and give released space to the image list."""

    def __init__(self, widget: QWidget):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(widget)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:
        return QSize(380, self.widget().sizeHint().height())


class JsonSourcePanel(QFrame):
    def __init__(self, title: str, description: str, shortcut: str):
        super().__init__()
        self.setObjectName("sourcePanel")
        self.setToolTip(description)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(5)
        heading = QHBoxLayout()
        name = QLabel(title)
        name.setObjectName("sourceTitle")
        self.status = QLabel("未导入")
        self.status.setObjectName("muted")
        heading.addWidget(name)
        heading.addStretch()
        heading.addWidget(self.status)
        layout.addLayout(heading)
        self.path = ElidedLabel("尚未导入 JSON")
        self.path.setObjectName("muted")
        layout.addWidget(self.path)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        self.choose = QPushButton("添加 JSON…")
        self.choose.setToolTip(f"{description}\n快捷键：{shortcut}")
        self.remap = QPushButton("字段映射")
        self.box_style = QPushButton("框样式")
        self.box_style.setIconSize(QSize(28, 16))
        self.remove = QPushButton("移除")
        for button in (self.choose, self.remap, self.box_style, self.remove):
            button.setObjectName("sourceAction")
            actions.addWidget(button)
        actions.setStretch(0, 1)
        layout.addLayout(actions)
        mapping_row = QHBoxLayout()
        mapping_row.setSpacing(5)
        self.type_mapping_button = QPushButton("导入 type-mapping…")
        self.type_mapping_clear = QPushButton("清除")
        for button in (self.type_mapping_button, self.type_mapping_clear):
            button.setObjectName("sourceAction")
            mapping_row.addWidget(button)
        mapping_row.setStretch(0, 1)
        layout.addLayout(mapping_row)
        self.type_mapping_status = ElidedLabel("种类显示：原始种类（可选导入映射）")
        self.type_mapping_status.setObjectName("muted")
        layout.addWidget(self.type_mapping_status)
