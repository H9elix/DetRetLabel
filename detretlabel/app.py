from __future__ import annotations

import sys
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QImageReader, QKeySequence, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter, QSizePolicy,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QAbstractItemView,
)

from .core import IMAGE_EXTENSIONS, FieldMapping, ImportResult, import_results, load_json, scan_images
from .mapping_dialog import MappingDialog
from .widgets import CollapsibleSection, ElidedLabel, JsonSourcePanel, SettingsScrollArea
from .box_style import BoxStyle, BoxStyleDialog, DEFAULT_BOX_STYLES, LABEL_FIELDS, LINE_STYLES
from .overlay_label import BoxLabelItem
from .annotation import AnnotationStore, source_signature
from .annotation_panel import AnnotationPanel
from .review_controller import ReviewController
from .type_mapping import TypeMapping, category_text
from .app_info import AUTHOR, BUILD, VERSION
from .help_viewer import HelpDialog

STYLE = """
QMainWindow, QDialog { background: #f2f5f9; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #24344b; }
QGroupBox { background: white; border: 1px solid #dce3ed; border-radius: 8px;
    margin-top: 16px; padding: 16px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 5px; padding: 8px 12px; }
QPushButton:hover { background: #eef4ff; border-color: #4386e8; }
QPushButton:pressed { background: #dfebfc; }
QPushButton:disabled { color: #a6afbd; background: #f0f2f5; }
QPushButton#primary { color: white; background: #2563eb; border-color: #2563eb; }
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#primary:disabled { background: #c6d4ec; border-color: #c6d4ec; }
QLineEdit, QComboBox, QPlainTextEdit { background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 5px; }
QComboBox { min-height: 20px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #dceafe; }
QTableWidget { background: white; alternate-background-color: #f8fafc; border: 1px solid #dce3ed;
    gridline-color: #eef1f5; selection-background-color: #dceafe; selection-color: #153d78; }
QHeaderView::section { background: #eaf0f7; border: 0; border-bottom: 1px solid #dce3ed; padding: 7px; }
QLabel#title { font-size: 25px; font-weight: 700; }
QLabel#dialogTitle { font-size: 20px; font-weight: 700; }
QLabel#muted { color: #69798d; }
QLabel#warning { color: #8b611c; }
QLabel#error { color: #b42318; }
QLabel#summary { background: #e7efff; color: #1f4e97; border-radius: 6px; padding: 10px; }
QCheckBox { spacing: 6px; }
QProgressBar { border: 0; background: #dfe8f6; max-height: 3px; }
QProgressBar::chunk { background: #2563eb; }
QFrame#section { background: white; border: 1px solid #dce3ed; border-radius: 7px; }
QToolButton#sectionHeader { border: 0; background: transparent; text-align: left;
    padding: 9px 10px; font-weight: 600; }
QToolButton#sectionHeader:hover { background: #eef4ff; }
QLabel#sectionSummary { color: #69798d; padding: 0 10px 8px; }
QFrame#sourcePanel { background: #f8fafc; border: 1px solid #e1e7ef; border-radius: 5px; }
QLabel#sourceTitle { font-weight: 600; }
QPushButton#sourceAction { padding: 5px 8px; }
QTabWidget::pane { border: 1px solid #dce3ed; background: white; }
QTabBar::tab { background: #eaf0f7; padding: 7px 16px; }
QTabBar::tab:selected { background: white; color: #1d4ed8; }
"""


@dataclass
class ResultSource:
    title: str
    document: Any = None
    path: Path | None = None
    mapping: FieldMapping | None = None
    result: ImportResult | None = None
    signature: str = ""


class TaskSignals(QObject):
    completed = Signal(object, str)


class Task(QRunnable):
    def __init__(self, function: Callable):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            value = self.function()
        except Exception as exc:
            self.signals.completed.emit(None, f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(value, "")


class ImageView(QGraphicsView):
    """Image preview with separate, cosmetic outlines for each JSON source."""

    box_drawn = Signal(object)
    target_clicked = Signal(str, int)
    draw_mode_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setBackgroundBrush(QColor("#172337"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(300, 220)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.auto_fit = True
        self.path: Path | None = None
        self.dimensions: tuple[int, int] | None = None
        self.overlay_items: dict[str, list[QGraphicsRectItem]] = {}
        self.label_items: dict[str, list[BoxLabelItem]] = {}
        self.label_anchors: list[tuple[BoxLabelItem, QRectF, str]] = []
        self.overlay_root: QGraphicsRectItem | None = None
        self._base_pens: dict[str, QPen] = {}
        self.draw_mode = False
        self._draw_start: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None
        self._press_position = None
        self.allowed_roles = {"algorithm", "ground_truth", "manual"}
        self.active_role: str | None = None
        self.active_index = 0
        self.show_all_targets = False
        self.show_noncurrent_labels = False
        self.retained_algorithm_indices: set[int] = set()
        self.show_ground_truth = False
        self.selected_gt_index = 0
        self.selected_manual_index = 0
        self.same_gt_index = 0
        self.mismatch_gt_index = 0
        self.mismatch_gt_color = "#ffff00"
        self.message("选择图片文件夹，开始准备测试集")

    def message(self, message: str) -> None:
        self.retained_algorithm_indices.clear()
        self.selected_gt_index = self.same_gt_index = self.mismatch_gt_index = 0
        self.selected_manual_index = 0
        self.set_draw_mode(False)
        self.overlay_items.clear()
        self.label_items.clear()
        self.label_anchors.clear()
        self.overlay_root = None
        self.scene.clear()
        text = self.scene.addText(message)
        text.setDefaultTextColor(QColor("#c5d1e3"))
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-25, -25, 25, 25))
        self.resetTransform()
        self.path = None
        self.dimensions = None

    def show_image(self, path: Path) -> str | None:
        self.retained_algorithm_indices.clear()
        self.selected_gt_index = self.same_gt_index = self.mismatch_gt_index = 0
        self.selected_manual_index = 0
        self.set_draw_mode(False)
        self.overlay_items.clear()
        self.label_items.clear()
        self.label_anchors.clear()
        self.overlay_root = None
        self.scene.clear()
        self.path = path
        # Explicitly leave EXIF rotation off: JSON boxes normally address raw pixel coordinates.
        reader = QImageReader(str(path))
        reader.setAutoTransform(False)
        image = reader.read()
        if image.isNull():
            error = reader.errorString()
            self.message("图片无法解码；请检查格式或文件是否损坏")
            return error
        self.dimensions = (image.width(), image.height())
        self.scene.addPixmap(QPixmap.fromImage(image))
        self.scene.setSceneRect(0, 0, image.width(), image.height())
        self.fit_image()
        return None

    def set_overlays(self, sources: dict, type_mappings: dict[str, TypeMapping] | None = None) -> None:
        if self.path is None or self.dimensions is None:
            return
        self.overlay_items.clear()
        self.label_items.clear()
        self.label_anchors.clear()
        if self.overlay_root is not None:
            self.scene.removeItem(self.overlay_root)
        width, height = self.dimensions
        # Clip drawing to the image without changing stored box coordinates.
        self.overlay_root = QGraphicsRectItem(0, 0, width, height)
        self.overlay_root.setPen(QPen(Qt.PenStyle.NoPen))
        self.overlay_root.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.overlay_root.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.overlay_root.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.overlay_root.setZValue(1)
        self.scene.addItem(self.overlay_root)
        self._base_pens = {role: style.pen() for role, (targets, style, title) in sources.items()}
        for role, (targets, style, title) in sources.items():
            items = []
            labels = []
            for index, target in enumerate(targets, 1):
                category = (type_mappings or {}).get(role, TypeMapping()).display(target.category)
                x1, y1, x2, y2 = target.box
                item = QGraphicsRectItem(QRectF(x1, y1, x2 - x1, y2 - y1), self.overlay_root)
                item.setPen(style.pen())
                item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                item.setData(0, role)
                item.setData(1, index)
                score = "无置信度" if target.score is None else f"置信度 {target.score:g}"
                raw = category_text(target.category)
                description = category if category is not None else "未指定种类"
                if category is not None and category != raw:
                    description += f"（原始种类：{raw}）"
                item.setToolTip(f"{title} · 目标 {index}\n{description} · {score}")
                items.append(item)
                text = style.label_text(category, target.score, index, title)
                visible_box = item.rect().intersected(QRectF(0, 0, width, height))
                if text and not visible_box.isEmpty():
                    label = BoxLabelItem(text, style.color, self.overlay_root)
                    label.setData(0, role)
                    label.setData(1, index)
                    label.setToolTip(item.toolTip())
                    labels.append(label)
                    self.label_anchors.append((label, visible_box, role))
            self.overlay_items[role] = items
            self.label_items[role] = labels
        self.set_current_target(self.active_role, self.active_index, show_all=self.show_all_targets,
                                show_noncurrent_labels=self.show_noncurrent_labels,
                                retained_algorithm_indices=self.retained_algorithm_indices,
                                show_ground_truth=self.show_ground_truth)

    def set_current_target(self, role: str | None, index: int, *, show_all: bool = False,
                           show_noncurrent_labels: bool = False, show_ground_truth: bool = False,
                           retained_algorithm_indices: set[int] | None = None) -> None:
        self.active_role = role
        self.active_index = index
        self.show_all_targets = show_all
        self.show_noncurrent_labels = show_all and show_noncurrent_labels
        self.retained_algorithm_indices = set(retained_algorithm_indices or ())
        self.show_ground_truth = show_ground_truth
        for source_role, items in self.overlay_items.items():
            for number, item in enumerate(items, 1):
                selected = source_role == role and number == index
                item.setVisible(self._target_visible(source_role, number))
                pen = QPen(self._base_pens[source_role])
                reference_selected = ((source_role == "ground_truth" and number == self.selected_gt_index) or
                                      (source_role == "manual" and number == self.selected_manual_index))
                if source_role == "ground_truth" and number == self.mismatch_gt_index:
                    pen.setColor(QColor(self.mismatch_gt_color))
                pen.setWidthF(4 if selected else 3 if reference_selected else 2)
                item.setPen(pen)
                color = QColor(pen.color())
                color.setAlpha(35 if selected else 0)
                item.setBrush(QBrush(color) if selected else QBrush(Qt.BrushStyle.NoBrush))
                reference = reference_selected or (source_role == "ground_truth" and number in (self.same_gt_index, self.mismatch_gt_index))
                item.setZValue(3 if reference else 2 if selected else 0)
        for label in self.label_items.get("ground_truth", []):
            color = (QColor(self.mismatch_gt_color) if label.data(1) == self.mismatch_gt_index
                     else self._base_pens["ground_truth"].color())
            if label.color != color:
                label.color = QColor(color)
                label.update()
        self._layout_overlay_labels()

    def set_gt_references(self, selected: int = 0, same: int = 0, mismatch: int = 0,
                          color: str = "#ffff00", manual: int = 0) -> None:
        self.selected_gt_index, self.same_gt_index, self.mismatch_gt_index = selected, same, mismatch
        self.selected_manual_index = manual
        self.mismatch_gt_color = color
        self.set_current_target(self.active_role, self.active_index, show_all=self.show_all_targets,
            show_noncurrent_labels=self.show_noncurrent_labels, show_ground_truth=self.show_ground_truth,
            retained_algorithm_indices=self.retained_algorithm_indices)

    def _target_visible(self, role: str, index: int, *, label: bool = False) -> bool:
        if role == "ground_truth":
            return self.show_ground_truth or index in (self.selected_gt_index, self.same_gt_index, self.mismatch_gt_index)
        if role == "algorithm":
            selected = role == self.active_role and index == self.active_index
            return (selected or index in self.retained_algorithm_indices or
                    (self.show_all_targets and (not label or self.show_noncurrent_labels)))
        return role == "manual"

    def set_draw_mode(self, enabled: bool) -> None:
        enabled = enabled and self.path is not None
        if self._rubber is not None:
            self.scene.removeItem(self._rubber)
            self._rubber = None
        self._draw_start = None
        self.draw_mode = enabled
        self.setDragMode(QGraphicsView.DragMode.NoDrag if enabled else QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.OpenHandCursor)
        self.draw_mode_changed.emit(enabled)

    def _image_point(self, position) -> QPointF:
        point = self.mapToScene(position.toPoint())
        width, height = self.dimensions or (0, 0)
        return QPointF(max(0, min(width, point.x())), max(0, min(height, point.y())))

    def mousePressEvent(self, event) -> None:
        self._press_position = event.position()
        if self.draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self._draw_start = self._image_point(event.position())
            pen = QPen(QColor("#b77aff"), 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._rubber = self.scene.addRect(QRectF(self._draw_start, self._draw_start), pen)
            self._rubber.setZValue(100)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.draw_mode and self._draw_start is not None and self._rubber is not None:
            self._rubber.setRect(QRectF(self._draw_start, self._image_point(event.position())).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.draw_mode and event.button() == Qt.MouseButton.LeftButton and self._draw_start is not None:
            rect = QRectF(self._draw_start, self._image_point(event.position())).normalized()
            if rect.width() >= 1 and rect.height() >= 1:
                box = (rect.left(), rect.top(), rect.right(), rect.bottom())
                self.set_draw_mode(False)
                self.box_drawn.emit(box)
            else:
                self.set_draw_mode(True)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._press_position is not None and (
                event.position() - self._press_position).manhattanLength() < 5:
            candidates = [item for item in self.items(event.position().toPoint())
                          if item.data(0) in self.allowed_roles and isinstance(item.data(1), int)]
            candidates.sort(key=lambda item: (not isinstance(item, BoxLabelItem), item.data(0) != self.active_role))
            if candidates:
                self.target_clicked.emit(candidates[0].data(0), candidates[0].data(1))

    def _layout_overlay_labels(self) -> None:
        if self.dimensions is None:
            return
        width, height = self.dimensions
        scale = max(abs(self.transform().m11()), .0001)
        margin = 2 / scale
        occupied: list[QRectF] = []
        for label, box, role in self.label_anchors:
            label.set_maximum_width(min(420, width * scale - 4))
            bounds = label.boundingRect()
            visible = self._target_visible(role, label.data(1), label=True)
            label.setVisible(visible and width * scale >= 24 and height * scale >= bounds.height() + 4)
            label_width, label_height = bounds.width() / scale, bounds.height() / scale
            x = min(max(box.left(), margin), max(margin, width - label_width - margin))
            # Keep coincident algorithm and GT labels on opposite edges of their boxes.
            if role == "ground_truth":
                y = box.bottom() + margin
                if y + label_height > height:
                    y = box.bottom() - label_height - margin
            else:
                y = box.top() - label_height - margin
                if y < 0:
                    y = box.top() + margin
            y = min(max(y, margin), max(margin, height - label_height - margin))
            preferred = QRectF(x, y, label_width, label_height)
            position = preferred
            if label.isVisible():
                step = label_height + margin * 2
                direction = 1 if role == "ground_truth" else -1
                offsets = [(0, 0)]
                for distance in range(1, 9):
                    offsets.extend(((0, direction * distance * step), (0, -direction * distance * step),
                                    (-distance * label_width / 2, 0), (distance * label_width / 2, 0)))
                for dx, dy in offsets:
                    candidate_x = min(max(x + dx, margin), max(margin, width - label_width - margin))
                    candidate_y = min(max(y + dy, margin), max(margin, height - label_height - margin))
                    candidate = QRectF(candidate_x, candidate_y, label_width, label_height)
                    if not any(rect.adjusted(-margin, -margin, margin, margin).intersects(candidate) for rect in occupied):
                        position = candidate
                        break
                occupied.append(position)
            label.setPos(position.topLeft())

    def fit_image(self) -> None:
        self.auto_fit = True
        if self.scene.items():
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._layout_overlay_labels()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.auto_fit:
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._layout_overlay_labels()

    def wheelEvent(self, event) -> None:
        if self.path:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            scale = self.transform().m11() * factor
            if 0.005 < scale < 40:
                self.auto_fit = False
                self.scale(factor, factor)
                self._layout_overlay_labels()
            event.accept()
        else:
            super().wheelEvent(event)


def make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(30)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.setWindowTitle("DetRetLabel · 检测结果标注工具")
        self.resize(1420, 900)
        self.setMinimumSize(1040, 720)
        self.settings = settings if settings is not None else QSettings("DetRetLabel", "DetRetLabel")
        self.folder: Path | None = None
        self.images: list[Path] = []
        self.sources = {"algorithm": ResultSource("算法结果"), "ground_truth": ResultSource("Ground Truth")}
        self.box_styles = dict(DEFAULT_BOX_STYLES)
        self.type_mappings = {role: TypeMapping() for role in self.sources}
        self.busy = False
        self.pool = QThreadPool(self)
        self._task: Task | None = None
        self._callback: Callable | None = None
        self._error_callback: Callable | None = None
        self._pending_scan = False
        self._active_extensions = set(IMAGE_EXTENSIONS)
        self._active_recursive = False
        self._help_dialog: HelpDialog | None = None
        self._build_ui()
        self._build_help_menu()
        self.review = ReviewController(self)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(lambda: self.scan_folder(self.folder) if self.folder else None)
        self._restore_preferences()
        self._shortcuts()
        self._update_controls()

    def _build_help_menu(self) -> None:
        self.help_menu = self.menuBar().addMenu("帮助(&H)")
        for text in (f"作者：{AUTHOR}", f"打包：{BUILD}", f"版本：{VERSION}"):
            self.help_menu.addAction(text).setEnabled(False)
        self.help_menu.addSeparator()
        self.readme_action = self.help_menu.addAction("预览 README.md…")
        self.readme_action.triggered.connect(lambda: self.show_help(0))
        self.guide_action = self.help_menu.addAction("详细使用指南…")
        self.guide_action.triggered.connect(lambda: self.show_help(1))

    def show_help(self, page: int = 0) -> None:
        if self._help_dialog is None:
            self._help_dialog = HelpDialog(self)
        self._help_dialog.show_page(page)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(20, 16, 20, 12)
        heading = QHBoxLayout()
        title = QLabel("DetRetLabel")
        title.setObjectName("title")
        heading.addWidget(title)
        subtitle = QLabel("检测结果标注工具  /  图片核对与人工标注")
        subtitle.setObjectName("muted")
        heading.addWidget(subtitle)
        heading.addStretch()
        outer.addLayout(heading)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer.addWidget(self.progress)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        left = QWidget()
        left.setMinimumWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(8)
        self.folder_section = CollapsibleSection("01  图片文件夹", "尚未选择文件夹")
        folder_layout = self.folder_section.body_layout
        self.folder_label = ElidedLabel("尚未选择文件夹")
        folder_layout.addWidget(self.folder_label)
        self.folder_button = QPushButton("选择图片文件夹…   Ctrl+O")
        self.folder_button.setObjectName("primary")
        self.folder_button.clicked.connect(self.choose_folder)
        folder_layout.addWidget(self.folder_button)
        self.formats: dict[str, QCheckBox] = {}
        for extension in IMAGE_EXTENSIONS:
            check = QCheckBox(extension[1:].upper())
            check.setChecked(True)
            check.toggled.connect(self._schedule_scan)
            self.formats[extension] = check
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for i, check in enumerate(self.formats.values()):
            (row1 if i < 4 else row2).addWidget(check)
        folder_layout.addLayout(row1)
        folder_layout.addLayout(row2)
        self.recursive = QCheckBox("包含子文件夹（重名时提示歧义）")
        self.recursive.toggled.connect(self._schedule_scan)
        folder_layout.addWidget(self.recursive)
        settings_layout.addWidget(self.folder_section)

        self.data_section = CollapsibleSection("02  检测与参考数据", "算法未导入 · GT 未导入")
        self.source_panels: dict[str, JsonSourcePanel] = {}
        for role, title, description, shortcut in (
            ("algorithm", "算法检测结果", "导入算法输出的检测结果 JSON", "Ctrl+J"),
            ("ground_truth", "参考标注 Ground Truth", "可选：导入 GT 供人工核对；支持无置信度标注", "Ctrl+Shift+J"),
        ):
            panel = JsonSourcePanel(title, description, shortcut)
            panel.choose.clicked.connect(lambda checked=False, r=role: self.choose_json(r))
            panel.remap.clicked.connect(lambda checked=False, r=role: self.remap_json(r))
            panel.remove.clicked.connect(lambda checked=False, r=role: self.clear_json(r))
            panel.box_style.clicked.connect(lambda checked=False, r=role: self.configure_box_style(r))
            panel.type_mapping_button.clicked.connect(lambda checked=False, r=role: self.choose_type_mapping(r))
            panel.type_mapping_clear.clicked.connect(lambda checked=False, r=role: self.set_type_mapping(r, TypeMapping()))
            self.source_panels[role] = panel
            self.data_section.body_layout.addWidget(panel)
        settings_layout.addWidget(self.data_section)
        self.settings_scroll = SettingsScrollArea(settings_widget)
        for section in (self.folder_section, self.data_section):
            section.toggled.connect(lambda expanded: self.settings_scroll.updateGeometry())
        left_layout.addWidget(self.settings_scroll)

        self.summary = QLabel("尚未选择图片")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("summary")
        left_layout.addWidget(self.summary)
        self.report_button = QPushButton("查看导入问题明细")
        self.report_button.clicked.connect(self.show_report)
        left_layout.addWidget(self.report_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索图片文件名或相对路径")
        self.search.textChanged.connect(self._filter_table)
        left_layout.addWidget(self.search)
        self.image_table = make_table(["图片", "算法", "GT", "标注"])
        self.image_table.setMinimumHeight(140)
        self.image_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.image_table.horizontalHeader().setStretchLastSection(False)
        self.image_table.currentCellChanged.connect(self._selection_changed)
        left_layout.addWidget(self.image_table, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 4, 0, 0)
        image_header = QHBoxLayout()
        self.image_title = QLabel("图片预览")
        self.image_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        image_header.addWidget(self.image_title, 1)
        self.previous_button = QPushButton("上一张  ←")
        self.previous_button.clicked.connect(lambda: self.move_image(-1))
        self.next_button = QPushButton("下一张  →")
        self.next_button.clicked.connect(lambda: self.move_image(1))
        fit = QPushButton("适应窗口  F")
        self.previous_button.setAutoRepeat(True)
        self.next_button.setAutoRepeat(True)
        image_header.addWidget(self.previous_button)
        image_header.addWidget(self.next_button)
        image_header.addWidget(fit)
        right_layout.addLayout(image_header)
        vertical = QSplitter(Qt.Orientation.Vertical)
        self.image_view = ImageView()
        fit.clicked.connect(self.image_view.fit_image)
        vertical.addWidget(self.image_view)
        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 3, 0, 0)
        result_layout.setSpacing(3)
        target_header = QHBoxLayout()
        self.target_title = ElidedLabel("当前图片的检测结果 · 统一显示为像素 xyxy")
        target_header.addWidget(self.target_title, 1)
        self.auto_gt_overlay = QCheckBox("叠加匹配 GT")
        self.auto_gt_overlay.setToolTip("仅作参考：显示同类且 IoU 最大的 GT；整体 IoU 最大者若异类，再用独立颜色显示。只提示 IoU > 0 的框。手动点选 GT 不受此开关影响。")
        target_header.addWidget(self.auto_gt_overlay)
        result_layout.addLayout(target_header)
        self.target_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.target_splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.target_tabs = QTabWidget()
        self.gt_tabs = QTabWidget()
        self.target_tables: dict[str, QTableWidget] = {}
        for role, title in (("algorithm", "算法结果"), ("ground_truth", "Ground Truth"), ("manual", "人工补框")):
            table = make_table(["目标", "种类", "置信度", "xmin", "ymin", "xmax", "ymax", "标注", "原因"])
            self.target_tables[role] = table
            table.setMinimumWidth(0)
            host = self.target_tabs if role == "algorithm" else self.gt_tabs
            host.addTab(table, title + " (0)")
        self.target_tables["manual"].setToolTip("单击只叠加查看人工补框，保留当前审核目标；双击进入漏检复核编辑该框的原因和备注。")
        for role in ("algorithm", "ground_truth"):
            self.target_tables[role].setToolTip("按置信度从高到低显示，同分保持原始顺序；未导入置信度时保持原始顺序。目标序号与图片中的框一致，可悬停查看原始 JSON 位置。")
        self.target_table = self.target_tables["algorithm"]
        self.target_tabs.currentChanged.connect(self._update_target_title)
        self.gt_tabs.currentChanged.connect(self._reference_tab_changed)
        self.target_splitter.addWidget(self.target_tabs)
        self.target_splitter.addWidget(self.gt_tabs)
        self.target_splitter.setSizes([330, 330])
        self.target_splitter.setChildrenCollapsible(False)
        self.target_splitter.setStretchFactor(0, 1)
        self.target_splitter.setStretchFactor(1, 1)
        result_layout.addWidget(self.target_splitter, 1)
        self.gt_match_status = ElidedLabel("GT 匹配提示仅供参考，不参与人工判定。")
        self.gt_match_status.setObjectName("muted")
        self.gt_match_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        result_layout.addWidget(self.gt_match_status)
        vertical.addWidget(result_widget)
        vertical.setSizes([500, 230])
        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.addWidget(vertical)
        self.annotation_panel = AnnotationPanel()
        workspace.addWidget(self.annotation_panel)
        workspace.setStretchFactor(0, 1)
        workspace.setSizes([650, 310])
        right_layout.addWidget(workspace, 1)
        hint = QLabel("滚轮缩放 · 拖动平移 · F 适应窗口 ｜ 左侧“框样式”可分别设置算法 / GT 的线型、颜色和文字信息。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        right_layout.addWidget(hint)
        splitter.addWidget(right)
        splitter.setSizes([400, 970])
        splitter.setStretchFactor(1, 1)
        self.statusBar().showMessage("就绪 · 先选择测试集图片文件夹")

    def _shortcuts(self) -> None:
        for shortcut, callback in (("Ctrl+O", self.choose_folder), ("Ctrl+J", lambda: self.choose_json("algorithm")),
                                   ("Ctrl+Shift+J", lambda: self.choose_json("ground_truth")),
                                   ("F", self.image_view.fit_image)):
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.addAction(action)

    def _restore_preferences(self) -> None:
        extensions = self.settings.value("extensions", list(IMAGE_EXTENSIONS))
        if isinstance(extensions, str):
            extensions = [extensions]
        self._active_extensions = set(extensions)
        self._active_recursive = self.settings.value("recursive", False, type=bool)
        self._restore_filter_controls()
        self.folder_section.set_expanded(self.settings.value("sections/folder_expanded", True, type=bool))
        self.data_section.set_expanded(self.settings.value("sections/data_expanded", True, type=bool))
        for role, default in DEFAULT_BOX_STYLES.items():
            self.box_styles[role] = BoxStyle.from_preferences(
                self.settings.value(f"box_styles/{role}/color", default.color),
                self.settings.value(f"box_styles/{role}/line_style", default.line_style), default,
                {name: self.settings.value(f"box_styles/{role}/{name}", getattr(default, name)) for name in LABEL_FIELDS})
            try:
                self.type_mappings[role] = TypeMapping.from_preferences(
                    self.settings.value(f"type_mappings/{role}/entries", "{}"),
                    self.settings.value(f"type_mappings/{role}/path", ""))
            except (ValueError, TypeError):
                self.type_mappings[role] = TypeMapping()
        self._refresh_style_controls()
        self._refresh_type_mapping_controls()
        self.auto_gt_overlay.setChecked(self.settings.value("review/auto_gt_overlay", False, type=bool))

    def _restore_filter_controls(self) -> None:
        for extension, check in self.formats.items():
            check.blockSignals(True)
            check.setChecked(extension in self._active_extensions)
            check.blockSignals(False)
        self.recursive.blockSignals(True)
        self.recursive.setChecked(self._active_recursive)
        self.recursive.blockSignals(False)

    def _schedule_scan(self) -> None:
        if self.folder and not self.busy:
            self._pending_scan = True
            self.filter_timer.start()

    def _update_controls(self) -> None:
        for control in (self.folder_button, self.recursive, *self.formats.values()):
            control.setEnabled(not self.busy)
        for role, panel in self.source_panels.items():
            panel.choose.setEnabled(not self.busy and self.folder is not None)
            panel.remap.setEnabled(not self.busy and self.sources[role].path is not None)
            panel.remove.setEnabled(not self.busy and self.sources[role].path is not None)
            panel.box_style.setEnabled(not self.busy)
            panel.type_mapping_button.setEnabled(not self.busy)
            panel.type_mapping_clear.setEnabled(not self.busy and bool(self.type_mappings[role].entries))
        self.report_button.setEnabled(not self.busy and any(s.result and s.result.issues for s in self.sources.values()))
        self.previous_button.setEnabled(bool(self.images))
        self.next_button.setEnabled(bool(self.images))
        self.progress.setVisible(self.busy)
        if hasattr(self, "review"):
            self.review.render()

    def _run(self, label: str, function: Callable, callback: Callable, on_error: Callable | None = None) -> None:
        if self.busy:
            return
        self.review.flush_note()
        self.filter_timer.stop()
        self.busy = True
        self.statusBar().showMessage(label)
        self._callback, self._error_callback = callback, on_error
        self._task = Task(function)
        self._task.signals.completed.connect(self._completed)
        self._update_controls()
        self.pool.start(self._task)

    @Slot(object, str)
    def _completed(self, value: Any, error: str) -> None:
        callback, on_error = self._callback, self._error_callback
        self.busy = False
        self._task = None
        self._callback = self._error_callback = None
        self._update_controls()
        if error:
            if on_error:
                on_error()
            self.statusBar().showMessage("操作失败，保留已有数据")
            QMessageBox.warning(self, "操作未完成", error)
        elif callback:
            callback(value)
        if self._pending_scan and not self.busy and self.folder:
            self.scan_folder(self.folder)

    def choose_folder(self) -> None:
        if self.busy:
            return
        selected = QFileDialog.getExistingDirectory(self, "选择测试集图片文件夹",
                        str(self.folder or self.settings.value("last_folder", "")))
        if selected:
            self.scan_folder(Path(selected))

    def scan_folder(self, folder: Path) -> None:
        if self.busy:
            return
        if not self.review.save(explicit=True):
            return
        self._pending_scan = False
        extensions = {ext for ext, check in self.formats.items() if check.isChecked()}
        recursive = self.recursive.isChecked()
        snapshots = {role: (source.document, source.mapping) for role, source in self.sources.items()}
        old_store = self.review.store
        def scan():
            images = scan_images(folder, extensions, recursive)
            return images, {role: import_results(document, mapping, images) if mapping else None
                            for role, (document, mapping) in snapshots.items()}, (
                                old_store if old_store.folder == folder.resolve() else AnnotationStore.load(folder))
        def done(value):
            self.folder = folder.resolve()
            self.images, results, self.review.store = value
            for role, result in results.items():
                self.sources[role].result = result
            self._active_extensions, self._active_recursive = extensions, recursive
            self.folder_label.setText(str(self.folder))
            self.settings.setValue("last_folder", str(self.folder))
            self._refresh()
            self.statusBar().showMessage(f"图片扫描完成 · {len(self.images)} 张图片" +
                                         (" · 未发现所选格式的图片" if not self.images else ""))
        self._run("正在扫描图片并匹配检测结果…", scan, done, self._restore_filter_controls)

    def choose_json(self, role: str = "algorithm") -> None:
        if self.busy or self.folder is None:
            return
        source = self.sources[role]
        preference = "last_json" if role == "algorithm" else "last_gt_json"
        selected, _ = QFileDialog.getOpenFileName(self, f"选择{source.title} JSON",
                          str(self.settings.value(preference, str(self.folder))), "JSON 文件 (*.json);;所有文件 (*)")
        if selected:
            path = Path(selected)
            self._run(f"正在读取{source.title} JSON…", lambda: load_json(path),
                      lambda document: self._map_document(document, path, role=role))

    def _map_document(self, document: Any, source: Path, initial: FieldMapping | None = None,
                      role: str = "algorithm") -> None:
        state = self.sources[role]
        try:
            dialog = MappingDialog(document, str(source), self, initial, source_kind=state.title)
        except ValueError as exc:
            QMessageBox.warning(self, "无法识别 JSON 结构", str(exc))
            self.statusBar().showMessage("JSON 未导入，保留已有数据")
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.statusBar().showMessage("已取消字段映射，保留已有数据")
            return
        mapping = dialog.mapping()
        images = tuple(self.images)
        def done(value):
            result, signature = value
            state.document, state.path, state.mapping, state.result = document, source, mapping, result
            state.signature = signature
            self.settings.setValue("last_json" if role == "algorithm" else "last_gt_json", str(source))
            self._refresh()
            (self.gt_tabs if role == "ground_truth" else self.target_tabs).setCurrentWidget(self.target_tables[role])
            self.statusBar().showMessage(f"{state.title}已导入 · 匹配 {len(result.referenced)} 张图片 / "
                                         f"{result.matched_target_count} 个有效目标 · 问题 {len(result.issues)} 条")
        self._run(f"正在校验{state.title}并匹配图片…", lambda: (
            import_results(document, mapping, images), source_signature(document, mapping)), done)

    def remap_json(self, role: str = "algorithm") -> None:
        source = self.sources[role]
        if not self.busy and source.path:
            self._map_document(source.document, source.path, source.mapping, role)

    def clear_json(self, role: str = "algorithm") -> None:
        if self.busy:
            return
        source = self.sources[role]
        source.document = source.path = source.mapping = source.result = None
        source.signature = ""
        self._refresh()
        self.statusBar().showMessage(f"已移除{source.title}")

    def configure_box_style(self, role: str) -> None:
        if self.busy:
            return
        dialog = BoxStyleDialog(self.sources[role].title, self.box_styles[role], DEFAULT_BOX_STYLES[role], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        style = dialog.selected_style()
        self.box_styles[role] = style
        self.settings.setValue(f"box_styles/{role}/color", style.color)
        self.settings.setValue(f"box_styles/{role}/line_style", style.line_style)
        for name in LABEL_FIELDS:
            self.settings.setValue(f"box_styles/{role}/{name}", getattr(style, name))
        self._refresh_style_controls()
        self._update_overlays()
        self.statusBar().showMessage(f"{self.sources[role].title}框样式已更新")

    def category_display(self, role: str, category) -> str | None:
        return self.type_mappings.get(role, TypeMapping()).display(category)

    def choose_type_mapping(self, role: str) -> None:
        if self.busy:
            return
        filename, _ = QFileDialog.getOpenFileName(self, f"{self.sources[role].title} · 导入种类显示映射",
            str(self.type_mappings[role].path or self.folder or ""), "映射文件 (*.txt *.map *.mapping);;所有文件 (*)")
        if not filename:
            return
        try:
            mapping = TypeMapping.load(Path(filename))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "种类映射未导入", str(exc))
            return
        self.set_type_mapping(role, mapping)

    def set_type_mapping(self, role: str, mapping: TypeMapping) -> None:
        if self.busy:
            return
        self.review.flush_note()
        self.type_mappings[role] = mapping
        self.settings.setValue(f"type_mappings/{role}/entries", json.dumps(mapping.entries, ensure_ascii=False))
        self.settings.setValue(f"type_mappings/{role}/path", str(mapping.path or ""))
        self._refresh_type_mapping_controls()
        source = self.sources[role]
        targets = source.result.detections.get(self.current_image(), []) if source.result else []
        for row, target in enumerate(targets):
            item = self.target_tables[role].item(row, 1)
            if item:
                item.setText(self.category_display(role, target.category) if target.category is not None else "—")
                item.setToolTip(f"原始种类：{category_text(target.category)}")
        self._update_overlays()
        self.review.render()
        self._update_controls()
        self.statusBar().showMessage(f"{source.title}：" + (f"已应用 {len(mapping.entries)} 条种类映射" if mapping.entries else "已恢复原始种类显示"))

    def _refresh_type_mapping_controls(self) -> None:
        for role, mapping in self.type_mappings.items():
            panel = self.source_panels[role]
            panel.type_mapping_status.setText(f"种类显示：{len(mapping.entries)} 条映射 · {mapping.path.name if mapping.path else '已保存配置'}"
                if mapping.entries else "种类显示：原始种类（可选导入映射）")
            panel.type_mapping_status.setToolTip(str(mapping.path or "") + "\n仅替换显示名称，不改变原始种类、标注身份和复核状态。")
            panel.type_mapping_clear.setEnabled(bool(mapping.entries) and not self.busy)

    def _refresh_style_controls(self) -> None:
        for role, style in self.box_styles.items():
            button = self.source_panels[role].box_style
            button.setIcon(style.icon())
            labels = "、".join(text for name, text in LABEL_FIELDS.items() if getattr(style, name)) or "仅目标框"
            button.setToolTip(f"{self.sources[role].title}目标框：{LINE_STYLES[style.line_style][0]} · {style.color.upper()}\n"
                              f"显示信息：{labels}\n点击配置线型、颜色和文字信息")

    def _update_overlays(self) -> None:
        path = self.current_image()
        if path is None or path != self.image_view.path:
            return
        sources = {
            role: (source.result.detections.get(path, []) if source.result else [], self.box_styles[role], source.title)
            for role, source in self.sources.items()
        }
        manual = self.review.role_targets("manual")
        if manual:
            gt_style = self.box_styles["ground_truth"]
            sources["manual"] = (manual, BoxStyle("#b77aff", gt_style.line_style,
                **{name: getattr(gt_style, name) for name in LABEL_FIELDS}), "人工补框")
        self.image_view.set_overlays(sources, self.type_mappings)
        self.review.highlight()

    def _source_image_info(self, role: str, image: Path, issue_names=None) -> tuple[str, str]:
        result = self.sources[role].result
        if result is None:
            return "—", "未导入"
        if issue_names is None:
            issue_names = self._issue_names(result)
        ambiguous, invalid = issue_names
        if image in result.referenced:
            count = len(result.detections.get(image, []))
            return str(count), ("含无效目标" if image.name.casefold() in invalid else "已匹配" if count else "空目标列表")
        if image.name.casefold() in ambiguous:
            return "重名", "同名歧义"
        if image.name.casefold() in invalid:
            return "无效", "目标列表无效"
        return "无记录", "JSON 无记录"

    @staticmethod
    def _issue_names(result: ImportResult | None) -> tuple[set[str], set[str]]:
        if result is None:
            return set(), set()
        return ({i.image_name.casefold() for i in result.issues if i.kind == "同名歧义"},
                {i.image_name.casefold() for i in result.issues if i.kind in ("目标数据无效", "目标列表无效")})

    def _refresh_source_panels(self) -> None:
        summaries = []
        for role, source in self.sources.items():
            panel = self.source_panels[role]
            result = source.result
            panel.path.setText(str(source.path) if source.path else "尚未导入 JSON")
            panel.choose.setText("替换 JSON…" if source.path else "添加 JSON…")
            panel.status.setText(f"{result.matched_target_count} 个目标" if result else "未导入")
            tooltip = ""
            if source.mapping:
                mapping = source.mapping
                tooltip = (f"记录：{mapping.collection.pointer or '根节点'}\n图片字段：{mapping.image_field}\n"
                           f"目标列表：{mapping.detections_field or '平铺记录'}\n框格式：{mapping.box_format}")
            if result:
                tooltip += (f"\n已关联 {len(result.referenced)} 张图片\n问题 {len(result.issues)} 条\n"
                            f"无有效关联 {len(self.images) - len(result.referenced)} 张图片")
            panel.remap.setToolTip(tooltip or "选择 JSON 后确认字段映射")
            panel.status.setToolTip(tooltip)
            label = "算法" if role == "algorithm" else "GT"
            summaries.append(f"{label} {result.matched_target_count} 个目标" if result else f"{label}未导入")
        self.data_section.set_summary(" · ".join(summaries))
        self.folder_section.set_summary(f"{self.folder.name or self.folder} · {len(self.images)} 张图片" if self.folder else "尚未选择文件夹")

    def _refresh(self) -> None:
        self.review.rebuild()
        previous = self.current_image()
        self.image_table.blockSignals(True)
        self.image_table.setRowCount(len(self.images))
        target_row = 0
        issues_by_role = {role: self._issue_names(source.result) for role, source in self.sources.items()}
        for row, image in enumerate(self.images):
            relative = str(image.relative_to(self.folder)) if self.folder else image.name
            item = QTableWidgetItem(relative)
            item.setToolTip(str(image))
            self.image_table.setItem(row, 0, item)
            for column, role in enumerate(self.sources, 1):
                count, state = self._source_image_info(role, image, issues_by_role[role])
                item = QTableWidgetItem(count)
                item.setToolTip(f"{self.sources[role].title}：{state}")
                item.setForeground(QColor("#2563eb" if role == "algorithm" else "#13795b"))
                self.image_table.setItem(row, column, item)
            if image == previous:
                target_row = row
        self.image_table.blockSignals(False)
        self._filter_table()
        if self.images:
            visible = [r for r in range(len(self.images)) if not self.image_table.isRowHidden(r)]
            if target_row not in visible and visible:
                target_row = visible[0]
            if visible:
                self.image_table.setCurrentCell(target_row, 0)
                self._selection_changed()
            else:
                self.image_table.setCurrentCell(-1, -1)
                self._selection_changed()
        else:
            self._selection_changed()
        lines = [f"图片 {len(self.images)} 张"]
        for source in self.sources.values():
            result = source.result
            lines.append(f"{source.title}：{len(result.referenced)} 张 · {result.matched_target_count} 个目标 · "
                         f"{len(result.issues)} 个问题" if result else f"{source.title}：未导入")
        self.summary.setText("\n".join(lines))
        self._refresh_source_panels()
        self._update_controls()

    def _filter_table(self) -> None:
        query = self.search.text().casefold()
        for row in range(self.image_table.rowCount()):
            self.image_table.setRowHidden(row, query not in self.image_table.item(row, 0).text().casefold())
        current = self.image_table.currentRow()
        if current < 0 or self.image_table.isRowHidden(current):
            visible = next((r for r in range(self.image_table.rowCount()) if not self.image_table.isRowHidden(r)), -1)
            self.image_table.setCurrentCell(visible, 0 if visible >= 0 else -1)

    def current_image(self) -> Path | None:
        row = self.image_table.currentRow()
        if 0 <= row < self.image_table.rowCount() and not self.image_table.isRowHidden(row):
            # Use the cell's path so refreshing after a rescan cannot index an old row into a new image list.
            item = self.image_table.item(row, 0)
            return Path(item.toolTip()) if item else None
        return None

    def _selection_changed(self, *_args) -> None:
        self.review.flush_note()
        self.review.gt_reference.invalidate()
        path = self.current_image()
        for index, (role, table) in enumerate(self.target_tables.items()):
            table.blockSignals(True)
            table.setRowCount(0)
            self.set_target_count(role, 0)
        if path is None:
            self.image_title.setText("图片预览")
            self.target_title.setText("当前没有选中的图片")
            self.image_view.message("没有符合当前条件的图片" if self.folder else "选择图片文件夹，开始准备测试集")
            for table in self.target_tables.values():
                table.blockSignals(False)
            self.review.image_changed()
            return
        row = self.image_table.currentRow()
        error = self.image_view.show_image(path)
        dimensions = self.image_view.dimensions
        suffix = f" · {dimensions[0]} × {dimensions[1]}" if dimensions else " · 解码失败"
        self.image_title.setText(f"{row + 1} / {len(self.images)}   {path.name}{suffix}")
        self.image_title.setToolTip(str(path))
        if error:
            self.statusBar().showMessage(f"{path.name} 无法显示：{error}")
        for index, (role, table) in enumerate(self.target_tables.items()):
            if role == "manual":
                continue
            result = self.sources[role].result
            targets = result.detections.get(path, []) if result else []
            table.setRowCount(len(targets))
            self.set_target_count(role, len(targets))
            for ri, target in enumerate(targets):
                values = [str(ri + 1), self.category_display(role, target.category) if target.category is not None else "—",
                          str(target.score) if target.score is not None else "—", *(f"{v:g}" for v in target.box)]
                for ci, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(f"当前显示序号：{ri + 1}（与叠加框一致）\n"
                                    f"{self.sources[role].title}原始 JSON：第 {target.record_index} 条记录，第 {target.target_index} 个目标")
                    if ci == 1:
                        item.setToolTip(f"原始种类：{category_text(target.category)}\n" + item.toolTip())
                    table.setItem(ri, ci, item)
        for table in self.target_tables.values():
            table.blockSignals(False)
        self.review.image_changed()
        self._update_target_title()
        self._update_overlays()

    def _update_target_title(self, *_args) -> None:
        path = self.current_image()
        if path is None:
            self.target_title.setText("当前没有选中的图片")
            return
        role = "algorithm"
        count, state = self._source_image_info(role, path) if role != "manual" else (str(len(self.review.role_targets("manual"))), "人工补充")
        title = self.sources[role].title if role != "manual" else "人工补框"
        self.target_title.setText(f"{title}：{state} · {count} 个目标 · 像素 xyxy")

    def set_target_count(self, role: str, count: int) -> None:
        host = self.target_tabs if role == "algorithm" else self.gt_tabs
        title = {"algorithm": "算法结果", "ground_truth": "Ground Truth", "manual": "人工补框"}[role]
        host.setTabText(host.indexOf(self.target_tables[role]), f"{title} ({count})")

    def _reference_tab_changed(self, *_args) -> None:
        if hasattr(self, "review"):
            role = "manual" if self.gt_tabs.currentWidget() is self.target_tables["manual"] else "ground_truth"
            self.review.reference_tab_changed(role)

    def move_image(self, direction: int) -> None:
        # Do not steal navigation keys from text entry fields or an open modal dialog.
        if QApplication.activeModalWidget():
            return
        start = self.image_table.currentRow()
        row = start + direction
        if start < 0:
            row = 0 if direction > 0 else len(self.images) - 1
        while 0 <= row < len(self.images):
            if not self.image_table.isRowHidden(row):
                self.image_table.setCurrentCell(row, 0)
                self.image_table.scrollToItem(self.image_table.item(row, 0))
                return
            row += direction

    def show_report(self) -> None:
        if not any(source.result for source in self.sources.values()):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("导入问题明细")
        dialog.resize(1060, 560)
        layout = QVBoxLayout(dialog)
        label = QLabel("无效目标不会进入后续标注；同名或未匹配的记录不会自动分配给图片。问题明细可选中后按 Ctrl+C 复制。")
        label.setWordWrap(True)
        layout.addWidget(label)
        table = make_table(["来源", "问题", "图片", "记录", "目标", "说明"])
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        issues = [(source.title, issue) for source in self.sources.values() if source.result for issue in source.result.issues]
        table.setRowCount(len(issues))
        for row, (source_title, issue) in enumerate(issues):
            for col, value in enumerate((source_title, issue.kind, issue.image_name, str(issue.record_index),
                                        str(issue.target_index or "—"), issue.message)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, col, item)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        def copy_rows():
            rows = sorted({index.row() for index in table.selectedIndexes()})
            QApplication.clipboard().setText("\n".join("\t".join(table.item(row, col).text()
                                        for col in range(table.columnCount())) for row in rows))
        copy = QAction(table)
        copy.setShortcut(QKeySequence.StandardKey.Copy)
        copy.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        copy.triggered.connect(copy_rows)
        table.addAction(copy)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "review"):
            QApplication.instance().installEventFilter(self.review)

    def closeEvent(self, event) -> None:
        if self.busy:
            self.statusBar().showMessage("正在处理数据，请完成后再关闭窗口")
            event.ignore()
            return
        if not self.review.save(explicit=True):
            event.ignore()
            return
        self.review.gt_reference.invalidate()
        QApplication.instance().removeEventFilter(self.review)
        self.settings.setValue("extensions", [ext for ext, check in self.formats.items() if check.isChecked()])
        self.settings.setValue("recursive", self.recursive.isChecked())
        self.settings.setValue("sections/folder_expanded", self.folder_section.is_expanded())
        self.settings.setValue("sections/data_expanded", self.data_section.is_expanded())
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DetRetLabel")
    app.setOrganizationName("DetRetLabel")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
