r"""Download COCO128 and prepare an auditable DetRetLabel integration test pack.

Usage: .venv\Scripts\python.exe -B tools\prepare_test_data.py
Real dataset boxes are converted from normalized YOLO cx/cy/w/h to pixel xyxy.
Confidence values and deliberately injected errors are synthetic test values.
No detector is run and no changes are made to the GUI application.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import shutil
import sys
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path, PurePosixPath

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

from detretlabel.core import Collection, FieldMapping, guess_mapping, import_results, scan_images

ARCHIVE_URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip"
DATASET_URL = "https://docs.ultralytics.com/datasets/detect/coco128/"
TERMS_URL = "https://cocodataset.org/#termsofuse"
CLASS_NAMES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
    "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_archive(path: Path) -> None:
    if path.exists() and zipfile.is_zipfile(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "DetRetLabel-TestData/0.1"})
    partial = path.with_suffix(".part")
    with urllib.request.urlopen(request, timeout=45) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    if not zipfile.is_zipfile(partial):
        raise ValueError("Downloaded file is not a ZIP archive")
    partial.replace(path)


def background_box(record: dict) -> list[float] | None:
    width, height = record["width"], record["height"]
    bw, bh = width * .10, height * .10
    for yi in range(9):
        for xi in range(9):
            x, y = width * xi * .1, height * yi * .1
            box = [x, y, x + bw, y + bh]
            overlap = 0
            for target in record["detections"]:
                a, b, c, d = target["bbox"]
                overlap += max(0, min(c, box[2]) - max(a, x)) * max(0, min(d, box[3]) - max(b, y))
            if overlap / (bw * bh) < .02:
                return [round(value, 3) for value in box]
    return None


def prepare_images(archive: Path, output: Path) -> tuple[list[dict], list[dict]]:
    images_dir = output / "images"
    sources = output / "sources"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir = sources / "yolo_labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    records, manifest = [], []
    rng = random.Random(20261024)
    with zipfile.ZipFile(archive) as package:
        # Select only known archive members; do not extract arbitrary paths.
        names = sorted(n for n in package.namelist() if
                       n.startswith("coco128/images/train2017/") and n.lower().endswith(".jpg"))
        if len(names) != 128:
            raise ValueError(f"Expected 128 images, received {len(names)}")
        for index, member in enumerate(names):
            original = PurePosixPath(member).name
            stem = PurePosixPath(member).stem
            suffix = ".jpeg" if index % 8 == 0 else ".png" if index % 4 == 0 else ".jpg"
            filename = stem + suffix
            image_bytes = package.read(member)
            image = QImage.fromData(image_bytes)
            if image.isNull():
                raise ValueError(f"Could not decode {member}")
            width, height = image.width(), image.height()
            image_path = images_dir / filename
            if suffix == ".png":
                if not image.save(str(image_path), "PNG"):
                    raise ValueError(f"Could not save {image_path}")
            else:
                image_path.write_bytes(image_bytes)
            label_member = f"coco128/labels/train2017/{stem}.txt"
            label_available = label_member in package.namelist()
            label_bytes = package.read(label_member) if label_available else b""
            targets = []
            for line_number, line in enumerate(label_bytes.decode("utf-8-sig").splitlines(), 1):
                if not line.strip():
                    continue
                values = line.split()
                if len(values) != 5:
                    raise ValueError(f"Invalid label at {label_member}:{line_number}")
                category_id = int(values[0])
                if not 0 <= category_id < len(CLASS_NAMES):
                    raise ValueError("Unknown category ID")
                cx, cy, bw, bh = map(float, values[1:])
                if not all(map(math.isfinite, (cx, cy, bw, bh))) or bw <= 0 or bh <= 0:
                    raise ValueError("Invalid YOLO box")
                box = [(cx - bw / 2) * width, (cy - bh / 2) * height,
                       (cx + bw / 2) * width, (cy + bh / 2) * height]
                targets.append({"bbox": [round(value, 4) for value in box],
                                "type": CLASS_NAMES[category_id], "category_id": category_id,
                                "conf": round(rng.uniform(.51, .99), 3),
                                "source_label_line": line_number})
            source_path = f"/srv/algorithm/test/{filename}" if index % 2 == 0 else f"C:\\algorithm\\outputs\\{filename}"
            records.append({"path": source_path, "width": width, "height": height,
                            "source_label_available": label_available, "detections": targets})
            manifest.append({"file": f"images/{filename}", "coco_image_id": int(stem),
                             "source_archive_member": member, "source_filename": original,
                             "width": width, "height": height, "target_count": len(targets),
                             "source_label_available": label_available,
                             "conversion": "JPEG to PNG, same pixel dimensions" if suffix == ".png" else "original JPEG bytes",
                             "source_image_sha256": digest(image_bytes),
                             "local_image_sha256": digest(image_path.read_bytes())})
        for member in package.namelist():
            if member.startswith("coco128/labels/train2017/") and member.endswith(".txt"):
                (labels_dir / PurePosixPath(member).name).write_bytes(package.read(member))
        for member, filename in (("coco128/LICENSE", "ARCHIVE_LICENSE.txt"), ("coco128/README.txt", "ARCHIVE_README.txt")):
            (sources / filename).write_bytes(package.read(member))
    return records, manifest


def simulated_errors(records: list[dict]) -> tuple[list[dict], dict]:
    results = copy.deepcopy(records)
    positive = [r for r in results if len(r["detections"]) >= 2]
    if len(positive) < 6:
        raise ValueError("Not enough positive samples for error scenarios")
    reference = {"note": "人工注入场景的对照答案，仅用于测试工作流，不是模型评测结论。", "scenarios": []}
    changed = positive[0]
    original = copy.deepcopy(changed["detections"][0])
    target = changed["detections"][0]
    target["category_id"] = (target["category_id"] + 1) % len(CLASS_NAMES)
    target["type"] = CLASS_NAMES[target["category_id"]]
    reference["scenarios"].append({"image": Path(changed["path"].replace("\\", "/")).name,
        "target_index": 1, "expected_status": "误检", "reason": "种类检错", "original_target": original})
    missed = positive[1]
    removed = missed["detections"].pop(0)
    reference["scenarios"].append({"image": missed["path"], "expected_status": "漏检", "missing_targets": [removed]})
    extra = next((r for r in positive[2:] if background_box(r) is not None), None)
    if extra is None:
        raise ValueError("Could not find background for an injected false positive")
    extra["detections"].append({"bbox": background_box(extra), "type": "person", "category_id": 0, "conf": .77})
    reference["scenarios"].append({"image": extra["path"], "target_index": len(extra["detections"]),
        "expected_status": "误检", "reason": "单纯误检", "note": "人工添加，位置避开数据集已有框；请以图片核对为准。"})
    remaining = [r for r in positive if r is not changed and r is not missed and r is not extra]
    low, empty, absent = remaining[:3]
    low["detections"][0]["conf"] = .12
    reference["scenarios"].append({"image": low["path"], "target_index": 1,
        "expected_status": "正检", "reason": "低置信度有效框", "confidence": .12})
    reference["scenarios"].append({"image": empty["path"], "expected_status": "漏检",
        "reason": "整张图返回空目标列表", "missing_targets": copy.deepcopy(empty["detections"])})
    empty["detections"] = []
    reference["scenarios"].append({"image": absent["path"], "expected_status": "JSON 无记录",
        "missing_targets": copy.deepcopy(absent["detections"])})
    results.remove(absent)
    return results, reference


def generate_contact_sheet(output: Path, records: list[dict]) -> None:
    # A small dataset overview with source boxes; full-size images stay untouched.
    selected, seen = [], set()
    candidates = sorted(records, key=lambda r: len({t["type"] for t in r["detections"]}), reverse=True)
    for record in candidates:
        categories = {t["type"] for t in record["detections"]}
        if len(selected) < 6 or categories - seen:
            selected.append(record)
            seen.update(categories)
        if len(selected) == 16:
            break
    selected.extend(r for r in records if r not in selected)
    selected = selected[:16]
    sheet = QImage(1280, 1040, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#142033"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(QFont("Segoe UI", 10))
    for index, record in enumerate(selected):
        filename = record["path"].replace("\\", "/").rsplit("/", 1)[-1]
        image = QImage(str(output / "images" / filename))
        scale = min(308 / image.width(), 218 / image.height())
        x0, y0 = (index % 4) * 320, (index // 4) * 260
        ox, oy = x0 + (320 - image.width() * scale) / 2, y0 + 5
        painter.drawImage(QRectF(ox, oy, image.width() * scale, image.height() * scale), image)
        painter.setPen(QPen(QColor("#60f4b0"), 1.4))
        for target in record["detections"]:
            x1, y1, x2, y2 = target["bbox"]
            painter.drawRect(QRectF(ox + x1 * scale, oy + y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale))
        painter.setPen(QColor("#e1ebf9"))
        label = f"{filename} | {len(record['detections'])} objects"
        painter.drawText(QRectF(x0 + 8, y0 + 226, 304, 28), Qt.AlignmentFlag.AlignLeft, label)
    painter.end()
    if not sheet.save(str(output / "preview_contact_sheet.jpg"), "JPEG", 90):
        raise ValueError("Could not save contact sheet")


def build_pack(output: Path, archive: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a new --output directory: {output}")
    output.mkdir(parents=True)
    records, image_manifest = prepare_images(archive, output)
    meta = {"dataset": "COCO128", "source": DATASET_URL, "bbox_format": "xyxy",
            "coordinate_units": "pixels", "box_origin": "dataset_ground_truth_yolo_labels",
            "confidence_origin": "synthetic; deterministic seed 20261024; no detector inference",
            "note": "框来自真实数据集标注；置信度为模拟值，仅用于工具功能测试。"}
    outputs = {}
    outputs["01_results_xyxy.json"] = {"meta": meta, "results": records}
    xywh = copy.deepcopy(records)
    for record in xywh:
        for target in record["detections"]:
            x1, y1, x2, y2 = target.pop("bbox")
            target["xywh"] = [x1, y1, round(x2 - x1, 4), round(y2 - y1, 4)]
    outputs["02_results_xywh.json"] = {"meta": {**meta, "bbox_format": "xywh"}, "results": xywh}
    outputs["03_results_flat.json"] = [
        {"name": r["path"], "xmin": t["bbox"][0], "ymin": t["bbox"][1],
         "xmax": t["bbox"][2], "ymax": t["bbox"][3], "type": t["type"], "score": t["conf"]}
        for r in records for t in r["detections"]]
    outputs["04_custom_fields.json"] = {"payload": {"frames": [
        {"frame_meta": {"asset": r["path"]}, "hits": [
            {"geometry": {"left": t["bbox"][0], "top": t["bbox"][1],
                          "right": t["bbox"][2], "bottom": t["bbox"][3]},
             "semantic": {"tag": t["type"]}, "metrics": {"likelihood": t["conf"]}}
            for t in r["detections"]]} for r in records]}}
    simulated, reference = simulated_errors(records)
    outputs["05_simulated_detection_errors.json"] = {"meta": {**meta, "deliberate_errors": True}, "results": simulated}
    edge = copy.deepcopy(records)
    positive = [r for r in edge if r["detections"]]
    positive[0]["detections"].append({"bbox": [30, 30, 10, 10], "type": "person", "conf": .8})
    positive[1]["detections"].append({"bbox": [1, 2, 3], "type": "car", "conf": .8})
    positive[2]["detections"].append({"bbox": [1, 2, 20, 30], "type": "dog"})
    positive[3]["detections"] = []
    edge.remove(positive[4])
    edge.extend([
        {"path": positive[5]["path"], "detections": "intentionally_not_a_list"},
        {"path": None, "detections": []},
        {"path": "/tmp/not_in_image_folder_999999.jpg", "detections": [{"bbox": [1, 2, 20, 30], "type": "person", "conf": .8}]},
    ])
    outputs["06_import_edge_cases.json"] = {"meta": {**meta, "deliberate_invalid_records": True}, "results": edge}
    outputs["07_filename_dictionary.json"] = {
        r["path"].replace("\\", "/").rsplit("/", 1)[-1]: r["detections"] for r in records}
    outputs["ground_truth.json"] = {"meta": {**meta, "confidence_origin": "absent: ground truth has no confidence",
        "missing_labels": "Images without a source label file are omitted; this does not mean the image has no objects."},
        "results": [{**r, "detections": [{k: v for k, v in t.items() if k != "conf"} for t in r["detections"]]}
                    for r in records if r["source_label_available"]]}
    for filename, data in outputs.items():
        write_json(output / filename, data)
    write_json(output / "reference" / "simulated_error_answer_key.json", reference)
    write_json(output / "sources" / "classes.json", dict(enumerate(CLASS_NAMES)))

    # Separate folders keep intentional corrupt or duplicate files out of the main test set.
    first_name = image_manifest[0]["file"].rsplit("/", 1)[-1]
    duplicate_root = output / "edge_cases" / "duplicate_names"
    for subfolder in ("set_a", "set_b"):
        destination = duplicate_root / subfolder / first_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output / "images" / first_name, destination)
    duplicate_data = {"results": [records[0]]}
    write_json(output / "edge_cases" / "duplicate_names.json", duplicate_data)
    corrupt_root = output / "edge_cases" / "corrupt_image" / "images"
    corrupt_root.mkdir(parents=True)
    shutil.copyfile(output / "images" / first_name, corrupt_root / first_name)
    (corrupt_root / "broken.png").write_bytes(b"Deliberately invalid PNG fixture for decoder error testing.\n")
    corrupt_data = {"results": [records[0], {"path": "/tmp/broken.png", "detections": []}]}
    write_json(output / "edge_cases" / "corrupt_image" / "results.json", corrupt_data)

    images = scan_images(output / "images", (".jpg", ".jpeg", ".png"))
    validation = {}
    custom_mapping = FieldMapping(Collection("/payload/frames"), "/frame_meta/asset", "/hits", "xyxy", None,
        ("/geometry/left", "/geometry/top", "/geometry/right", "/geometry/bottom"), "/semantic/tag", "/metrics/likelihood")
    for filename, data in outputs.items():
        collection = Collection("") if filename.startswith("03_") else Collection("", "keyed") if filename.startswith("07_") else Collection("/results")
        mapping = custom_mapping if filename.startswith("04_") else guess_mapping(data, collection)
        result = import_results(data, mapping, images)
        validation[filename] = {"matched_images": len(result.referenced), "matched_targets": result.matched_target_count,
                               "record_count": result.record_count, "issues": dict(result.issue_counts),
                               "images_without_json_record": len(images) - len(result.referenced)}
        if not filename.startswith("06_") and result.issues:
            raise AssertionError(f"Unexpected import issues: {filename}: {result.issue_counts}")
    expected_errors = {"目标数据无效": 3, "目标列表无效": 1, "图片字段无效": 1, "图片未匹配": 1}
    if validation["06_import_edge_cases.json"]["issues"] != expected_errors:
        raise AssertionError("Unexpected edge-case results")
    duplicate_result = import_results(duplicate_data, guess_mapping(duplicate_data, Collection("/results")),
                                     scan_images(duplicate_root, (".jpg", ".jpeg", ".png"), recursive=True))
    if dict(duplicate_result.issue_counts) != {"同名歧义": 1}:
        raise AssertionError("Duplicate filename fixture did not trigger ambiguity")
    validation["edge_cases/duplicate_names.json"] = {"issues": dict(duplicate_result.issue_counts),
                                                   "matched_targets": duplicate_result.matched_target_count}
    counts = Counter(t["type"] for r in records for t in r["detections"])
    summary = {"created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
               "source": DATASET_URL, "download_url": ARCHIVE_URL, "terms_url": TERMS_URL,
               "archive_sha256": digest(archive.read_bytes()), "images": len(records),
               "targets": sum(len(r["detections"]) for r in records), "categories_present": len(counts),
               "formats": dict(Counter(Path(m["file"]).suffix for m in image_manifest)),
               "empty_result_images": sum(not r["detections"] for r in records),
               "missing_source_label_images": [m["file"] for m in image_manifest if not m["source_label_available"]],
               "confidence_is_synthetic": True, "category_counts": dict(counts.most_common()),
               "files": image_manifest}
    write_json(output / "manifest.json", summary)
    write_json(output / "reference" / "validation_report.json", validation)
    generate_contact_sheet(output, records)
    readme = f"""# DetRetLabel 测试素材包

已下载 COCO128 的 {summary['images']} 张真实图片、{summary['targets']} 个真实目标框，涉及 {len(counts)} 种类别。
框由原始 YOLO 标签转换而来。JSON 中的置信度是固定种子生成的模拟数值，没有运行检测模型。
`05_` 中的错误为人工注入；这些数据用于功能测试，不能用于评估算法精度。

## 最快开始

1. 双击项目根目录的 `run.bat`。
2. 图片文件夹选择：`{output / 'images'}`。
3. JSON 选择：`{output / '01_results_xyxy.json'}`。
4. 映射窗口确认：记录 `/results`、图片 `/path`、目标列表 `/detections`、框 `/bbox`、格式 xyxy、种类 `/type`、置信度 `/conf`。
5. 预期：{len(records)} 张图片、{summary['targets']} 个有效目标、0 条导入问题。

图片格式：{dict(summary['formats'])}。PNG 由原图无缩放转换；JPEG/JPG 保留下载的原始字节。
JSON 路径故意混用 Linux / Windows 路径，均应按最后的文件名匹配。
原始压缩包中有 {len(summary['missing_source_label_images'])} 张图片缺少配套标签：{', '.join(summary['missing_source_label_images'])}。
这些图片在 01/02/04/07 中作为显式空结果测试，在 ground_truth.json 中省略；不能据此认定图片没有真实目标。
逐图 `source_label_available` 字段和 manifest.json 对此作了明确记录。

## 可切换的 JSON

| 文件 | 测试用途 | 关键映射 |
| --- | --- | --- |
| `01_results_xyxy.json` | 推荐起点：每图一个目标列表 | `/results`, `/path`, `/detections`, `/bbox`, xyxy |
| `02_results_xywh.json` | 左上角 + 宽高数组 | `/results`, `/path`, `/detections`, `/xywh`, **xywh** |
| `03_results_flat.json` | 每条记录一个目标 | 根节点、`/name`、平铺记录、xmin/ymin/xmax/ymax、`/type`、`/score` |
| `04_custom_fields.json` | 嵌套自定义字段，测试手动链接 | 见下方说明 |
| `05_simulated_detection_errors.json` | 人工加入类别错误、漏框、额外框、低分正确框、空结果和缺少图片记录 | 与 01 相同 |
| `06_import_edge_cases.json` | 故意损坏部分目标、缺少置信度、无效图片字段、无效列表、未匹配文件名 | 与 01 相同，预期 6 条问题 |
| `07_filename_dictionary.json` | 直接以图片名作为字典键 | 根节点、字典键、`/detections`、`/bbox`、xyxy |
| `ground_truth.json` | 数据集真实框，不添加置信度 | 与 01 相同；置信度选择“不导入” |

`04_custom_fields.json` 手动映射：

- 记录位置：`/payload/frames`
- 图片：`/frame_meta/asset`
- 目标列表：`/hits`
- 坐标结构：四个独立字段；格式 xyxy
- xmin / ymin / xmax / ymax：`/geometry/left`、`/geometry/top`、`/geometry/right`、`/geometry/bottom`
- 种类：`/semantic/tag`
- 置信度：`/metrics/likelihood`

## 额外场景

- **格式筛选**：在主图片目录中取消 PNG 或 JPEG 勾选后，对应 JSON 记录应显示“图片未匹配”；重新勾选后恢复匹配。
- **重名冲突**：图片文件夹选择 `edge_cases/duplicate_names`，勾选包含子文件夹；JSON 选择 `edge_cases/duplicate_names.json`。预期两张同名图片、1 条“同名歧义”、0 个已关联目标。
- **图片损坏**：图片文件夹选择 `edge_cases/corrupt_image/images`，JSON 选择 `edge_cases/corrupt_image/results.json`。`broken.png` 被刻意损坏，选中时应显示解码失败，其余图片正常。
- **人工误检 / 漏检**：`reference/simulated_error_answer_key.json` 给出 05 中 6 个注入场景的图片、目标序号及遗漏框。
- **预期统计**：`reference/validation_report.json` 是用当前工具的解析器实际导入后得到的统计。
- **素材总览**：`preview_contact_sheet.jpg` 展示 16 张样例和数据集目标框；原始测试图没有绘制叠加框。

## 来源与可追溯性

- 数据集介绍：{DATASET_URL}
- 下载地址：{ARCHIVE_URL}
- COCO 条款：{TERMS_URL}
- 原始包附带的声明：`sources/ARCHIVE_LICENSE.txt`、`sources/ARCHIVE_README.txt`。
- 原始 YOLO 标签：`sources/yolo_labels/`。YOLO 类别号为 0–79，见 `sources/classes.json`。
- `manifest.json` 包含下载地址、压缩包校验值、逐图尺寸、类别分布、原始图片成员名和校验值。
- COCO 标注由 COCO Consortium 以 CC BY 4.0 提供；照片版权属于各自权利人，照片使用条款见 COCO 来源说明。压缩包附带许可声明已原样保留。

复现：在项目根目录运行 `.venv\\Scripts\\python.exe -B tools\\prepare_test_data.py --output test_data/coco128_new`。
脚本会复用 `test_data/_downloads/coco128.zip`，并拒绝覆盖已有输出目录。
"""
    (output / "开始测试.md").write_text(readme, encoding="utf-8")
    return {k: v for k, v in summary.items() if k not in ("files", "category_counts")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "test_data" / "coco128")
    parser.add_argument("--archive", type=Path, default=ROOT / "test_data" / "_downloads" / "coco128.zip")
    args = parser.parse_args()
    download_archive(args.archive)
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families() and os.name == "nt":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    summary = build_pack(args.output.resolve(), args.archive.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Test pack created: {args.output.resolve()}")


if __name__ == "__main__":
    main()
