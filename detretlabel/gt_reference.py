"""Non-blocking, cancellable GT suggestions and independent GT inspection."""
from __future__ import annotations

from threading import Event

from PySide6.QtCore import QObject, QThreadPool, QTimer
from PySide6.QtGui import QBrush, QColor

from .box_style import mismatch_color
from .gt_matching import GTCandidate, GTMatch, match_gt


class GTReferenceController(QObject):
    def __init__(self, review):
        super().__init__(review)
        self.review, self.w = review, review.w
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._dispatch)
        self._key = None
        self._generation = 0
        self._pending = None
        self._job = None
        self._cancel = None
        self._painted = set()
        self.result: GTMatch | None = None
        self.selected_index = 0
        self.selected_manual_id = None
        self.w.auto_gt_overlay.toggled.connect(self._options_changed)

    def _options_changed(self, enabled):
        self.w.settings.setValue("review/auto_gt_overlay", enabled)
        self.apply_overlays()

    def _clear_rows(self):
        table = self.w.target_tables["ground_truth"]
        for row in self._painted:
            if row < table.rowCount():
                for column in range(table.columnCount()):
                    if item := table.item(row, column):
                        item.setBackground(QBrush())
        self._painted.clear()

    def _clear_selection(self):
        self.selected_index = 0
        self.selected_manual_id = None
        for role in ("ground_truth", "manual"):
            table = self.w.target_tables[role]
            previous = table.blockSignals(True)
            table.clearSelection()
            table.setCurrentCell(-1, -1)
            table.blockSignals(previous)

    def invalidate(self):
        self._generation += 1
        self._key = None
        self._pending = None
        self.timer.stop()
        if self._cancel:
            self._cancel.set()
        self.result = None
        self._clear_rows()
        self._clear_selection()
        self.w.gt_match_status.setText("GT 匹配提示仅供参考，不参与人工判定。")
        self.apply_overlays()

    def update(self):
        target = self.review.current
        key = (self.w.current_image(), target.id, self.w.sources["ground_truth"].signature,
               self.w.type_mappings["algorithm"], self.w.type_mappings["ground_truth"]) if target and target.role == "algorithm" else None
        if key != self._key:
            self.invalidate()
            self._key = key
            candidates = self.review.role_targets("ground_truth") if key else []
            if candidates:
                # Only the active target is compared. No image-wide all-pairs computation.
                payload = tuple(GTCandidate(i, candidate.box,
                    self.w.category_display("ground_truth", candidate.category)) for i, candidate in enumerate(candidates, 1))
                self._pending = (self._generation, target.box, self.w.category_display("algorithm", target.category), payload)
                self.w.gt_match_status.setText("正在后台匹配 GT…可继续切图或审核。")
                self.timer.start(0)
            elif key:
                self.w.gt_match_status.setText("本图没有可匹配的 GT；可继续人工审核。")
        if self.result:
            self._paint_result()
        self.apply_overlays()

    def _dispatch(self):
        if self._job is not None or self._pending is None:
            return
        from .app import Task
        generation, box, category, candidates = self._pending
        self._pending = None
        cancel = Event()
        self._cancel = cancel
        self._running_generation = generation
        self._job = Task(lambda: match_gt(box, category, candidates, cancel))
        self._job.signals.completed.connect(self._done)
        self.pool.start(self._job)

    def _done(self, value, error):
        generation = self._running_generation
        self._job = None
        self._cancel = None
        if generation == self._generation:
            if error:
                self.w.gt_match_status.setText("GT 匹配暂不可用；可继续审核或手动点选 GT。")
            elif value is not None:
                self.result = value
                self._paint_result(reveal=True)
                self.apply_overlays()
        if self._pending is not None:
            self.timer.start(0)

    def _paint_result(self, reveal=False):
        self._clear_rows()
        table = self.w.target_tables["ground_truth"]
        hints = []
        for index, color, iou, description in (
                (self.result.same_index, "#e7f7ec", self.result.same_iou, "同类"),
                (self.result.mismatch_index, "#fff5d6", self.result.best_iou, "整体最大但异类")):
            if not index or index > table.rowCount():
                continue
            row = index - 1
            for column in range(table.columnCount()):
                if item := table.item(row, column):
                    item.setBackground(QColor(color))
            self._painted.add(row)
            hints.append(f"{description} GT #{index} · IoU {iou:.3f}")
        self.w.gt_match_status.setText("  ｜  ".join(hints) + "（仅参考）" if hints else "没有与当前算法框重叠的 GT（IoU > 0）。")
        # Reveal a suggestion without making it a selected row or changing the review target.
        if reveal and hints and not self.selected_index:
            item = table.item((self.result.same_index or self.result.best_index) - 1, 0)
            if item:
                table.scrollToItem(item)

    def inspect(self, index: int, role: str = "ground_truth"):
        targets = self.review.role_targets(role)
        if role in ("ground_truth", "manual") and 0 < index <= len(targets):
            self.selected_index = index if role == "ground_truth" else 0
            self.selected_manual_id = targets[index - 1].id if role == "manual" else None
            self.apply_overlays()

    def clear_inspection(self):
        self.selected_index = 0
        self.selected_manual_id = None
        self.apply_overlays()

    def apply_overlays(self):
        automatic = self.w.auto_gt_overlay.isChecked() and self.result is not None
        manual = next((index for index, target in enumerate(self.review.role_targets("manual"), 1)
                       if target.id == self.selected_manual_id), 0)
        if not manual:
            self.selected_manual_id = None
        self.w.image_view.set_gt_references(self.selected_index,
            self.result.same_index if automatic else 0, self.result.mismatch_index if automatic else 0,
            mismatch_color(self.w.box_styles["algorithm"].color, self.w.box_styles["ground_truth"].color), manual=manual)
