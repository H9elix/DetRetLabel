"""JSON interpretation and matching. This module has no GUI dependencies.

Fields use JSON Pointers (RFC 6901), relative to either an image record or
a detection. Input records are preserved so later annotations can refer back
to the original result without changing its category or confidence.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
IMAGE_NAMES = ("path", "name", "file_name", "filename", "image_path", "image_name", "image", "file")
BOX_NAMES = ("bbox", "box", "bounding_box", "rect", "xyxy", "xywh")
TYPE_NAMES = ("type", "class_name", "category", "label", "class", "category_id", "class_id")
SCORE_NAMES = ("conf", "score", "confidence", "probability", "prob")
DETECTION_NAMES = ("detections", "objects", "predictions", "results", "targets", "annotations", "boxes")
MISSING = object()


def basename(value: str) -> str:
    """Handle paths from both Windows and POSIX, on either platform."""
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def name_key(value: str) -> str:
    # Match Windows filename behavior, retaining the extension.
    return basename(value).casefold()


def pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def read_pointer(value: Any, pointer: str | None, default: Any = MISSING) -> Any:
    if pointer is None:
        return default
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        return default
    try:
        for part in pointer[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict):
                value = value[part]
            elif isinstance(value, list) and part.isdecimal():
                value = value[int(part)]
            else:
                return default
        return value
    except (KeyError, IndexError, TypeError):
        return default


def fields(records: Iterable[Any], prefix: str = "", depth: int = 0) -> list[str]:
    found: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            path = prefix + "/" + pointer_part(str(key))
            found.add(path)
            if isinstance(value, dict) and depth < 5:
                found.update(fields([value], path, depth + 1))
    return sorted(found)


@dataclass(frozen=True)
class Collection:
    pointer: str
    kind: str = "list"  # list, keyed, single

    @property
    def label(self) -> str:
        kind = {"list": "记录列表", "keyed": "以图片名为键", "single": "单张图片记录"}[self.kind]
        return f"{self.pointer or '(JSON 根节点)'} · {kind}"


def discover_collections(document: Any) -> list[Collection]:
    found: list[Collection] = []

    def walk(value: Any, pointer: str, depth: int) -> None:
        if isinstance(value, list):
            if not value or any(isinstance(item, dict) for item in value[:50]):
                found.append(Collection(pointer))
            return
        if not isinstance(value, dict) or depth > 5:
            return
        if value and any(Path(basename(str(key))).suffix.lower() in IMAGE_EXTENSIONS for key in value):
            found.append(Collection(pointer, "keyed"))
        if any(isinstance(item, str) and (key.lower() in IMAGE_NAMES or
               Path(basename(item)).suffix.lower() in IMAGE_EXTENSIONS) for key, item in value.items()):
            found.append(Collection(pointer, "single"))
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                walk(item, pointer + "/" + pointer_part(key), depth + 1)

    walk(document, "", 0)
    return found


def iter_records(document: Any, collection: Collection) -> Iterable[tuple[str | None, Any]]:
    data = read_pointer(document, collection.pointer)
    if collection.kind == "list" and isinstance(data, list):
        yield from ((None, item) for item in data)
    elif collection.kind == "keyed" and isinstance(data, dict):
        for key, value in data.items():
            yield key, {"detections": value} if isinstance(value, list) else value
    elif collection.kind == "single" and isinstance(data, dict):
        yield None, data
    else:
        raise ValueError("所选记录节点与数据结构不符，请重新选择记录位置。")


def sample_records(document: Any, collection: Collection, limit: int = 50) -> list[Any]:
    from itertools import islice
    return [record for _, record in islice(iter_records(document, collection), limit)]


def sample_targets(records: list[Any], pointer: str | None, limit: int = 100) -> list[Any]:
    from itertools import islice
    if pointer is None:
        return records[:limit]
    def targets():
        for record in records:
            value = read_pointer(record, pointer)
            if isinstance(value, list):
                yield from value
    return list(islice(targets(), limit))


def _last(pointer: str) -> str:
    return pointer.rsplit("/", 1)[-1].lower()


def guess_field(options: list[str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        candidates = [option for option in options if _last(option) == alias]
        if candidates:
            return min(candidates, key=lambda item: (item.count("/"), item))
    return None


def target_list_fields(records: list[Any]) -> list[str]:
    """Avoid interpreting an empty bbox array as an empty detection list."""
    result = []
    for pointer in fields(records):
        arrays = [read_pointer(record, pointer) for record in records
                  if isinstance(read_pointer(record, pointer), list)]
        if not arrays:
            continue
        has_objects = any(any(isinstance(item, dict) for item in value[:20]) for value in arrays)
        all_empty = all(not value for value in arrays)
        if has_objects or _last(pointer) in DETECTION_NAMES or (all_empty and _last(pointer) not in BOX_NAMES):
            result.append(pointer)
    return result


@dataclass(frozen=True)
class FieldMapping:
    collection: Collection
    image_field: str = "@key"
    detections_field: str | None = None  # None: the image record is a detection
    box_format: str = "xyxy"
    box_field: str | None = None
    coordinates: tuple[str, ...] = ()
    category_field: str | None = None
    score_field: str | None = None


def guess_mapping(document: Any, collection: Collection, detections: str | None | object = MISSING) -> FieldMapping:
    records = sample_records(document, collection)
    record_fields = fields(records)
    if detections is MISSING:
        array_fields = target_list_fields(records)
        detections = guess_field(array_fields, DETECTION_NAMES)
        if detections is None and len(array_fields) == 1:
            detections = array_fields[0]
    targets = sample_targets(records, detections)
    target_fields = fields(targets)
    box = guess_field(target_fields, BOX_NAMES)
    coords: tuple[str, ...] = ()
    box_format = "xywh" if box and _last(box) == "xywh" else "xyxy"
    # Explicit named coordinates are safer than inferring an array's meaning.
    for aliases, fmt in ((('xmin', 'ymin', 'xmax', 'ymax'), 'xyxy'),
                         (('x1', 'y1', 'x2', 'y2'), 'xyxy'),
                         (('x', 'y', 'w', 'h'), 'xywh'),
                         (('x', 'y', 'width', 'height'), 'xywh')):
        paths = tuple(guess_field(target_fields, (alias,)) for alias in aliases)
        if all(paths):
            coords, box, box_format = paths, None, fmt
            break
    return FieldMapping(collection, "@key" if collection.kind == "keyed" else
                        (guess_field(record_fields, IMAGE_NAMES) or ""), detections, box_format, box, coords,
                        guess_field(target_fields, TYPE_NAMES), guess_field(target_fields, SCORE_NAMES))


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列：{exc.msg}") from exc
    except UnicodeError as exc:
        raise ValueError("JSON 需要使用 UTF-8 编码（支持 UTF-8 BOM）。") from exc


def scan_images(folder: Path, extensions: Iterable[str], recursive: bool = False) -> list[Path]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise ValueError("图片文件夹不存在或无法访问。")
    allowed = {extension.lower() for extension in extensions}
    result: list[Path] = []
    # os.walk's onerror makes inaccessible subfolders visible, rather than silently dropping images.
    import os
    def onerror(error: OSError) -> None:
        raise error
    for directory, subdirs, files in os.walk(folder, onerror=onerror, followlinks=False):
        for filename in files:
            if Path(filename).suffix.lower() in allowed:
                result.append(Path(directory) / filename)
        if not recursive:
            subdirs.clear()
    import re
    def natural_key(path: Path) -> list[tuple[int, Any]]:
        return [(0, int(part)) if part.isdecimal() else (1, part.casefold())
                for part in re.split(r"(\d+)", path.relative_to(folder).as_posix())]
    return sorted(result, key=natural_key)


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]  # canonical xyxy, original values retained in raw
    category: Any
    score: float | None
    record_index: int
    target_index: int
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class ImportIssue:
    kind: str
    image_name: str
    record_index: int
    target_index: int | None
    message: str


@dataclass
class ImportResult:
    detections: dict[Path, list[Detection]]
    referenced: set[Path]
    issues: list[ImportIssue]
    record_count: int
    target_count: int

    @property
    def matched_target_count(self) -> int:
        return sum(map(len, self.detections.values()))

    @property
    def issue_counts(self) -> Counter:
        return Counter(issue.kind for issue in self.issues)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} 必须是有限数字")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} 必须是有限数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须是有限数字")
    return number


def parse_detection(raw: Any, mapping: FieldMapping, ri: int, ti: int) -> Detection:
    if not isinstance(raw, dict):
        raise ValueError("检测目标必须是 JSON 对象")
    if mapping.coordinates:
        if len(mapping.coordinates) != 4 or len(set(mapping.coordinates)) != 4:
            raise ValueError("请选择四个不同的坐标字段")
        values = [read_pointer(raw, p) for p in mapping.coordinates]
    else:
        values = read_pointer(raw, mapping.box_field)
        if not isinstance(values, list) or len(values) != 4:
            raise ValueError("框字段必须是含四个数值的数组；字典式坐标请映射四个独立字段")
    x, y, a, b = [_number(value, "框坐标") for value in values]
    if mapping.box_format == "xywh":
        if a <= 0 or b <= 0:
            raise ValueError("xywh 中宽和高必须大于 0")
        a, b = x + a, y + b
    elif mapping.box_format != "xyxy":
        raise ValueError("暂不支持这种框坐标格式")
    if a <= x or b <= y or not all(map(math.isfinite, (a, b))):
        raise ValueError("框的 xmax、ymax 必须分别大于 xmin、ymin，且坐标必须有限")
    category = read_pointer(raw, mapping.category_field, None)
    if category is not None and (isinstance(category, bool) or not isinstance(category, (str, int, float))):
        raise ValueError("种类字段必须是名称或数值 ID")
    if isinstance(category, float) and not math.isfinite(category):
        raise ValueError("种类 ID 必须是有限数字")
    if mapping.category_field and category is None:
        raise ValueError("记录缺少映射的种类字段")
    score = None
    if mapping.score_field:
        score = _number(read_pointer(raw, mapping.score_field), "置信度")
    return Detection((x, y, a, b), category, score, ri, ti, raw)


def import_results(document: Any, mapping: FieldMapping, images: Iterable[Path]) -> ImportResult:
    index: dict[str, list[Path]] = defaultdict(list)
    for path in images:
        index[name_key(path.name)].append(path)
    result = ImportResult({}, set(), [], 0, 0)
    for ri, (key, record) in enumerate(iter_records(document, mapping.collection), 1):
        result.record_count += 1
        if not mapping.image_field:
            raise ValueError("请选择图片名 / 路径字段。")
        image_name = key if mapping.image_field == "@key" else read_pointer(record, mapping.image_field)
        if not isinstance(image_name, str) or not basename(image_name):
            result.issues.append(ImportIssue("图片字段无效", "", ri, None, "图片名 / 路径字段缺失或不是非空字符串"))
            continue
        short_name = basename(image_name)
        targets = [record] if mapping.detections_field is None else read_pointer(record, mapping.detections_field)
        if not isinstance(targets, list):
            result.issues.append(ImportIssue("目标列表无效", short_name, ri, None, "映射的目标列表缺失或不是数组"))
            continue
        if targets and not mapping.box_field and not mapping.coordinates:
            raise ValueError("记录中存在检测目标，请选择框数组字段或四个坐标字段。")
        result.target_count += len(targets)
        matches = index.get(name_key(image_name), [])
        path = matches[0] if len(matches) == 1 else None
        if not matches:
            result.issues.append(ImportIssue("图片未匹配", short_name, ri, None,
                                "当前图片集合中没有同名文件（可能被格式筛选排除）"))
        elif len(matches) > 1:
            result.issues.append(ImportIssue("同名歧义", short_name, ri, None,
                                "存在多个同名图片，未自动关联：" + "；".join(str(p) for p in matches)))
        else:
            result.referenced.add(path)
            result.detections.setdefault(path, [])
        for ti, target in enumerate(targets, 1):
            try:
                detection = parse_detection(target, mapping, ri, ti)
            except ValueError as exc:
                result.issues.append(ImportIssue("目标数据无效", short_name, ri, ti, str(exc)))
            else:
                if path is not None:
                    result.detections[path].append(detection)
    return result
