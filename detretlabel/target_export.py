"""Validate and atomically export a fixed, flat, normalized target data set."""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path, PureWindowsPath
from typing import Callable

from .core import image_dimensions
from .review_report import ReviewReport
from .type_mapping import TypeMapping, category_text
from .export_check_report import write_check_excel

OUTPUT_FIELDS = ("name", "width", "height", "type", "type_name", "conf", "x", "y", "w", "h")
DECIMAL_FIELDS = ("conf", "x", "y", "w", "h")
PRECISION = Decimal("0.00000001")


@dataclass(frozen=True)
class ExportIssue:
    severity: str
    image: str
    target_id: str
    code: str
    message: str
    target_label: str = ""
    category: object = None
    type_name: str = ""


@dataclass
class TargetExport:
    targets: list[dict] = field(default_factory=list)
    issues: list[ExportIssue] = field(default_factory=list)
    image_sizes: dict[str, tuple[int, int]] = field(default_factory=dict)
    candidate_count: int = 0

    @property
    def errors(self):
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self):
        return [issue for issue in self.issues if issue.severity == "warning"]


@dataclass(frozen=True)
class ExportFiles:
    json: Path | None
    check_report: Path | None
    target_count: int
    skipped_count: int


def check_report_path(path: Path) -> Path:
    return path.with_name(path.stem + "_导出检查.xlsx")


def _fixed(value) -> Decimal:
    with localcontext() as context:
        context.prec = 768
        result = Decimal(str(value)).quantize(PRECISION)
    return abs(result) if result == 0 else result


def build_target_export(report: ReviewReport, *, image_sizes: dict | None = None,
                        type_mapping: TypeMapping | None = None,
                        image_size_loader: Callable[[Path], tuple[int, int]] | None = None,
                        size_issues: dict[str, str] | None = None,
                        trusted_sizes: set[str] | None = None) -> TargetExport:
    rows = [row for row in report.targets if
            (row["role"] == "algorithm" and row["status_code"] == "true_positive") or
            (row["role"] == "manual" and row["status_code"] == "false_negative")]
    export = TargetExport(candidate_count=len(rows))
    mapping = type_mapping or TypeMapping()
    def category_key(value):
        return ("string", value) if isinstance(value, str) else ("number", value)
    known_categories = {category_key(row["category"]) for row in report.targets if row["role"] == "algorithm"
        and isinstance(row["category"], (str, int, float)) and not isinstance(row["category"], bool)}
    sizes, size_errors = {}, {}
    for row in rows:
        image = row["image"]
        if image in sizes:
            continue
        actual, cached = None, (image_sizes or {}).get(image)
        if image_size_loader:
            try:
                actual = image_dimensions(image_size_loader(Path(report.folder) / image))
            except (OSError, ValueError):
                pass
        try:
            if actual:
                sizes[image] = actual
                try:
                    different = cached is not None and actual != image_dimensions(cached)
                except ValueError:
                    different = True
                if different:
                    export.issues.append(ExportIssue("warning", image, "", "size_conflict",
                        "已保存或 02 映射的尺寸与实际图片不一致，本次采用实际图片宽高。"))
            elif (size_issues or {}).get(image) and image not in (trusted_sizes or set()):
                raise ValueError((size_issues or {})[image])
            else:
                sizes[image] = image_dimensions(cached)
            export.image_sizes[image] = sizes[image]
            if (size_issues or {}).get(image):
                export.issues.append(ExportIssue("warning", image, "", "size_mapping", size_issues[image]))
        except ValueError as exc:
            sizes[image] = None
            size_errors[image] = f"缺少可信的图片像素宽高：{exc}。可选择图片文件夹、在 02 映射图片宽高，或批量填写尺寸。"

    for row in rows:
        image, target_id, category = row["image"], row["target_id"], row["category"]
        issues_before = len(export.issues)
        def issue(code, message, severity="error"):
            label = f"{row['source']} #{row['number']}"
            display = mapping.entries.get(category_text(category), str(category)) if category is not None else ""
            export.issues.append(ExportIssue(severity, image, target_id, code, message, label, category, str(display)))
        if not isinstance(image, str) or not image or Path(image).is_absolute() or PureWindowsPath(image).is_absolute() or ".." in image.replace("\\", "/").split("/"):
            issue("name", "图片相对路径无效。")
        if sizes[image] is None:
            issue("dimensions", size_errors[image])
        if category is None or isinstance(category, bool) or not isinstance(category, (str, int, float)) or (isinstance(category, float) and not math.isfinite(category)) or category == "":
            issue("type", "缺少有效的原始种类 type：算法目标请在 02 检查种类映射；人工框可双击种类列补充。")
        try:
            box = [row[key] for key in ("xmin", "ymin", "xmax", "ymax")]
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in box):
                raise ValueError("坐标必须是有限数值")
            if box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError("目标框宽高必须大于零")
        except (ValueError, KeyError) as exc:
            issue("box", f"目标框无效：{exc}。")
        confidence = 1 if row["role"] == "manual" else row["score"]
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence)):
            issue("conf", "置信度不是有限数值；请检查 02 的置信度字段映射。")
        if any(item.severity == "error" for item in export.issues[issues_before:]):
            continue
        width, height = sizes[image]
        try:
            with localcontext() as context:
                context.prec = 768
                x1, y1, x2, y2 = [Decimal(str(value)) for value in box]
                coordinates = [_fixed(x1 / width), _fixed(y1 / height),
                               _fixed((x2 - x1) / width), _fixed((y2 - y1) / height)]
            if coordinates[2] <= 0 or coordinates[3] <= 0:
                raise ValueError("归一化 w / h 保留八位小数后为零")
        except (ValueError, InvalidOperation) as exc:
            issue("precision", f"坐标无法按八位小数导出：{exc}。")
            continue
        key = category_text(category)
        name = mapping.entries.get(key, str(category))
        if key not in mapping.entries:
            issue("type_name", "type-mapping 未命中，type_name 使用原始 type 的字符串表示。", "warning")
        if confidence is None:
            issue("missing_conf", "算法置信度缺失，conf 输出 JSON null（不是字符串）。", "warning")
        if row["role"] == "manual" and known_categories and category_key(category) not in known_categories:
            issue("manual_type", "人工补漏 type 的原始值或数据类型未在当前算法类别中出现，已保留原值。若为旧版标签名称/类型差异，可双击人工补框的种类列选择已有类别；不会根据显示名称反推 type。", "warning")
        if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
            issue("outside_image", "目标框超出图片边界，已保留原框，不裁剪归一化坐标。", "warning")
        export.targets.append(dict(zip(OUTPUT_FIELDS, (image.replace("\\", "/"), width, height,
            category, str(name), None if confidence is None else _fixed(confidence), *coordinates))))
    return export


def _encode(value, *, fixed=False) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("导出字段必须为字符串、有限数值或 null。")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("导出字段含非有限数值。")
    return format(_fixed(number), ".8f") if fixed else format(number, "f")


def write_targets_json(export: TargetExport, path: Path, *, allow_partial=False) -> Path:
    if export.errors and not allow_partial:
        raise ValueError("导出检查未通过，请补充必要信息；也可明确选择仅导出有效部分。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="review-targets-", suffix=".json", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write("[\n" if export.targets else "[")
            for index, target in enumerate(export.targets):
                if tuple(target) != OUTPUT_FIELDS or not isinstance(target["type_name"], str):
                    raise ValueError("有效目标字段与固定导出规范不符。")
                handle.write("  {\n")
                for column, key in enumerate(OUTPUT_FIELDS):
                    handle.write(f'    "{key}": {_encode(target[key], fixed=key in DECIMAL_FIELDS)}')
                    handle.write(",\n" if column < len(OUTPUT_FIELDS) - 1 else "\n")
                handle.write("  },\n" if index < len(export.targets) - 1 else "  }\n")
            handle.write("]\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return path


def export_check_text(export: TargetExport, *, allow_partial=False) -> str:
    blocked = bool(export.errors and not allow_partial)
    lines = ["DetRetLabel 有效目标 JSON 导出检查", f"检查时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "格式：平铺数组；固定十个字段；归一化左上角 x / y / w / h；置信度与坐标八位普通十进制。",
        f"候选目标：{export.candidate_count}；有效：{len(export.targets)}；阻断项：{len(export.errors)}；提示：{len(export.warnings)}。",
        "结果：JSON 未生成，已有 JSON 保留。" if blocked else f"结果：导出 {len(export.targets)} 个目标；跳过 {export.candidate_count - len(export.targets)} 个目标。", ""]
    for issue in export.issues:
        label = "错误" if issue.severity == "error" else "提示"
        target = f"{issue.target_label}（{issue.target_id}）" if issue.target_label else issue.target_id or "整图"
        lines.append(f"[{label}] 图片：{issue.image} | 目标：{target} | {issue.message}")
    if not export.issues:
        lines.append("本次检查通过，没有发现问题。")
    return "\n".join(lines) + "\n"


def write_export_files(export: TargetExport, path: Path, *, allow_partial=False) -> ExportFiles:
    if path.suffix.lower() != ".json":
        raise ValueError("有效目标必须保存为 .json 文件。")
    blocked = bool(export.errors and not allow_partial)
    report_path = check_report_path(path)
    write_report = bool(export.issues or report_path.exists())
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".detretlabel-export-", dir=path.parent))
    backups, installed = {}, []
    keep_backups = False
    try:
        files = []
        if not blocked:
            staged_json = staging / "targets.json"
            write_targets_json(export, staged_json, allow_partial=allow_partial)
            files.append((staged_json, path))
        if write_report:
            staged_report = staging / "check.xlsx"
            write_check_excel(export, staged_report, json_path=path, allow_partial=allow_partial)
            files.append((staged_report, report_path))
        for index, (_, destination) in enumerate(files):
            backup = staging / f"backup-{index}" if destination.exists() else None
            if backup:
                shutil.copyfile(destination, backup)
            backups[destination] = backup
        try:
            for source, destination in files:
                os.replace(source, destination)
                installed.append(destination)
        except OSError as exc:
            failures = []
            for destination in reversed(installed):
                try:
                    if backups[destination]:
                        os.replace(backups[destination], destination)
                    else:
                        destination.unlink()
                except OSError:
                    failures.append(str(destination))
            if failures:
                keep_backups = True
                raise OSError(f"导出失败且无法恢复：{'；'.join(failures)}。旧文件备份保留在：{staging}") from exc
            raise
    finally:
        if not keep_backups:
            shutil.rmtree(staging, ignore_errors=True)
    return ExportFiles(None if blocked else path, report_path if write_report else None,
                       0 if blocked else len(export.targets), export.candidate_count - len(export.targets))
