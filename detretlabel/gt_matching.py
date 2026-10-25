"""Display-only GT suggestions for one algorithm target. No annotation decisions."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event


@dataclass(frozen=True)
class GTCandidate:
    index: int  # one-based table / overlay index
    box: tuple[float, float, float, float]
    category: str | None
    original_order: tuple[int, int] | None = None  # JSON record / target, independent of display order


@dataclass(frozen=True)
class GTMatch:
    same_index: int = 0
    same_iou: float = 0.0
    best_index: int = 0
    best_iou: float = 0.0
    best_same: bool = False

    @property
    def mismatch_index(self) -> int:
        return self.best_index if not self.best_same else 0


def box_iou(left: tuple, right: tuple) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = width * height
    if not intersection:
        return 0.0
    union = ((left[2] - left[0]) * (left[3] - left[1]) +
             (right[2] - right[0]) * (right[3] - right[1]) - intersection)
    return intersection / union if union > 0 else 0.0


def match_gt(box: tuple, category: str | None, candidates: tuple[GTCandidate, ...],
             cancel: Event | None = None) -> GTMatch | None:
    same_index, same_iou, best_index, best_iou, best_same = 0, 0.0, 0, 0.0, False
    same_order = best_order = None
    for number, candidate in enumerate(candidates):
        if number % 128 == 0 and cancel is not None and cancel.is_set():
            return None
        iou = box_iou(box, candidate.box)
        same = bool(category and candidate.category and category == candidate.category)
        original_order = candidate.original_order if candidate.original_order is not None else (number, 0)
        if iou > best_iou or (iou > 0 and iou == best_iou and
                             (best_order is None or original_order < best_order)):
            best_index, best_iou, best_same = candidate.index, iou, same
            best_order = original_order
        if same and (iou > same_iou or (iou > 0 and iou == same_iou and
                                       (same_order is None or original_order < same_order))):
            same_index, same_iou = candidate.index, iou
            same_order = original_order
    return GTMatch(same_index, same_iou, best_index, best_iou, best_same)
