"""Coordinate target selection, keyboard stages and safe annotation persistence."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QEvent, QKeyCombination, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QInputDialog, QLineEdit, QMessageBox,
    QPlainTextEdit, QTableWidgetItem, QTextEdit, QWidget,
)

from .annotation import AnnotationStore, ReviewConfig, ReviewTarget, STATUSES, source_signature
from .annotation_panel import display_key
from .review_report import build_report, write_excel
from .gt_reference import GTReferenceController
from .widgets import batch_table_updates


class ReviewController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.panel = window.annotation_panel
        self.store = AnnotationStore()
        self.targets: dict[str, ReviewTarget] = {}
        self.by_image: dict[Path, list[ReviewTarget]] = {}
        self.current_id: str | None = None
        self.reason_status: str | None = None
        self.phase = "algorithm"
        self._image_path: Path | None = None
        self._phase_signature = ""
        self._previous_show_all: bool | None = None
        self._hold_sources: set[str] = set()
        self._hold_key: int | None = None
        self._selecting = False
        self._note_pending = False
        self._all_progress_dirty = True
        self.gt_reference = GTReferenceController(self)
        self.config = ReviewConfig()
        self.shortcuts = {}
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self.save)
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.setInterval(500)
        self.note_timer.timeout.connect(self.flush_note)
        self.reason_timer = QTimer(self)
        self.reason_timer.setSingleShot(True)
        self.reason_timer.setInterval(500)
        self.reason_timer.timeout.connect(self.flush_reason_draft)
        self.panel.reason_combo.currentTextChanged.connect(self.reason_draft_changed)
        self.panel.status_selected.connect(self.choose_status)
        self.panel.reason_selected.connect(self.choose_reason)
        self.panel.confirm_requested.connect(self.confirm)
        self.panel.note_edited.connect(self.note_edited)
        self.panel.scope_changed.connect(self.scope_changed)
        self.panel.overlay_options_changed.connect(self.highlight)
        self.panel.import_config.connect(self.import_config)
        self.panel.save_requested.connect(lambda: self.save(explicit=True))
        self.panel.export_requested.connect(self.export_report)
        self.panel.undo_requested.connect(self.undo)
        self.panel.previous_target.connect(lambda: self.move_target(-1))
        self.panel.next_target.connect(lambda: self.move_target(1))
        self.panel.next_unreviewed.connect(lambda: self.next_unreviewed(True))
        self.panel.clear_requested.connect(self.clear)
        self.panel.clear_image_requested.connect(lambda: self.clear_annotations(all_images=False))
        self.panel.clear_all_requested.connect(lambda: self.clear_annotations(all_images=True))
        self.panel.draw_requested.connect(self.toggle_draw)
        self.panel.delete_manual_requested.connect(self.delete_manual)
        self.panel.all_positive_requested.connect(self.all_positive)
        self.panel.review_requested.connect(self.toggle_review)
        self.panel.finish_review_requested.connect(self.finish_review)
        self.panel.next_review_requested.connect(self.next_review_image)
        self.panel.hold_changed.connect(lambda enabled: self.set_hold("mouse", enabled))
        window.image_view.box_drawn.connect(self.add_manual)
        window.image_view.target_clicked.connect(self.clicked_target)
        window.image_view.draw_mode_changed.connect(self.draw_mode_changed)
        for role, table in window.target_tables.items():
            table.currentCellChanged.connect(lambda row, col, oldrow, oldcol, r=role: self.table_selected(r, row))
            table.cellClicked.connect(lambda row, col, r=role: self.table_selected(r, row))
        window.target_tables["manual"].cellDoubleClicked.connect(lambda row, col: self.edit_manual(row))
        default_path = Path(__file__).resolve().parents[1] / "config" / "reasons.json"
        configured = Path(window.settings.value("review/reasons_path", str(default_path)))
        try:
            self.apply_config(ReviewConfig.load(configured if configured.exists() else default_path))
        except (OSError, ValueError):
            self.apply_config(ReviewConfig())
            self.panel.save_state.setText("原因配置无法读取，请点击“原因配置”重新导入。")
        self.panel.auto_next.setChecked(window.settings.value("review/auto_next", self.config.auto_advance, type=bool))
        self.panel.auto_next.toggled.connect(lambda value: window.settings.setValue("review/auto_next", value))
        QApplication.instance().installEventFilter(self)
        self.render()

    @property
    def current(self) -> ReviewTarget | None:
        return self.targets.get(self.current_id)

    def queue(self, path: Path | None = None) -> list[ReviewTarget]:
        return self.role_targets(self.phase, path)

    def target_completed(self, target: ReviewTarget) -> bool:
        record = self.store.get(target.id) or {}
        status = record.get("status")
        return (status in ("true_positive", "false_positive") and not record.get("reason_pending", False)) if target.role == "algorithm" else status == "false_negative"

    def algorithm_ready(self, path: Path | None = None) -> bool:
        return all(self.target_completed(t) for t in self.role_targets("algorithm", path))

    def algorithm_signature(self) -> str:
        return self.w.sources["algorithm"].signature or "no_algorithm_json"

    def review_completed(self, path: Path | None = None) -> bool:
        path = path or self.w.current_image()
        return self.algorithm_ready(path) and self.miss_review_completed(path)

    def miss_review_completed(self, path: Path | None = None) -> bool:
        path = path or self.w.current_image()
        return bool(path and self.w.folder and all(self.target_completed(t) for t in self.role_targets("manual", path)) and
                    self.store.review_completed(path.relative_to(self.w.folder).as_posix(), self.algorithm_signature()))

    def role_targets(self, role: str, path: Path | None = None) -> list[ReviewTarget]:
        return [t for t in self.by_image.get(path or self.w.current_image(), []) if t.role == role]

    def rebuild(self) -> None:
        self._all_progress_dirty = True
        self.flush_note()
        self.flush_reason_draft()
        if self.w.folder and self.store.folder != self.w.folder:
            self.store = AnnotationStore.load(self.w.folder)
        self.targets.clear()
        self.by_image = {image: [] for image in self.w.images}
        if not self.w.folder:
            return
        for role, source in self.w.sources.items():
            if source.result is None or source.mapping is None:
                continue
            if not source.signature:
                source.signature = source_signature(source.document, source.mapping)
            for image, detections in source.result.detections.items():
                if image not in self.by_image:
                    continue
                relative = image.relative_to(self.w.folder).as_posix()
                for detection in detections:
                    target = ReviewTarget.from_detection(relative, role, source.path, source.signature, detection)
                    self.by_image[image].append(target)
                    self.targets[target.id] = target
        for target in self.store.manual_targets.values():
            image = self.w.folder / Path(target.image)
            if image in self.by_image:
                self.by_image[image].append(target)
                self.targets[target.id] = target

    def image_changed(self) -> None:
        self.flush_note()
        self.flush_reason_draft()
        path = self.w.current_image()
        if path != self._image_path or self.algorithm_signature() != self._phase_signature:
            self.leave_review(render=False)
            self._image_path = path
            self._phase_signature = self.algorithm_signature()
        self.reason_status = None
        self.fill_manual_table()
        queue = self.queue()
        target = next((t for t in queue if t.id == self.current_id), None)
        if target is None:
            target = next((t for t in queue if not self.target_completed(t)), queue[0] if queue else None)
        self.current_id = target.id if target else None
        if target:
            self.reason_status = ("false_negative" if target.role == "manual" else
                                  "false_positive" if (self.store.get(target.id) or {}).get("reason_pending") else None)
        self.update_rows()
        self.render()
        if target:
            self.select(target.id, activate_table=False)
        else:
            self.highlight()

    def fill_manual_table(self) -> None:
        targets = self.role_targets("manual")
        table = self.w.target_tables["manual"]
        table.blockSignals(True)
        with batch_table_updates(table):
            table.setRowCount(len(targets))
            for row, target in enumerate(targets):
                values = [str(row + 1), str(target.category) if target.category is not None else "—", "—",
                          *(f"{v:g}" for v in target.box)]
                for col, value in enumerate(values):
                    table.setItem(row, col, QTableWidgetItem(value))
        table.blockSignals(False)
        self.w.set_target_count("manual", len(targets))

    def table_selected(self, role: str, row: int) -> None:
        if self._selecting or row < 0 or self.w.busy:
            return
        if role in ("ground_truth", "manual"):
            self.gt_reference.inspect(row + 1, role)
            return
        if role == "algorithm" and self.phase != "algorithm":
            self.w.image_view.set_draw_mode(False)
            self.leave_review()
        targets = self.role_targets(role)
        if row < len(targets) and targets[row] in self.queue():
            self.select(targets[row].id, activate_table=False)

    def clicked_target(self, role: str, index: int) -> None:
        if role in ("ground_truth", "manual"):
            if not self.w.busy:
                self._selecting = True
                self.w.target_tables[role].setCurrentCell(index - 1, 0)
                self._selecting = False
                self.gt_reference.inspect(index, role)
            return
        if role == "algorithm" and self.phase != "algorithm":
            self.w.image_view.set_draw_mode(False)
            self.leave_review()
        targets = self.role_targets(role)
        if 0 < index <= len(targets) and targets[index - 1] in self.queue():
            self.select(targets[index - 1].id)

    def reference_tab_changed(self, role: str) -> None:
        if self._selecting or self.w.busy:
            return
        row = self.w.target_tables[role].currentRow()
        if row >= 0:
            self.gt_reference.inspect(row + 1, role)
        else:
            self.gt_reference.clear_inspection()

    def edit_manual(self, row: int) -> None:
        targets = self.role_targets("manual")
        if self.w.busy or not 0 <= row < len(targets):
            return
        target_id = targets[row].id
        if self.phase != "manual" and not self.start_review():
            return
        self.select(target_id)
        self.w.image_view.setFocus()
        QTimer.singleShot(0, self.scroll_to_review)

    def select(self, target_id: str, activate_table: bool = True) -> None:
        target = self.targets.get(target_id)
        if target is None or target.role != self.phase:
            return
        if target_id != self.current_id:
            self.flush_note()
            self.flush_reason_draft()
            self.reason_status = None
        self.current_id = target_id
        if target.role == "manual":
            self.reason_status = "false_negative"
        elif (self.store.get(target.id) or {}).get("reason_pending"):
            self.reason_status = "false_positive"
        self._selecting = True
        table = self.w.target_tables[target.role]
        if activate_table:
            (self.w.target_tabs if target.role == "algorithm" else self.w.gt_tabs).setCurrentWidget(table)
        row = next((index for index, item in enumerate(self.role_targets(target.role)) if item.id == target_id), -1)
        table.setCurrentCell(row, 0)
        if row >= 0:
            table.scrollToItem(table.item(row, 0))
        self._selecting = False
        self.render()
        self.highlight()

    def highlight(self) -> None:
        self.w.image_view.allowed_roles = {"algorithm", "ground_truth", "manual"}
        target = self.current
        row = next((i for i, item in enumerate(self.role_targets(target.role), 1) if item.id == target.id), 0) if target else 0
        self.w.image_view.set_current_target(target.role if target else None, row,
            show_all=self.panel.show_all_targets.isChecked() or bool(self._hold_sources),
            show_noncurrent_labels=self.panel.show_noncurrent_labels.isChecked(),
            retained_algorithm_indices={i for i, item in enumerate(self.role_targets("algorithm"), 1)
                if self.target_completed(item)} if self.panel.keep_reviewed_targets.isChecked() else set(),
            show_ground_truth=self.panel.gt_button.isChecked())
        self.gt_reference.update()

    def render(self) -> None:
        target, queue = self.current, self.queue()
        position = next((i for i, t in enumerate(queue, 1) if target and t.id == target.id), 0)
        self.panel.set_context(target, self.store.get(target.id) if target else None, position, len(queue),
                               sum(self.target_completed(t) for t in queue), self.reason_status, self.phase,
                               self.w.category_display(target.role, target.category) if target else None)
        self.panel.scope_combo.blockSignals(True)
        self.panel.scope_combo.setCurrentIndex(0 if self.phase == "algorithm" else 1)
        self.panel.scope_combo.blockSignals(False)
        self.panel.undo_button.setEnabled(bool(self.store.history) and not self.w.busy)
        self.panel.export_button.setEnabled(bool(self.w.images) and not self.w.busy)
        image = self.w.current_image()
        relative = image.relative_to(self.w.folder).as_posix() if image and self.w.folder else None
        self.panel.clear_image_button.setEnabled(bool(relative and self.store.has_annotations(relative)) and not self.w.busy)
        self.panel.clear_all_button.setEnabled(self.store.folder is not None and self.store.has_annotations() and not self.w.busy)
        self.panel.setEnabled(not self.w.busy)
        can_edit = target is not None and self.w.image_view.path is not None and not self.w.image_view.draw_mode
        can_classify = can_edit and self.phase == "algorithm"
        for button in self.panel.wheel.buttons.values():
            button.setEnabled(can_classify)
        self.panel.reason_frame.setEnabled(can_edit)
        valid_image = self.w.image_view.path is not None and not self.w.busy
        self.panel.hold_button.setEnabled(valid_image)
        if self.w.busy:
            self.release_hold()
        self.panel.all_positive_button.setEnabled(valid_image and self.phase == "algorithm" and bool(self.role_targets("algorithm")) and not self.w.image_view.draw_mode)
        gt_available = self.w.sources["ground_truth"].result is not None
        self.panel.gt_button.setEnabled(gt_available and not self.w.busy)
        key = display_key(self.config.shortcuts["toggle_ground_truth"])
        self.panel.gt_button.setToolTip(f"显示 / 隐藏本图全部 GT 框与标签 · {key}\n只控制显示，不改变当前标注范围。"
                                       if gt_available else f"请先导入 GT 参考标注，再按 {key} 或点击此按钮显示。")
        ready = self.algorithm_ready()
        completed = self.review_completed()
        miss_completed = self.miss_review_completed()
        manual_count = len(self.role_targets("manual"))
        state = ("已完成漏检复核 · 图片审核完成" if completed else
                 "04 已完成 · 03 尚未完成，图片仍未完成" if miss_completed else
                 "漏检复核中" if self.phase == "manual" else
                 "算法已审完，请复核漏检" if ready else "03 尚未完成，也可先进入 04 漏检复核")
        if self.phase == "manual":
            state += f" · 已补漏 {manual_count} 个"
            if not ready and not miss_completed:
                state += "\n03 尚未完成：可保存 / 完成 04，图片仍保留未完成标记。"
        source = self.w.sources["algorithm"].result
        if self.w.current_image() and (source is None or self.w.current_image() not in source.referenced):
            state += "\n算法 JSON 无本图记录，请核对输入后人工复核。"
        self.panel.review_status.setText(state)
        key = {name: display_key(value) for name, value in self.config.shortcuts.items()}
        self.panel.review_button.setText(("返回算法审核" if self.phase == "manual" else "重新复核" if miss_completed else "进入漏检复核") + f"  {key['start_review']}")
        self.panel.review_button.setEnabled(valid_image and not self.w.image_view.draw_mode)
        self.panel.review_hint.setVisible(self.phase == "manual")
        self.panel.review_draw_button.setVisible(self.phase == "manual")
        self.panel.review_draw_button.setEnabled(valid_image and self.phase == "manual")
        self.panel.finish_review_button.setVisible(self.phase == "manual")
        self.panel.finish_review_button.setText(("完成漏检复核" if manual_count else "确认无漏检并完成") + f"  {key['finish_review']}")
        self.panel.finish_review_button.setEnabled(valid_image and self.phase == "manual" and not self.w.image_view.draw_mode)
        self.panel.finish_review_button.setToolTip("保存并完成 04 漏检复核；03 未完成时，图片仍标记为未完成，报告分别列出两步状态。")
        self.panel.next_review_button.setEnabled(bool(self.w.images) and not self.w.busy)

    def update_rows(self) -> None:
        for role, table in self.w.target_tables.items():
            with batch_table_updates(table):
                for row, target in enumerate(self.role_targets(role)):
                    record = self.store.get(target.id) or {}
                    status = "仅参考" if role == "ground_truth" else STATUSES.get(record.get("status"), "未标注")
                    if role == "algorithm" and record.get("status") == "false_negative":
                        status = "旧漏检，待重审"
                    elif role == "algorithm" and record.get("reason_pending"):
                        status += " · 原因待确认"
                    for col, text in ((7, status), (8, "" if role == "ground_truth" else record.get("reason", ""))):
                        item = QTableWidgetItem(text)
                        item.setToolTip(record.get("note", "") if col == 7 else text)
                        table.setItem(row, col, item)
        image = self.w.current_image()
        rows = enumerate(self.w.images) if self._all_progress_dirty else (
            ((self.w.image_table.currentRow(), image),) if image else ())
        with batch_table_updates(self.w.image_table):
            for row, path in rows:
                queue = self.role_targets("algorithm", path)
                done = sum(self.target_completed(t) for t in queue)
                completed = self.review_completed(path)
                miss_completed = self.miss_review_completed(path)
                state = "复核完成" if completed else "03未完成 · 04已完成" if miss_completed else "待复核"
                text = f"{done}/{len(queue)} · " + state
                item = self.w.image_table.item(row, 3)
                if item is None:
                    item = QTableWidgetItem()
                    self.w.image_table.setItem(row, 3, item)
                if item.text() != text:
                    item.setText(text)
                item.setToolTip(f"03 算法审核：{'已完成' if done == len(queue) else '未完成'}\n04 漏检复核：{'已完成' if miss_completed else '待复核'}\nGT 不参与决策。")
                item.setForeground(QColor("#13795b" if completed else "#24344b"))
        self._all_progress_dirty = False

    def changed(self) -> None:
        self.save_timer.start()
        self.panel.save_state.setText("有新修改，正在自动保存…")
        self.update_rows()
        self.render()
        self.highlight()

    def choose_status(self, status: str) -> None:
        if (self.current is None or self.current.role != "algorithm" or status not in ("true_positive", "false_positive")
                or self.w.busy or self.w.image_view.path is None or self.w.image_view.draw_mode):
            return
        self.flush_note()
        record = self.store.get(self.current.id) or {}
        reason = record.get("reason", "") if record.get("status") == status else ""
        self.store.set_annotation(self.current, status, reason, self.panel.note.text(),
            reason_pending=status == "false_positive", reason_draft=reason)
        self.reason_status = status if status != "true_positive" else None
        self.changed()
        if status == "true_positive":
            self.advance_after_decision()
        self.w.image_view.setFocus()

    def choose_reason(self, reason: str) -> None:
        if self.current is None or self.reason_status is None or self.w.busy or self.w.image_view.path is None:
            return
        self.flush_note()
        self.store.set_annotation(self.current, self.reason_status, reason, self.panel.note.text())
        self.reason_status = None
        self.changed()
        self.advance_after_decision()
        self.w.image_view.setFocus()

    def advance_after_decision(self) -> None:
        if self.phase == "algorithm" and self.reason_status is None and self.algorithm_ready() and not self.miss_review_completed():
            self.start_review()
        elif self.panel.auto_next.isChecked():
            self.next_unreviewed(False)

    def confirm(self) -> None:
        self.flush_note()
        if self.reason_status:
            self.choose_reason(self.panel.reason_combo.currentText().strip())
        elif self.current and self.store.completed(self.current.id):
            if self.phase == "algorithm" and self.algorithm_ready():
                self.start_review()
            else:
                self.next_unreviewed(False)
        self.w.image_view.setFocus()

    def note_edited(self, text: str) -> None:
        if self.current:
            self._note_pending = True
            self.note_timer.start()
            self.panel.clear_image_button.setEnabled(not self.w.busy)
            self.panel.clear_all_button.setEnabled(not self.w.busy)

    def flush_note(self) -> None:
        self.note_timer.stop()
        if not self._note_pending or self.current is None:
            self._note_pending = False
            return
        self._note_pending = False
        record = self.store.get(self.current.id) or {}
        self.store.set_annotation(self.current, record.get("status"), record.get("reason", ""), self.panel.note.text(),
            reason_pending=record.get("reason_pending", False), reason_draft=record.get("reason_draft", ""))
        self.save_timer.start()

    def reason_draft_changed(self, *_args) -> None:
        if self.current and self.current.role == "algorithm" and self.reason_status == "false_positive":
            self.reason_timer.start()

    def flush_reason_draft(self) -> None:
        self.reason_timer.stop()
        if not self.current or self.current.role != "algorithm" or self.reason_status != "false_positive":
            return
        record = self.store.get(self.current.id) or {}
        if record.get("reason_pending"):
            self.store.set_annotation(self.current, "false_positive", record.get("reason", ""), record.get("note", ""),
                reason_pending=True, reason_draft=self.panel.reason_combo.currentText())
            self.save_timer.start()

    def save(self, explicit: bool = False) -> bool:
        self.flush_note()
        self.flush_reason_draft()
        self.save_timer.stop()
        try:
            self.store.save()
        except (OSError, ValueError) as exc:
            self.panel.save_state.setText(f"保存失败：{exc}\n修改仍在内存中，请重试保存。")
            self.panel.save_state.setToolTip(f"{self.panel.save_state.full_text}\n{self.store.path or ''}")
            if explicit:
                QMessageBox.warning(self.w, "标注未保存", str(exc))
            return False
        if self.store.path:
            self.panel.save_state.setText("已自动保存 · .detretlabel/annotations.json")
            self.panel.save_state.setToolTip(str(self.store.path))
        return True

    def report_snapshot(self):
        self.flush_note()
        self.flush_reason_draft()
        pending = (self.current.id, self.panel.reason_combo.currentText().strip()) if self.current and self.reason_status else None
        return build_report(self.w.folder, list(self.w.images), self.by_image, self.store, self.algorithm_signature(),
            self.w.type_mappings, {role: str(source.path) if source.path else "未导入" for role, source in self.w.sources.items()},
            {image: self.w._source_image_info("algorithm", image)[1] for image in self.w.images}, pending)

    def export_report(self) -> None:
        if self.w.busy or not self.w.images:
            return
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        directory = Path(self.w.settings.value("review/export_folder", str(self.w.folder.parent)))
        filename, _ = QFileDialog.getSaveFileName(self.w, "导出当前复核报告（包含全部已加载图片）",
            str(directory / f"复核报告_{stamp}.xlsx"), "Excel 工作簿 (*.xlsx)")
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".xlsx":
            path = path.with_name(path.name + ".xlsx")
        snapshot = self.report_snapshot()
        def done(value):
            self.w.settings.setValue("review/export_folder", str(value.parent))
            self.w.statusBar().showMessage(f"复核报告已导出：{value}")
        self.w._run("正在导出复核报告…", lambda: write_excel(snapshot, path), done)

    def clear(self) -> None:
        if self.current and not self.w.busy:
            self._note_pending = False
            self.store.clear(self.current.id)
            self.reason_status = "false_negative" if self.current.role == "manual" else None
            self.changed()

    def clear_annotations(self, all_images: bool = False) -> None:
        if self.w.busy or not self.w.folder:
            return
        path = self.w.current_image()
        relative = path.relative_to(self.w.folder).as_posix() if path else None
        if not all_images and relative is None:
            return
        title = "清空全部标注" if all_images else "清空本图标注"
        undo_key = display_key(self.config.shortcuts["undo"])
        scope = (f"当前图片目录：\n{self.w.folder}\n全部图片的标注（包含搜索隐藏、格式筛选外和历史记录）"
                 if all_images else f"图片：{relative}\n本图全部目标的标注（包含历史记录）")
        answer = QMessageBox.question(self.w, title,
            f"确定清空以下范围？\n\n{scope}\n\n将清除人工结论、原因、备注、人工补漏框及图片复核状态。"
            f"\n图片和原始 JSON 保留。清空后可用 {undo_key} 整体撤销（仅本次会话）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.flush_note()
        self.flush_reason_draft()
        if not self.store.clear_annotations(None if all_images else relative, focus_image=relative):
            return
        self._note_pending = False
        self.note_timer.stop()
        self.w.image_view.set_draw_mode(False)
        self.release_hold()
        self.leave_review(render=False)
        self.rebuild()
        self.image_changed()
        self.w._update_overlays()
        self.changed()
        self.panel.verticalScrollBar().setValue(0)
        if self.save(explicit=True):
            self.w.statusBar().showMessage(f"{title}已完成并保存；可用 {undo_key} 整体撤销。")
        self.w.image_view.setFocus()

    def undo(self) -> None:
        if self.w.busy:
            return
        self.flush_note()
        self.flush_reason_draft()
        self.reason_status = None
        target_id = self.store.undo()
        if target_id:
            self.panel._reason_key = None
            self.rebuild()
            self.fill_manual_table()
            if target_id.startswith("image:"):
                image = self.w.folder / target_id[6:]
                if image in self.w.images:
                    self.w.image_table.setCurrentCell(self.w.images.index(image), 0)
            elif target_id in self.targets:
                target = self.targets[target_id]
                image = self.w.folder / target.image
                if image in self.w.images:
                    self.w.image_table.setCurrentCell(self.w.images.index(image), 0)
                if target.role != self.phase:
                    if target.role == "manual":
                        self.start_review()
                    else:
                        self.leave_review()
                self.select(target_id)
            else:
                self.image_changed()
            if self.current:
                self.reason_status = ("false_negative" if self.current.role == "manual" else
                                      "false_positive" if (self.store.get(self.current.id) or {}).get("reason_pending") else None)
            self.w._update_overlays()
            self.changed()

    def move_target(self, direction: int) -> None:
        queue = self.queue()
        if not queue or self.w.busy:
            return
        index = next((i for i, t in enumerate(queue) if t.id == self.current_id), 0)
        self.select(queue[max(0, min(len(queue) - 1, index + direction))].id)

    def next_unreviewed(self, across_images: bool = False) -> None:
        if self.w.busy:
            return
        queue = self.queue()
        start = next((i for i, t in enumerate(queue) if t.id == self.current_id), -1)
        ordered = queue[start + 1:] + queue[:start + 1]
        candidate = next((t for t in ordered if not self.target_completed(t)), None)
        if candidate:
            self.select(candidate.id)
            return
        if across_images:
            row = self.w.image_table.currentRow()
            indices = list(range(row + 1, len(self.w.images))) + list(range(0, row))
            for index in indices:
                if self.w.image_table.isRowHidden(index):
                    continue
                candidate = next((t for t in self.role_targets("algorithm", self.w.images[index]) if not self.target_completed(t)), None)
                if candidate:
                    self.w.image_table.setCurrentCell(index, 0)
                    self.select(candidate.id)
                    return
        self.w.statusBar().showMessage("本图当前范围已标完；可用左右方向键切图，或 N 查找下一未标目标")

    def scope_changed(self, scope: str) -> None:
        if scope == "manual":
            self.start_review()
        else:
            self.leave_review()

    def all_positive(self) -> None:
        if not self.panel.all_positive_button.isEnabled():
            return
        self.flush_note()
        targets = self.role_targets("algorithm")
        with self.store.transaction(self.current_id or targets[0].id):
            for target in targets:
                record = self.store.get(target.id) or {}
                self.store.set_annotation(target, "true_positive", note=record.get("note", ""))
        self.reason_status = None
        self.changed()
        self.start_review()
        self.w.image_view.setFocus()

    def start_review(self) -> bool:
        if self.w.busy or self.w.image_view.path is None or self.w.image_view.draw_mode:
            return False
        self.flush_note()
        self.flush_reason_draft()
        if self.phase != "manual":
            self._previous_show_all = self.panel.show_all_targets.isChecked()
        self.phase = "manual"
        self.current_id = None
        self.reason_status = None
        self.panel.show_all_targets.setChecked(True)
        self.image_changed()
        if self.miss_review_completed():
            image = self.w.current_image().relative_to(self.w.folder).as_posix()
            self.store.set_image_review(image, self.algorithm_signature(), False)
            self.changed()
        self.w.image_view.setFocus()
        QTimer.singleShot(0, self.scroll_to_review)
        return True

    def scroll_to_review(self) -> None:
        if self.phase == "manual" and self.w.isVisible():
            self.panel.ensureWidgetVisible(self.panel.review_frame, 0, 8)

    def leave_review(self, render: bool = True) -> None:
        self.flush_note()
        if self.w.image_view.draw_mode:
            self.w.image_view.set_draw_mode(False)
        self.phase = "algorithm"
        self.current_id = None
        self.reason_status = None
        if self._previous_show_all is not None:
            self.panel.show_all_targets.setChecked(self._previous_show_all)
            self._previous_show_all = None
        if render:
            self.image_changed()
            self.panel.verticalScrollBar().setValue(0)

    def toggle_review(self) -> None:
        self.leave_review() if self.phase == "manual" else self.start_review()

    def finish_review(self) -> None:
        if self.phase != "manual" or not self.panel.finish_review_button.isEnabled():
            return
        self.flush_note()
        if self.current and self.reason_status == "false_negative":
            self.choose_reason(self.panel.reason_combo.currentText().strip())
        if any(not self.target_completed(t) for t in self.role_targets("manual")):
            self.w.statusBar().showMessage("还有补漏框未确认，请确认其漏检原因或删除该框。")
            return
        image = self.w.current_image().relative_to(self.w.folder).as_posix()
        self.store.set_image_review(image, self.algorithm_signature(), True)
        self.changed()
        if self.save(explicit=True):
            self.next_review_image()

    def next_review_image(self) -> None:
        if self.w.busy:
            return
        row = self.w.image_table.currentRow()
        indices = list(range(row + 1, len(self.w.images))) + list(range(0, row + 1))
        for index in indices:
            if not self.w.image_table.isRowHidden(index) and not self.review_completed(self.w.images[index]):
                self.w.image_table.setCurrentCell(index, 0)
                if self.miss_review_completed() and not self.algorithm_ready():
                    self.leave_review()
                    self.w.statusBar().showMessage("04 已完成，03 尚未完成，请继续算法审核。")
                    self.w.image_view.setFocus()
                    return
                if self.algorithm_ready() and self.reason_status != "false_positive":
                    self.start_review()
                self.w.image_view.setFocus()
                return
        self.w.statusBar().showMessage("所有可见图片已完成漏检复核。")
        self.w.image_view.setFocus()

    def set_hold(self, source: str, enabled: bool) -> None:
        if enabled and not self.w.busy and self.w.image_view.path is not None:
            self._hold_sources.add(source)
        else:
            self._hold_sources.discard(source)
        self.panel.hold_button.setDown(bool(self._hold_sources))
        self.highlight()

    def release_hold(self) -> None:
        self._hold_sources.clear()
        self._hold_key = None
        self.panel.hold_button.setDown(False)
        self.highlight()

    def toggle_draw(self) -> None:
        if self.phase != "manual":
            self.w.statusBar().showMessage("补漏框仅在 04 中使用；请先进入漏检复核。")
            return
        if not self.w.busy:
            self.w.image_view.set_draw_mode(not self.w.image_view.draw_mode)

    def toggle_ground_truth(self) -> None:
        if not self.w.busy:
            self.panel.gt_button.click()

    def draw_mode_changed(self, enabled: bool) -> None:
        self.panel.review_draw_button.setChecked(enabled)
        self.render()
        if enabled:
            self.w.statusBar().showMessage("在图片上按住左键拖出漏检框；Esc 取消补框")

    def add_manual(self, box) -> None:
        path = self.w.current_image()
        if path is None or self.phase != "manual":
            return
        category, accepted = QInputDialog.getText(self.w, "补充漏检目标", "目标种类（可留空）：")
        if not accepted:
            return
        target = ReviewTarget.manual(path.relative_to(self.w.folder).as_posix(), box, category.strip() or None)
        self.store.set_annotation(target, "false_negative")
        self.rebuild()
        self.fill_manual_table()
        self.w._update_overlays()
        self.select(target.id)
        self.reason_status = "false_negative"
        self.changed()
        QTimer.singleShot(0, self.scroll_to_review)

    def delete_manual(self) -> None:
        if self.current and self.current.role == "manual" and not self.w.busy:
            self.flush_note()
            self.store.delete_manual(self.current.id)
            self.current_id = None
            self.reason_status = None
            self.rebuild()
            self.fill_manual_table()
            self.w._update_overlays()
            self.image_changed()
            self.changed()

    def apply_config(self, config: ReviewConfig) -> None:
        shortcuts = {}
        reserved = {"Ctrl+O", "Ctrl+J", "Ctrl+Shift+J", "F"}
        for operation, text in config.shortcuts.items():
            key = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
            normalized = key.toString(QKeySequence.SequenceFormat.PortableText)
            if normalized.split("+")[-1] == "Enter":
                normalized = normalized[:-5] + "Return"
            if key.isEmpty() or key.count() != 1 or not normalized or normalized in reserved or normalized in shortcuts:
                raise ValueError(f"快捷键无效、重复或与导入 / 预览冲突：{text}")
            shortcuts[normalized] = operation
        self.release_hold()
        self.config, self.shortcuts = config, shortcuts
        self.panel.set_config(config)
        self.panel.auto_next.setChecked(config.auto_advance)
        self.w.previous_button.setText(f"上一张 {display_key(config.shortcuts['previous_image'])}")
        self.w.next_button.setText(f"下一张 {display_key(config.shortcuts['next_image'])}")

    def import_config(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self.w, "导入标注原因与快捷键配置", str(self.config.path or ""), "JSON 文件 (*.json)")
        if not filename:
            return
        try:
            config = ReviewConfig.load(Path(filename))
            self.apply_config(config)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.w, "原因配置未导入", str(exc))
            return
        self.w.settings.setValue("review/reasons_path", str(config.path))
        self.render()

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide) and watched is self.w:
            self.release_hold()
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self.release_hold()
        if event.type() == QEvent.Type.UngrabMouse and watched is self.panel.hold_button:
            self.set_hold("mouse", False)
        if event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat() and self._hold_key is not None:
            if event.key() == self._hold_key or event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
                self._hold_key = None
                self.set_hold("key", False)
                return True
        if event.type() != QEvent.Type.KeyPress or self.w.busy or not self.w.isVisible():
            return False
        if not isinstance(watched, QWidget) or watched.window() is not self.w or QApplication.activeModalWidget():
            return False
        combinations = [event.keyCombination()]
        if event.modifiers() & Qt.KeyboardModifier.KeypadModifier:
            # Try explicit Num+ shortcuts first, then treat keypad keys like their main-keyboard equivalents.
            combinations.append(QKeyCombination(event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier,
                                                Qt.Key(event.key())))
        operation = None
        for combination in combinations:
            key = QKeySequence(combination).toString(QKeySequence.SequenceFormat.PortableText)
            if key.split("+")[-1] == "Enter":
                key = key[:-5] + "Return"
            operation = self.shortcuts.get(key)
            if operation is not None:
                break
        if operation is None:
            return False
        focused = QApplication.focusWidget()
        editing = isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)) or (
            isinstance(focused, QComboBox) and focused.isEditable())
        command_modifier = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        if editing and operation not in ("save", "back") and not (command_modifier and operation in ("finish_review", "all_positive")):
            return False
        if operation == "hold_all_algorithm":
            if not event.isAutoRepeat():
                self._hold_key = event.key()
                self.set_hold("key", True)
            return True
        if event.isAutoRepeat() and operation not in ("previous_target", "next_target", "previous_image", "next_image"):
            return True
        if self.w.image_view.draw_mode and operation in (
                "confirm", "clear", "undo", "all_positive", "start_review", "finish_review",
                *(f"decision_{index}" for index in range(1, 10))):
            return True
        if operation.startswith("decision_"):
            number = int(operation.rsplit("_", 1)[1])
            if self.reason_status:
                reasons = self.config.reasons.get(self.reason_status, ())
                if number <= len(reasons):
                    self.choose_reason(reasons[number - 1])
            elif number <= 2:
                self.choose_status(tuple(STATUSES)[number - 1])
        elif operation == "back":
            self.flush_note()
            self.flush_reason_draft()
            if self.w.image_view.draw_mode:
                self.w.image_view.set_draw_mode(False)
            else:
                self.reason_status = None
                self.render()
            self.w.image_view.setFocus()
        else:
            actions = {"previous_target": lambda: self.move_target(-1), "next_target": lambda: self.move_target(1),
                "previous_image": lambda: self.w.move_image(-1), "next_image": lambda: self.w.move_image(1),
                "confirm": self.confirm, "clear": self.clear, "next_unreviewed": lambda: self.next_unreviewed(True),
                "draw_missed": self.toggle_draw, "toggle_ground_truth": self.toggle_ground_truth,
                "all_positive": self.all_positive, "start_review": self.toggle_review,
                "finish_review": self.finish_review, "next_review_image": self.next_review_image,
                "undo": self.undo, "save": lambda: self.save(explicit=True)}
            actions[operation]()
        event.accept()
        return True
