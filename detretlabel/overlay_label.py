"""Readable, constant-size labels anchored to image-space detection boxes."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QGraphicsItem

from .box_style import label_font, label_foreground


class BoxLabelItem(QGraphicsItem):
    def __init__(self, text: str, color: str, parent: QGraphicsItem):
        super().__init__(parent)
        self.full_text = text
        self.display_text = text
        self.color = QColor(color)
        self.font = label_font()
        self.metrics = QFontMetricsF(self.font)
        self._rect = QRectF(0, 0, self.metrics.horizontalAdvance(text) + 10, self.metrics.height() + 6)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(20)

    def set_maximum_width(self, width: float) -> None:
        text = self.metrics.elidedText(self.full_text, Qt.TextElideMode.ElideRight, max(0, int(width - 10)))
        if text != self.display_text:
            self.prepareGeometryChange()
            self.display_text = text
            self._rect.setWidth(self.metrics.horizontalAdvance(text) + 10)

    def boundingRect(self) -> QRectF:
        return QRectF(self._rect)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawRoundedRect(self._rect, 3, 3)
        painter.setFont(self.font)
        painter.setPen(label_foreground(self.color))
        painter.drawText(self._rect.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignVCenter, self.display_text)
