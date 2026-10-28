"""Mouse and keyboard controls for the two-stage human review workflow."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .annotation import ReviewConfig, ReviewTarget, STATUSES
from .widgets import ElidedLabel


def display_key(key: str) -> str:
    names = {"Up": "↑", "Down": "↓", "Left": "←", "Right": "→", "Return": "Enter", "Escape": "Esc", "Space": "空格"}
    return "+".join(names.get(part, part) for part in key.split("+"))


class DecisionWheel(QWidget):
    selected = Signal(str)
    hold_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(274, 207)
        self.setMaximumHeight(215)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.center = QLabel("— / —\n当前目标", self)
        self.center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center.setStyleSheet("background:#eef4ff;border:2px solid #b8cdf4;border-radius:39px;font-weight:600;")
        self.buttons = {}
        for status, color in (("true_positive", "#13795b"), ("false_positive", "#c73535")):
            button = QPushButton(self)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setStyleSheet(f"QPushButton {{ color:{color};background:white;border:2px solid {color};"
                                 "border-radius:25px;padding:0;font-weight:600; }"
                                 f"QPushButton:checked {{ color:white;background:{color}; }}"
                                 "QPushButton:disabled { color:#a6afbd;border-color:#cbd5e1;background:#f0f2f5; }")
            button.clicked.connect(lambda checked=False, s=status: self.selected.emit(s))
            self.buttons[status] = button
        self.gt_button = QPushButton(self)
        self.gt_button.setCheckable(True)
        self.gt_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.gt_button.setStyleSheet("QPushButton { background:white;border:1px solid #9caebd;"
                                    "border-radius:8px;padding:0;font-weight:600; }"
                                    "QPushButton:checked { color:#075e54;background:#ccfbef;border-color:#169b89; }"
                                    "QPushButton:disabled { color:#a6afbd;background:#f0f2f5;border-color:#cbd5e1; }")
        self.hold_button = QPushButton(self)
        self.hold_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hold_button.setStyleSheet("QPushButton { background:white;border:1px solid #d5b27b;"
                                      "border-radius:8px;padding:0;font-weight:600; }"
                                      "QPushButton:pressed { background:#fff0d7;border-color:#e08a15; }")
        self.hold_button.pressed.connect(lambda: self.hold_changed.emit(True))
        self.hold_button.released.connect(lambda: self.hold_changed.emit(False))
        self.set_keys(ReviewConfig(), False)

    def set_keys(self, config: ReviewConfig, reason_mode: bool) -> None:
        for index, (status, button) in enumerate(self.buttons.items(), 1):
            key = display_key(config.shortcuts[f"decision_{index}"])
            button.setText(f"{STATUSES[status]}\n{key}")
            button.setToolTip((f"点击选择{STATUSES[status]}。当前按键用于下方原因，"
                               f"按 {display_key(config.shortcuts['back'])} 返回状态快捷键。") if reason_mode else
                              f"点击或按 {key} 选择{STATUSES[status]}")

    def set_current(self, position: int, total: int, status: str | None) -> None:
        self.center.setText(f"{position or '—'} / {total or '—'}\n当前目标")
        for key, button in self.buttons.items():
            button.setEnabled(total > 0)
            button.setChecked(key == status)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cx = self.width() / 2
        self.center.setGeometry(round(cx - 40), 67, 80, 80)
        for status, x, y in (("true_positive", cx - 126, 139), ("false_positive", cx + 40, 139)):
            self.buttons[status].setGeometry(round(x), y, 86, 61)
        self.hold_button.setGeometry(0, 4, 84, 50)
        self.gt_button.setGeometry(self.width() - 84, 4, 84, 50)


class AnnotationPanel(QScrollArea):
    status_selected = Signal(str)
    reason_selected = Signal(str)
    confirm_requested = Signal()
    note_edited = Signal(str)
    scope_changed = Signal(str)
    overlay_options_changed = Signal()
    import_config = Signal()
    save_requested = Signal()
    undo_requested = Signal()
    previous_target = Signal()
    next_target = Signal()
    next_unreviewed = Signal()
    draw_requested = Signal()
    delete_manual_requested = Signal()
    clear_requested = Signal()
    all_positive_requested = Signal()
    review_requested = Signal()
    finish_review_requested = Signal()
    next_review_requested = Signal()
    hold_changed = Signal(bool)
    export_requested = Signal()
    json_export_requested = Signal()
    clear_image_requested = Signal()
    clear_all_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        # Reserve the scrollbar width so expanding reasons cannot reflow the controls above.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(300)
        self.setMaximumWidth(350)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.config = ReviewConfig()
        self._reason_key = None
        body = QWidget()
        self.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(9, 0, 0, 0)
        layout.setSpacing(7)
        title = QLabel("03  算法结果审核")
        title.setObjectName("sourceTitle")
        layout.addWidget(title)
        toolbar = QHBoxLayout()
        self.config_button = QPushButton("原因配置…")
        self.config_button.clicked.connect(self.import_config)
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save_requested)
        self.undo_button = QPushButton("撤销")
        self.undo_button.clicked.connect(self.undo_requested)
        self.export_button = QPushButton("导出 Excel…")
        self.export_button.setToolTip("导出全部已加载图片的 Excel 复核快照；正检、误检、漏检各有独立概览、按种类统计和列表，同时保留未审核与图片进度。")
        self.export_button.clicked.connect(self.export_requested)
        for button in (self.config_button, self.undo_button):
            button.setObjectName("sourceAction")
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.json_export_button = QPushButton("导出有效目标json")
        self.json_export_button.setObjectName("sourceAction")
        self.json_export_button.setToolTip("独立导出正检和人工补漏：固定十个平铺字段、归一化 x/y/w/h、八位小数；导出前检查漏映射和尺寸。")
        self.json_export_button.clicked.connect(self.json_export_requested)
        status_row = QHBoxLayout()
        self.save_state = ElidedLabel("标注自动保存到 .detretlabel/annotations.json")
        self.save_state.setObjectName("muted")
        self.help_label = QLabel("快捷键说明")
        self.help_label.setObjectName("muted")
        self.help_label.setCursor(Qt.CursorShape.WhatsThisCursor)
        status_row.addWidget(self.help_label)
        layout.addLayout(status_row)
        self.export_frame = QFrame()
        self.export_frame.setObjectName("sourcePanel")
        export_layout = QVBoxLayout(self.export_frame)
        export_layout.setContentsMargins(9, 7, 9, 7)
        export_layout.setSpacing(5)
        self.export_title = QLabel("05  保存与导出")
        self.export_title.setObjectName("sourceTitle")
        export_layout.addWidget(self.export_title)
        export_buttons = QHBoxLayout()
        for button in (self.save_button, self.export_button):
            button.setObjectName("sourceAction")
            export_buttons.addWidget(button)
        export_layout.addLayout(export_buttons)
        export_layout.addWidget(self.json_export_button)
        export_layout.addWidget(self.save_state)
        self.export_frame.setToolTip("独立的全局操作区，可在 03 / 04 任意阶段保存和导出；不要求完成当前图片。")
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("03 审核算法目标（GT 仅参考）", "algorithm")
        self.scope_combo.addItem("04 漏检复核（人工补框）", "manual")
        self.scope_combo.currentIndexChanged.connect(lambda: self.scope_changed.emit(self.scope_combo.currentData()))
        layout.addWidget(self.scope_combo)
        self.show_all_targets = QCheckBox("叠加显示全部目标")
        self.show_all_targets.setToolTip("仅控制算法结果：勾选后显示本图全部算法框；关闭后显示当前算法目标，以及按保留开关显示的已审核目标。GT 由环形区右上角的按钮独立控制；人工补框始终显示。")
        self.show_all_targets.toggled.connect(self._overlay_options_changed)
        layout.addWidget(self.show_all_targets)
        self.keep_reviewed_targets = QCheckBox("保留显示已审核目标框及标签")
        self.keep_reviewed_targets.setChecked(True)
        self.keep_reviewed_targets.setToolTip("独立保留本图已完成审核的算法目标框及标签，不要求开启全部目标或非当前标签。误检原因待确认的目标不计入已审核；标签内容遵循算法框样式。")
        self.keep_reviewed_targets.toggled.connect(self.overlay_options_changed)
        layout.addWidget(self.keep_reviewed_targets)
        self.show_noncurrent_labels = QCheckBox("显示非当前标注目标的标签")
        self.show_noncurrent_labels.setToolTip("控制其他可见算法框的非当前目标标签，包括按住临时显示的算法框；不依赖“叠加显示全部目标”开关。已审核目标可由保留开关独立显示标签。标签内容遵循算法框样式。")
        self.show_noncurrent_labels.toggled.connect(self.overlay_options_changed)
        layout.addWidget(self.show_noncurrent_labels)
        self.auto_next = QCheckBox("完成后到本图下一未标目标")
        self.auto_next.setChecked(True)
        layout.addWidget(self.auto_next)
        actions = QGridLayout()
        actions.setSpacing(5)
        self.previous_button = QPushButton("上个目标 ↑")
        self.next_button = QPushButton("下个目标 ↓")
        self.unreviewed_button = QPushButton("下一未标 N")
        self.delete_button = QPushButton("删除补框")
        self.delete_button.setToolTip("删除当前正在编辑的人工补框；需编辑其他补框时，先双击右侧人工补框行。")
        self.clear_button = QPushButton("清除标注")
        self.clear_button.setToolTip("只清除当前目标的结论、原因和备注；人工补框本身保留。")
        self.previous_button.clicked.connect(self.previous_target)
        self.next_button.clicked.connect(self.next_target)
        self.unreviewed_button.clicked.connect(self.next_unreviewed)
        self.delete_button.clicked.connect(self.delete_manual_requested)
        self.clear_button.clicked.connect(self.clear_requested)
        for index, button in enumerate((self.previous_button, self.next_button, self.unreviewed_button)):
            button.setObjectName("sourceAction")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            actions.addWidget(button, index // 3, index % 3)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        target_actions = QHBoxLayout()
        for button in (self.delete_button, self.clear_button):
            button.setObjectName("sourceAction")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            target_actions.addWidget(button)
        layout.addLayout(target_actions)
        self.all_positive_button = QPushButton()
        self.all_positive_button.setObjectName("sourceAction")
        self.all_positive_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.all_positive_button.clicked.connect(self.all_positive_requested)
        layout.addWidget(self.all_positive_button)
        self.summary_frame = QFrame()
        self.summary_frame.setObjectName("sourcePanel")
        summary = QVBoxLayout(self.summary_frame)
        summary.setContentsMargins(8, 7, 8, 7)
        summary.setSpacing(5)
        target_row = QHBoxLayout()
        self.target_info = ElidedLabel("选择图片与目标后开始标注")
        target_row.addWidget(self.target_info, 1)
        self.progress_label = QLabel("本图已标注 0 / 0")
        self.progress_label.setObjectName("muted")
        target_row.addWidget(self.progress_label)
        summary.addLayout(target_row)
        self.current_result = ElidedLabel("当前结果：未标注")
        summary.addWidget(self.current_result)
        self.note = QLineEdit()
        self.note.setPlaceholderText("备注（可选，自动保存）")
        self.note.textEdited.connect(self.note_edited)
        self.note.returnPressed.connect(self.confirm_requested)
        summary.addWidget(self.note)
        layout.addWidget(self.summary_frame)
        self.wheel = DecisionWheel()
        self.wheel.selected.connect(self.status_selected)
        self.gt_button = self.wheel.gt_button
        self.hold_button = self.wheel.hold_button
        self.wheel.hold_changed.connect(self.hold_changed)
        self.gt_button.toggled.connect(self.overlay_options_changed)
        layout.addWidget(self.wheel)
        self.reason_frame = QFrame()
        reason_layout = QVBoxLayout(self.reason_frame)
        reason_layout.setContentsMargins(0, 0, 0, 0)
        self.reason_title = QLabel("原因（可选）")
        self.reason_title.setObjectName("sourceTitle")
        reason_layout.addWidget(self.reason_title)
        self.stage = QLabel()
        self.stage.setWordWrap(True)
        self.stage.setObjectName("muted")
        reason_layout.addWidget(self.stage)
        self.reason_buttons_widget = QWidget()
        self.reason_grid = QGridLayout(self.reason_buttons_widget)
        self.reason_grid.setContentsMargins(0, 0, 0, 0)
        self.reason_grid.setSpacing(5)
        reason_layout.addWidget(self.reason_buttons_widget)
        self.reason_buttons: list[QPushButton] = []
        self.reason_combo = QComboBox()
        self.reason_combo.setEditable(True)
        self.reason_combo.setMinimumContentsLength(12)
        self.reason_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.reason_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.reason_combo.lineEdit().setPlaceholderText("选择原因，或输入新原因")
        self.reason_combo.lineEdit().returnPressed.connect(self.confirm_requested)
        reason_layout.addWidget(self.reason_combo)
        self.confirm_button = QPushButton("确认 / 跳过原因  Enter")
        self.confirm_button.clicked.connect(self.confirm_requested)
        reason_layout.addWidget(self.confirm_button)
        layout.addWidget(self.reason_frame)
        self.reason_frame.hide()
        self.main_layout = layout
        self.review_frame = QFrame()
        self.review_frame.setObjectName("sourcePanel")
        self.review_layout = QVBoxLayout(self.review_frame)
        self.review_layout.setContentsMargins(8, 7, 8, 7)
        self.review_layout.setSpacing(5)
        heading = QLabel("04  漏检复核")
        heading.setObjectName("sourceTitle")
        self.review_layout.addWidget(heading)
        self.review_status = QLabel()
        self.review_status.setWordWrap(True)
        self.review_layout.addWidget(self.review_status)
        self.review_button = QPushButton()
        self.review_button.setObjectName("sourceAction")
        self.review_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.review_button.clicked.connect(self.review_requested)
        self.review_layout.addWidget(self.review_button)
        self.review_hint = QLabel("按 B 在图上补漏框；GT 仅供参考，由“显示 GT”独立控制。")
        self.review_hint.setWordWrap(True)
        self.review_hint.setObjectName("muted")
        self.review_layout.addWidget(self.review_hint)
        self.review_draw_button = QPushButton()
        self.review_draw_button.setCheckable(True)
        self.review_draw_button.setObjectName("sourceAction")
        self.review_draw_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.review_draw_button.clicked.connect(self.draw_requested)
        self.review_layout.addWidget(self.review_draw_button)
        self.finish_review_button = QPushButton()
        self.finish_review_button.setObjectName("sourceAction")
        self.finish_review_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.finish_review_button.clicked.connect(self.finish_review_requested)
        self.review_layout.addWidget(self.finish_review_button)
        self.next_review_button = QPushButton()
        self.next_review_button.setObjectName("sourceAction")
        self.next_review_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.next_review_button.clicked.connect(self.next_review_requested)
        self.review_layout.addWidget(self.next_review_button)
        layout.addWidget(self.review_frame)
        reset_row = QHBoxLayout()
        reset_row.setSpacing(5)
        self.clear_image_button = QPushButton("清空本图标注")
        self.clear_all_button = QPushButton("清空全部标注")
        self.clear_image_button.setToolTip("确认后清空本图全部人工结论、原因、备注、人工补框及复核状态；可整体撤销。")
        self.clear_all_button.setToolTip("确认后清空当前图片目录的全部标注，包括筛选隐藏图片和历史记录；可整体撤销。")
        self.clear_image_button.clicked.connect(self.clear_image_requested)
        self.clear_all_button.clicked.connect(self.clear_all_requested)
        for button in (self.clear_image_button, self.clear_all_button):
            button.setObjectName("sourceAction")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setStyleSheet("QPushButton { color:#b43b3b; } QPushButton:disabled { color:#a6afbd; }")
            reset_row.addWidget(button)
        layout.addLayout(reset_row)
        layout.addStretch()
        self.set_config(self.config)

    def _overlay_options_changed(self, checked: bool) -> None:
        self.overlay_options_changed.emit()

    def set_config(self, config: ReviewConfig) -> None:
        self.config = config
        self._reason_key = None
        key = {name: display_key(value) for name, value in config.shortcuts.items()}
        self.config_button.setToolTip(str(config.path or "未选择原因配置文件"))
        self.save_button.setToolTip(f"保存标注 · {key['save']}")
        self.undo_button.setToolTip(f"撤销上一次修改 · {key['undo']}")
        self.previous_button.setText(f"上个目标 {key['previous_target']}")
        self.next_button.setText(f"下个目标 {key['next_target']}")
        self.unreviewed_button.setText(f"下一未标 {key['next_unreviewed']}")
        self.gt_button.setText(f"显示 GT\n{key['toggle_ground_truth']}")
        self.hold_button.setText(f"按住看全部\n{key['hold_all_algorithm']}")
        self.hold_button.setToolTip(f"按住鼠标或 {key['hold_all_algorithm']} 临时显示全部算法框，松开恢复；非当前标签由上方开关控制。")
        self.all_positive_button.setText(f"本图算法全部正检  {key['all_positive']}")
        self.all_positive_button.setToolTip("将本图全部算法目标改为正检，保留备注、清除误检原因；不修改 GT 和人工漏检。可用撤销整体恢复。")
        self.review_draw_button.setText(f"在图上补漏框  {key['draw_missed']}")
        self.next_review_button.setText(f"下一待复核图片  {key['next_review_image']}")
        self.confirm_button.setText(f"确认 / 跳过原因  {key['confirm']}")
        self.help_label.setToolTip(f"状态与原因共用 {key['decision_1']}–{key['decision_9']}\n"
            f"{key['confirm']} 确认 / 跳过原因 · {key['back']} 返回状态\n"
            f"{key['previous_image']} / {key['next_image']} 切图 · {key['clear']} 清除\n"
            f"{key['toggle_ground_truth']} 显示 / 隐藏全部 GT\n"
            f"按住 {key['hold_all_algorithm']} 临时看全部算法框 · {key['all_positive']} 全部正检\n"
            f"{key['start_review']} 进入 / 返回复核 · {key['finish_review']} 完成本图复核\n"
            f"{key['save']} 保存 · {key['undo']} 撤销\n输入框内按键用于输入，不触发标注。")

    def set_context(self, target: ReviewTarget | None, record: dict | None, position: int,
                    total: int, completed: int, reason_status: str | None, phase: str = "algorithm",
                    display_category: str | None = None) -> None:
        record = record or {}
        status = record.get("status")
        self.wheel.set_current(position, total if target else 0, status)
        self.wheel.set_keys(self.config, reason_status is not None)
        self.progress_label.setText(f"{'已确认漏检' if phase == 'manual' else '本图已标注'} {completed} / {total}")
        self.target_info.setText((f"{'人工漏检' if target.role == 'manual' else '算法目标'}"
            f" · {display_category if display_category is not None else target.category if target.category is not None else '未指定种类'}") if target else
            ("请检查图片并补漏，或确认无漏检" if phase == "manual" else "本图没有算法目标，请进行漏检复核"))
        if target:
            self.target_info.setToolTip(f"{self.target_info.full_text}\n原始种类：{target.category}\n{target.image}")
        keys = {name: display_key(value) for name, value in self.config.shortcuts.items()}
        self.stage.setText(f"{keys['decision_1']}–{keys['decision_9']} 选原因 · {keys['confirm']} 确认 / 跳过\n"
                           + (f"{keys['back']} 收起原因 · 双击补框行可重新编辑" if phase == "manual" else
                              f"{keys['back']} 返回状态 · 可点击按钮重选"))
        self.reason_frame.setVisible(reason_status is not None and target is not None)
        self.reason_title.setText(f"{STATUSES.get(reason_status, '')}原因（可选）")
        host = self.review_layout if phase == "manual" else self.main_layout
        if self.reason_frame.parentWidget() is not host.parentWidget():
            self.main_layout.removeWidget(self.reason_frame)
            self.review_layout.removeWidget(self.reason_frame)
            index = self.review_layout.indexOf(self.finish_review_button) if phase == "manual" else self.main_layout.indexOf(self.wheel) + 1
            host.insertWidget(index, self.reason_frame)
        reason_key = (target.id if target else None, reason_status, tuple(self.config.reasons.get(reason_status, ())))
        if reason_key != self._reason_key:
            self._reason_key = reason_key
            while self.reason_grid.count():
                item = self.reason_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.reason_buttons.clear()
            reasons = self.config.reasons.get(reason_status, ())
            for index, reason in enumerate(reasons[:9]):
                button = QPushButton(f"{keys[f'decision_{index + 1}']}  {reason}")
                button.setObjectName("sourceAction")
                button.setMinimumHeight(28)
                button.setMinimumWidth(0)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                button.setToolTip(reason)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.clicked.connect(lambda checked=False, value=reason: self.reason_selected.emit(value))
                self.reason_grid.addWidget(button, index // 2, index % 2)
                self.reason_buttons.append(button)
            self.reason_combo.clear()
            self.reason_combo.addItems(reasons)
            self.reason_combo.setCurrentText(record.get("reason_draft", "") if record.get("reason_pending") else record.get("reason", ""))
        description = STATUSES.get(status, "未标注") + (f" · {record['reason']}" if record.get("reason") else "")
        self.current_result.setText("当前结果：" + description)
        self.note.setText(record.get("note", ""))
        self.note.setEnabled(target is not None)
        self.delete_button.setEnabled(target is not None and target.role == "manual")
        self.clear_button.setEnabled(target is not None and bool(record))
        self.previous_button.setEnabled(total > 0)
        self.next_button.setEnabled(total > 0)
