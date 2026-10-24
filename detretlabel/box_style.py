"""Independent, persistent drawing styles and their configuration dialog."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QVBoxLayout, QWidget,
)


LINE_STYLES = {
    "solid": ("实线", Qt.PenStyle.SolidLine),
    "dash": ("虚线", Qt.PenStyle.DashLine),
    "dot": ("点线", Qt.PenStyle.DotLine),
    "dash_dot": ("点划线", Qt.PenStyle.DashDotLine),
    "dash_dot_dot": ("双点划线", Qt.PenStyle.DashDotDotLine),
}

LABEL_FIELDS = {
    "show_category": "种类（type）",
    "show_score": "置信度（score）",
    "show_index": "目标序号",
    "show_source": "来源名称（算法 / GT）",
}


def label_foreground(color: QColor) -> QColor:
    brightness = .299 * color.red() + .587 * color.green() + .114 * color.blue()
    return QColor("#172337" if brightness >= 145 else "#ffffff")


def label_font() -> QFont:
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(12)
    return font


@dataclass(frozen=True)
class BoxStyle:
    color: str
    line_style: str = "solid"
    show_category: bool = True
    show_score: bool = True
    show_index: bool = False
    show_source: bool = False

    def label_text(self, category, score: float | None, index: int, source: str) -> str:
        parts = []
        if self.show_source:
            parts.append(source)
        if self.show_index:
            parts.append(f"#{index}")
        if self.show_category and category is not None and str(category).strip():
            parts.append(str(category))
        if self.show_score and score is not None:
            parts.append(f"{score:g}")
        return " · ".join(parts)

    def pen(self) -> QPen:
        pen = QPen(QColor(self.color), 2.0, LINE_STYLES[self.line_style][1])
        pen.setCosmetic(True)  # Keep the outline readable at any image zoom level.
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        return pen

    def icon(self) -> QIcon:
        pixmap = QPixmap(40, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setPen(self.pen())
        painter.drawLine(2, 8, 38, 8)
        painter.end()
        return QIcon(pixmap)

    @classmethod
    def from_preferences(cls, color, line_style, fallback: BoxStyle, labels: dict | None = None) -> BoxStyle:
        candidate = QColor(color) if isinstance(color, str) else QColor()
        def boolean(value, default):
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
                return value.lower() in ("true", "1")
            return default
        options = {name: boolean((labels or {}).get(name), getattr(fallback, name)) for name in LABEL_FIELDS}
        return cls(candidate.name() if candidate.isValid() else fallback.color,
                   line_style if isinstance(line_style, str) and line_style in LINE_STYLES else fallback.line_style,
                   **options)


DEFAULT_BOX_STYLES = {
    "algorithm": BoxStyle("#ff0000", "solid"),
    "ground_truth": BoxStyle("#00ff00", "dash"),
}


def mismatch_color(algorithm: str, ground_truth: str) -> str:
    """Prefer yellow, falling back to a distinct color when source styles are similar."""
    colors = [QColor(algorithm), QColor(ground_truth)]
    palette = ("#ffff00", "#ff00ff", "#00ffff", "#ff8c00", "#6a5acd", "#ffffff")
    def distance(value):
        candidate = QColor(value)
        return min(sum((a - b) ** 2 for a, b in zip(candidate.getRgb()[:3], color.getRgb()[:3])) for color in colors)
    return next((value for value in palette if distance(value) >= 120 ** 2), max(palette, key=distance))


class BoxStylePreview(QWidget):
    def __init__(self, style: BoxStyle, title: str = "算法结果"):
        super().__init__()
        self.box_style = style
        self.title = title
        self.setMinimumSize(340, 130)

    def set_box_style(self, style: BoxStyle) -> None:
        self.box_style = style
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        half = self.width() / 2
        painter.fillRect(QRectF(0, 0, half, self.height()), QColor("#eff3f8"))
        painter.fillRect(QRectF(half, 0, half, self.height()), QColor("#172337"))
        painter.setPen(self.box_style.pen())
        for x in (0, half):
            painter.drawRect(QRectF(x + 20, 36, half - 40, self.height() - 54))
        text = self.box_style.label_text("car", .95, 1, self.title)
        if text:
            font = label_font()
            painter.setFont(font)
            metrics = QFontMetricsF(font)
            text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(half - 44))
            for x in (0, half):
                rect = QRectF(x + 20, 12, metrics.horizontalAdvance(text) + 10, metrics.height() + 6)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self.box_style.color))
                painter.drawRoundedRect(rect, 3, 3)
                painter.setPen(label_foreground(QColor(self.box_style.color)))
                painter.drawText(rect.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignVCenter, text)
        painter.end()


class BoxStyleDialog(QDialog):
    def __init__(self, title: str, current: BoxStyle, default: BoxStyle, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} · 框与文字显示")
        self.setMinimumWidth(430)
        self.default_style = default
        self.color = QColor(current.color)
        layout = QVBoxLayout(self)
        heading = QLabel(f"{title}的叠加显示设置")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        form = QFormLayout()
        self.line_combo = QComboBox()
        for key, (label, _) in LINE_STYLES.items():
            self.line_combo.addItem(BoxStyle("#53677f", key).icon(), label, key)
        self.line_combo.setCurrentIndex(self.line_combo.findData(current.line_style))
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self.choose_color)
        form.addRow("线型", self.line_combo)
        form.addRow("颜色", self.color_button)
        layout.addLayout(form)
        labels_group = QGroupBox("图片上显示的信息")
        labels_layout = QGridLayout(labels_group)
        self.label_checks: dict[str, QCheckBox] = {}
        for index, (name, text) in enumerate(LABEL_FIELDS.items()):
            check = QCheckBox(text)
            check.setChecked(getattr(current, name))
            self.label_checks[name] = check
            labels_layout.addWidget(check, index // 2, index % 2)
        layout.addWidget(labels_group)
        label_note = QLabel("未映射的字段自动略过；全部取消时只显示目标框。")
        label_note.setObjectName("muted")
        layout.addWidget(label_note)
        layout.addWidget(QLabel("样式预览（浅色 / 深色背景）"))
        self.preview = BoxStylePreview(current, title)
        layout.addWidget(self.preview)
        note = QLabel("确认后应用于这份 JSON 的全部目标框，并记住设置。")
        note.setObjectName("muted")
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                       QDialogButtonBox.StandardButton.Cancel |
                                       QDialogButtonBox.StandardButton.RestoreDefaults)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText("恢复默认")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.restore_default)
        layout.addWidget(self.buttons)
        self.line_combo.currentIndexChanged.connect(self._refresh_preview)
        for check in self.label_checks.values():
            check.toggled.connect(self._refresh_preview)
        self._refresh_preview()

    def selected_style(self) -> BoxStyle:
        return BoxStyle(self.color.name(), self.line_combo.currentData(),
                        **{name: check.isChecked() for name, check in self.label_checks.items()})

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.color, self, "选择目标框颜色")
        if color.isValid():
            self.color = color
            self._refresh_preview()

    def restore_default(self) -> None:
        self.color = QColor(self.default_style.color)
        self.line_combo.setCurrentIndex(self.line_combo.findData(self.default_style.line_style))
        for name, check in self.label_checks.items():
            check.setChecked(getattr(self.default_style, name))
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        style = self.selected_style()
        self.color_button.setText(f"{self.color.name().upper()}    选择颜色…")
        swatch = QPixmap(18, 18)
        swatch.fill(self.color)
        self.color_button.setIcon(QIcon(swatch))
        self.preview.set_box_style(style)
