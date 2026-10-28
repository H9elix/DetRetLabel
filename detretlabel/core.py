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
from typing import Any, Callable, Iterable

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
    normalized: bool = False
    width_field: str | None = None  # image record; export metadata, excluded from review identity
    height_field: str | None = None


def mapping_conflicts(mapping: FieldMapping) -> list[str]:
    selected = list(mapping.coordinates) if mapping.coordinates else ([mapping.box_field] if mapping.box_field else [])
    selected += [p for p in (mapping.category_field, mapping.score_field) if p]
    if mapping.detections_field is None:
        selected += [p for p in (mapping.image_field, mapping.width_field, mapping.height_field) if p and p != "@key"]
    errors = []
    for index, first in enumerate(selected):
        for second in selected[index + 1:]:
            if first == second or first.startswith(second + "/") or second.startswith(first + "/"):
                errors.append(f"字段用途冲突：{first} 与 {second}。请分别选择图片、框、种类、置信度及图片尺寸字段。")
    if bool(mapping.width_field) != bool(mapping.height_field):
        errors.append("图片宽度和高度字段需要同时映射，或都不映射。")
    if mapping.width_field and mapping.width_field == mapping.height_field:
        errors.append("图片宽度与高度不能映射到同一字段。")
    return list(dict.fromkeys(errors))


def image_dimensions(value: Any) -> tuple[int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("图片宽高必须是两个正整数")
    result = []
    for item in value:
        number = _number(item, "图片宽高")
        if number <= 0 or not number.is_integer():
            raise ValueError("图片宽高必须是正整数")
        result.append(int(number))
    return tuple(result)


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
    width = guess_field(record_fields, ("width", "image_width", "img_width"))
    height = guess_field(record_fields, ("height", "image_height", "img_height"))
    if not detections and (width in coords or height in coords):
        width = height = None  # These are box dimensions, not image dimensions.
    if not width or not height:
        width = height = None
    return FieldMapping(collection, "@key" if collection.kind == "keyed" else
                        (guess_field(record_fields, IMAGE_NAMES) or ""), detections, box_format, box, coords,
                        guess_field(target_fields, TYPE_NAMES), guess_field(target_fields, SCORE_NAMES),
                        width_field=width, height_field=height)


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


def sort_detections(targets: list[Detection]) -> None:
    """Stable display/review order; original JSON positions and identities stay intact."""
    targets.sort(key=lambda target: (target.score is None,
                                     -target.score if target.score is not None else 0.0))


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
    image_sizes: dict[Path, tuple[int, int]] = field(default_factory=dict)
    size_issues: dict[Path, str] = field(default_factory=dict)
    actual_size_images: set[Path] = field(default_factory=set)

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


def parse_detection(raw: Any, mapping: FieldMapping, ri: int, ti: int, *,
                    image_size: tuple[int, int] | None = None) -> Detection:
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
    if mapping.normalized:
        if (image_size is None or len(image_size) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in image_size)):
            raise ValueError("换算归一化坐标需要有效的图片宽高")
        width, height = image_size
        x, y, a, b = x * width, y * height, a * width, b * height
        if not all(map(math.isfinite, (x, y, a, b))) or a <= x or b <= y:
            raise ValueError("归一化坐标换算后的框无效或超出数值范围")
    category = read_pointer(raw, mapping.category_field, None)
    if category is not None and (isinstance(category, bool) or not isinstance(category, (str, int, float))):
        raise ValueError("种类字段必须是名称或数值 ID")
    if isinstance(category, float) and not math.isfinite(category):
        raise ValueError("种类 ID 必须是有限数字")
    if mapping.category_field and category is None:
        raise ValueError("记录缺少映射的种类字段")
    score = None
    if mapping.score_field:
        value = read_pointer(raw, mapping.score_field, None)
        if value is not None:
            score = _number(value, "置信度")
    return Detection((x, y, a, b), category, score, ri, ti, raw)


def import_results(document: Any, mapping: FieldMapping, images: Iterable[Path], *,
                   image_size_loader: Callable[[Path], tuple[int, int]] | None = None) -> ImportResult:
    conflicts = mapping_conflicts(mapping)
    if conflicts:
        raise ValueError("\n".join(conflicts))
    index: dict[str, list[Path]] = defaultdict(list)
    for path in images:
        index[name_key(path.name)].append(path)
    result = ImportResult({}, set(), [], 0, 0)
    image_sizes: dict[Path, tuple[int, int] | str] = {}
    reported_size_errors: set[Path] = set()
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
        metadata_size = None
        if path is not None and mapping.width_field and mapping.height_field:
            try:
                metadata_size = image_dimensions((read_pointer(record, mapping.width_field), read_pointer(record, mapping.height_field)))
                previous = result.image_sizes.get(path)
                if previous and previous != metadata_size:
                    result.size_issues[path] = "同图不同记录的图片宽高不一致；导出前需要确认实际图片尺寸。"
                elif path not in result.size_issues:
                    result.image_sizes[path] = metadata_size
            except ValueError as exc:
                result.size_issues[path] = f"02 的图片宽高字段无效：{exc}；请检查是否误选了目标框的 w / h。"
        size = None
        if mapping.normalized and targets:
            # Unmatched targets are validated in unit coordinates and never retained.
            size = (1, 1)
            if path is not None:
                if path not in image_sizes:
                    try:
                        if image_size_loader is None:
                            raise ValueError("未提供图片尺寸读取器")
                        dimensions = image_size_loader(path)
                        if (len(dimensions) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                or not math.isfinite(v) or v <= 0 for v in dimensions)):
                            raise ValueError("图片宽高无效")
                        image_sizes[path] = dimensions
                        actual_size = image_dimensions(dimensions)
                        if metadata_size and metadata_size != actual_size:
                            result.size_issues[path] = "02 的图片宽高与实际图片不一致，已优先使用实际图片尺寸。"
                        result.image_sizes[path] = actual_size
                        result.actual_size_images.add(path)
                    except (OSError, ValueError) as exc:
                        image_sizes[path] = str(exc)
                size = image_sizes[path]
                if isinstance(size, str):
                    if path not in reported_size_errors:
                        result.issues.append(ImportIssue("图片尺寸无效", short_name, ri, None,
                            f"无法换算归一化坐标：{size}"))
                        reported_size_errors.add(path)
                    continue
        for ti, target in enumerate(targets, 1):
            try:
                detection = parse_detection(target, mapping, ri, ti, image_size=size)
            except ValueError as exc:
                result.issues.append(ImportIssue("目标数据无效", short_name, ri, ti, str(exc)))
            else:
                if path is not None:
                    result.detections[path].append(detection)
    # Import runs in a worker. Cache one order for tables, review queues, boxes and reports;
    # never sort on image selection or mutate the source document used for stable identities.
    for targets in result.detections.values():
        sort_detections(targets)
    return result
