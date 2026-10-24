"""Human review data, reason configuration, stable identities and atomic saving."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from .core import Detection, FieldMapping, load_json

STATUSES = {"true_positive": "正检", "false_positive": "误检", "false_negative": "漏检"}
DEFAULT_SHORTCUTS = {**{f"decision_{n}": str(n) for n in range(1, 10)},
    "previous_target": "Up", "next_target": "Down", "previous_image": "Left", "next_image": "Right",
    "confirm": "Return", "back": "Escape", "clear": "Backspace", "next_unreviewed": "N",
    "draw_missed": "B", "toggle_ground_truth": "G", "hold_all_algorithm": "H",
    "all_positive": "Ctrl+1", "start_review": "R", "finish_review": "Ctrl+Return",
    "next_review_image": "M", "undo": "Ctrl+Z", "save": "Ctrl+S"}


@dataclass
class ReviewConfig:
    reasons: dict[str, tuple[str, ...]] = field(default_factory=lambda: {"false_positive": (), "false_negative": ()})
    shortcuts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))
    auto_advance: bool = True
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> ReviewConfig:
        data = load_json(path)
        if not isinstance(data, dict) or data.get("version", 1) != 1:
            raise ValueError("原因配置必须是 version 为 1 的 JSON 对象。")
        reasons = data.get("reasons", data)
        if not isinstance(reasons, dict):
            raise ValueError("reasons 必须包含 false_positive 和 false_negative 原因列表。")
        parsed = {}
        for status, chinese in (("false_positive", "误检"), ("false_negative", "漏检")):
            values = reasons.get(status, reasons.get(chinese, []))
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{chinese}原因必须是非空字符串组成的数组。")
            parsed[status] = tuple(dict.fromkeys(v.strip() for v in values))
        shortcuts = data.get("shortcuts", {})
        if not isinstance(shortcuts, dict) or any(key not in DEFAULT_SHORTCUTS for key in shortcuts):
            raise ValueError("shortcuts 包含未知操作，请参考 config/reasons.json。")
        if any(not isinstance(v, str) or not v.strip() or "," in v for v in shortcuts.values()):
            raise ValueError("快捷键必须是单个按键或组合键，例如 1、Up、Ctrl+S。")
        shortcuts = {**DEFAULT_SHORTCUTS, **{key: value.strip() for key, value in shortcuts.items()}}
        if len({value.casefold() for value in shortcuts.values()}) != len(shortcuts):
            raise ValueError("快捷键不能重复；状态与原因会自动复用 decision_1 至 decision_9。")
        auto = data.get("auto_advance", True)
        if not isinstance(auto, bool):
            raise ValueError("auto_advance 必须为 true 或 false。")
        return cls(parsed, shortcuts, auto, path.resolve())


def source_signature(document: Any, mapping: FieldMapping) -> str:
    payload = json.dumps([document, asdict(mapping)], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewTarget:
    id: str
    image: str  # relative to the selected image folder
    role: str
    box: tuple[float, float, float, float]
    category: Any = None
    score: float | None = None
    source_file: str = ""
    signature: str = ""
    record_index: int = 0
    target_index: int = 0

    @classmethod
    def from_detection(cls, image: str, role: str, source: Path | None, signature: str, target: Detection):
        identity = [image.replace("\\", "/").casefold(), role, signature,
                    target.record_index, target.target_index, target.box, target.category, target.score]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        return cls(key, image, role, target.box, target.category, target.score,
                   str(source or ""), signature, target.record_index, target.target_index)

    @classmethod
    def manual(cls, image: str, box: tuple, category: str | None = None):
        return cls("manual-" + uuid.uuid4().hex, image, "manual", tuple(box), category)

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_target(data: Any, key: str) -> ReviewTarget:
    if not isinstance(data, dict) or data.get("id") != key:
        raise ValueError("标注文件含无效目标身份信息。")
    try:
        target = ReviewTarget(**{**data, "box": tuple(data["box"])})
    except (TypeError, KeyError) as exc:
        raise ValueError("标注文件目标字段不完整。") from exc
    if not isinstance(target.image, str) or not target.image or target.role not in ("algorithm", "ground_truth", "manual"):
        raise ValueError("标注文件目标来源或图片名无效。")
    image = Path(target.image.replace("\\", "/"))
    if image.is_absolute() or PureWindowsPath(target.image).is_absolute() or ".." in image.parts:
        raise ValueError("标注中的图片必须位于当前图片文件夹内。")
    if len(target.box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in target.box):
        raise ValueError("标注文件含无效框坐标。")
    if target.box[2] <= target.box[0] or target.box[3] <= target.box[1]:
        raise ValueError("标注文件含倒置或空框。")
    return target


@dataclass
class ReviewChange:
    focus: str
    targets: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)
    references: dict = field(default_factory=dict)


class AnnotationStore:
    def __init__(self, folder: Path | None = None):
        self.folder = folder.resolve() if folder else None
        self.records: dict[str, dict] = {}
        self.manual_targets: dict[str, ReviewTarget] = {}
        self.dirty = False
        self.image_reviews: dict[str, dict] = {}
        self.reference_annotations: dict[str, dict] = {}
        self.history: list[ReviewChange] = []
        self._transaction: ReviewChange | None = None

    @property
    def path(self) -> Path | None:
        return self.folder / ".detretlabel" / "annotations.json" if self.folder else None

    @classmethod
    def load(cls, folder: Path) -> AnnotationStore:
        store = cls(folder)
        if not store.path.exists():
            return store
        data = load_json(store.path)
        if not isinstance(data, dict) or data.get("version") not in (1, 2):
            raise ValueError("已有标注文件版本或格式无效，已停止载入，避免覆盖。")
        records, manual = data.get("annotations"), data.get("manual_targets")
        if not isinstance(records, dict) or not isinstance(manual, dict):
            raise ValueError("已有标注文件缺少 annotations 或 manual_targets，未覆盖该文件。")
        for key, value in manual.items():
            target = _validate_target(value, key)
            if target.role != "manual":
                raise ValueError("人工补框来源无效。")
            store.manual_targets[key] = target
        for key, record in records.items():
            if not isinstance(record, dict) or record.get("status") not in (*STATUSES, None):
                raise ValueError("已有标注含未知正误漏状态。")
            _validate_target(record.get("target"), key)
            if not isinstance(record.get("reason"), str) or not isinstance(record.get("note"), str):
                raise ValueError("已有标注原因或备注无效。")
            if not isinstance(record.get("reason_pending", False), bool) or not isinstance(record.get("reason_draft", ""), str):
                raise ValueError("已有标注原因草稿格式无效。")
            if record["target"]["role"] == "ground_truth":
                store.reference_annotations[key] = record
            else:
                store.records[key] = record
        references = data.get("reference_annotations", {})
        if not isinstance(references, dict):
            raise ValueError("历史参考标注格式无效。")
        for key, record in references.items():
            if not isinstance(record, dict) or _validate_target(record.get("target"), key).role != "ground_truth":
                raise ValueError("历史参考标注来源无效。")
            store.reference_annotations[key] = record
        reviews = data.get("image_reviews", {})
        if not isinstance(reviews, dict):
            raise ValueError("图片复核状态格式无效。")
        for image, review in reviews.items():
            image_path = Path(image.replace("\\", "/")) if isinstance(image, str) else None
            if (not image or not image_path or not image_path.name or image_path.is_absolute() or PureWindowsPath(image).is_absolute() or ".." in image_path.parts
                    or not isinstance(review, dict) or not isinstance(review.get("completed"), bool)
                    or not isinstance(review.get("algorithm_signature"), str)):
                raise ValueError("图片复核状态无效。")
            store.image_reviews[image] = review
        return store

    def get(self, target_id: str) -> dict | None:
        return self.records.get(target_id)

    def completed(self, target_id: str) -> bool:
        return bool(self.records.get(target_id, {}).get("status"))

    def _remember(self, target_id: str) -> None:
        change = self._transaction or ReviewChange(target_id)
        if target_id not in change.targets:
            change.targets[target_id] = (copy.deepcopy(self.records.get(target_id)), self.manual_targets.get(target_id))
        if self._transaction is None:
            self.history.append(change)

    @contextmanager
    def transaction(self, focus: str):
        if self._transaction is not None:
            yield
            return
        change, was_dirty = ReviewChange(focus), self.dirty
        self._transaction = change
        try:
            yield
        except Exception:
            self._restore(change)
            self.dirty = was_dirty
            raise
        else:
            if change.targets or change.images or change.references:
                self.history.append(change)
        finally:
            self._transaction = None

    def set_image_review(self, image: str, signature: str, completed: bool) -> None:
        old = self.image_reviews.get(image)
        if old and old["algorithm_signature"] == signature and old["completed"] == completed:
            return
        change = self._transaction or ReviewChange("image:" + image)
        if image not in change.images:
            change.images[image] = copy.deepcopy(old)
        if self._transaction is None:
            self.history.append(change)
        self.image_reviews[image] = {"algorithm_signature": signature, "completed": completed,
            "completed_at": datetime.now(timezone(timedelta(hours=8))).isoformat() if completed else None}
        self.dirty = True

    def review_completed(self, image: str, signature: str) -> bool:
        review = self.image_reviews.get(image, {})
        return review.get("completed", False) and review.get("algorithm_signature") == signature

    def invalidate_review(self, image: str) -> None:
        review = self.image_reviews.get(image)
        if review and review["completed"]:
            self.set_image_review(image, review["algorithm_signature"], False)

    def set_annotation(self, target: ReviewTarget, status: str | None, reason: str = "", note: str = "", *,
                       reason_pending: bool = False, reason_draft: str = "") -> None:
        if target.role == "ground_truth":
            raise ValueError("GT 仅供参考，不参与人工决策。")
        if status not in (*STATUSES, None):
            raise ValueError("未知标注状态。")
        reason = reason.strip() if status in ("false_positive", "false_negative") else ""
        reason_pending = bool(reason_pending and target.role == "algorithm" and status == "false_positive")
        reason_draft = reason_draft if reason_pending else ""
        old = self.records.get(target.id)
        if old and (old["status"], old["reason"], old["note"], old.get("reason_pending", False), old.get("reason_draft", "")) == (status, reason, note, reason_pending, reason_draft):
            return
        with self.transaction(target.id):
            self._remember(target.id)
            if target.role == "manual":
                self.manual_targets[target.id] = target
            self.records[target.id] = {"target": target.to_dict(), "status": status, "reason": reason, "note": note,
                "updated_at": datetime.now(timezone(timedelta(hours=8))).isoformat()}
            if reason_pending:
                self.records[target.id].update(reason_pending=True, reason_draft=reason_draft)
            if not old or (old["status"], old["reason"]) != (status, reason):
                self.invalidate_review(target.image)
            self.dirty = True

    def clear(self, target_id: str) -> None:
        if target_id in self.records:
            with self.transaction(target_id):
                self._remember(target_id)
                record = self.records.pop(target_id)
                self.invalidate_review(record["target"]["image"])
                self.dirty = True

    def delete_manual(self, target_id: str) -> None:
        if target_id in self.manual_targets:
            with self.transaction(target_id):
                self._remember(target_id)
                target = self.manual_targets.pop(target_id)
                self.records.pop(target_id, None)
                self.invalidate_review(target.image)
                self.dirty = True

    def _annotation_keys(self, image: str | None = None) -> tuple[list[str], list[str], list[str], list[str]]:
        def matches(name):
            return image is None or name.replace("\\", "/").casefold() == image.replace("\\", "/").casefold()
        return ([key for key, record in self.records.items() if matches(record["target"]["image"])],
                [key for key, target in self.manual_targets.items() if matches(target.image)],
                [name for name in self.image_reviews if matches(name)],
                [key for key, record in self.reference_annotations.items() if matches(record["target"]["image"])])

    def has_annotations(self, image: str | None = None) -> bool:
        if image is None:
            return bool(self.records or self.manual_targets or self.image_reviews or self.reference_annotations)
        return any(self._annotation_keys(image))

    def clear_annotations(self, image: str | None = None, focus_image: str | None = None) -> bool:
        """Reset one image or the entire store, including hidden and historical records, as one undo."""
        records, manual, images, references = self._annotation_keys(image)
        if not any((records, manual, images, references)):
            return False
        focus = image or focus_image
        with self.transaction("image:" + focus if focus else "reset"):
            for key in dict.fromkeys(records + manual):
                self._remember(key)
                self.records.pop(key, None)
                self.manual_targets.pop(key, None)
            for name in images:
                self._transaction.images.setdefault(name, copy.deepcopy(self.image_reviews[name]))
                del self.image_reviews[name]
            for key in references:
                self._transaction.references.setdefault(key, copy.deepcopy(self.reference_annotations[key]))
                del self.reference_annotations[key]
            self.dirty = True
        return True

    def _restore(self, change: ReviewChange) -> None:
        for key, (record, manual) in change.targets.items():
            if record is None:
                self.records.pop(key, None)
            else:
                self.records[key] = record
            if manual is None:
                self.manual_targets.pop(key, None)
            else:
                self.manual_targets[key] = manual
        for image, review in change.images.items():
            if review is None:
                self.image_reviews.pop(image, None)
            else:
                self.image_reviews[image] = review
        for key, record in change.references.items():
            if record is None:
                self.reference_annotations.pop(key, None)
            else:
                self.reference_annotations[key] = record

    def undo(self) -> str | None:
        if not self.history:
            return None
        change = self.history.pop()
        self._restore(change)
        self.dirty = True
        return change.focus

    def save(self) -> None:
        if not self.dirty:
            return
        if self.path is None:
            raise ValueError("请先选择图片文件夹，再保存标注。")
        payload = {"version": 2, "annotations": self.records, "image_reviews": self.image_reviews,
                   "reference_annotations": self.reference_annotations,
                   "manual_targets": {key: value.to_dict() for key, value in self.manual_targets.items()}}
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="annotations-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        self.dirty = False
