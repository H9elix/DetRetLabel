"""Validate version tags and stage only verified files for GitHub Releases."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import runpy
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def application_version(root: Path = ROOT, tag: str | None = None) -> str:
    version = runpy.run_path(str(root / "detretlabel" / "__init__.py"))["__version__"]
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("Application version must use MAJOR.MINOR.PATCH, for example 0.1.0.")
    project = (root / "pyproject.toml").read_text(encoding="utf-8-sig")
    match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
    if not match or match.group(1) != version:
        raise ValueError("Versions in detretlabel/__init__.py and pyproject.toml must match.")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Release tag must match the source version: expected v{version}, received {tag!r}.")
    return version


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def stage_assets(root: Path = ROOT, tag: str | None = None) -> Path:
    version = application_version(root, tag)
    output = root / "dist" / "windows-x64"
    manifest = json.loads((output / "build-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != version:
        raise ValueError("Build manifest version does not match the current source. Rebuild before publishing.")
    if not re.fullmatch(r"Build\d{8}", manifest.get("build", "")):
        raise ValueError("The build manifest is missing a valid build stamp.")
    checks = {item["variant"]: item for item in manifest.get("smoke_tests", [])}
    if set(checks) != {"single", "folder"} or any(item.get("checks", 0) < 1 for item in checks.values()):
        raise ValueError("Both executable variants must pass runtime verification before publishing.")
    expected = {entry["path"]: entry for entry in manifest["files"]}
    names = ("DetRetLabel-single.exe", "DetRetLabel-single-win64.zip", "DetRetLabel-folder-win64.zip")
    for name in names:
        path = output / name
        entry = expected[name]
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError(f"Build output failed integrity verification: {name}")
    destination = output / "release"
    destination.mkdir(exist_ok=True)
    for name in (*names, "build-manifest.json"):
        shutil.copyfile(output / name, destination / name)
    # GitHub normalizes non-ASCII asset names; use a stable ASCII upload name.
    shutil.copyfile(output / "使用说明.txt", destination / "USAGE.txt")
    sums = [f"{digest(destination / name)}  {name}\n" for name in (*names, "build-manifest.json", "USAGE.txt")]
    (destination / "SHA256SUMS.txt").write_text("".join(sums), encoding="utf-8", newline="\n")
    notes = f"""作者：**{manifest['author']}** · **{manifest['build']}** · Windows 64 位

| 下载 | 使用方式 |
| --- | --- |
| `DetRetLabel-single.exe` | 单文件版，直接双击运行，仅复制这个 exe 即可 |
| `DetRetLabel-single-win64.zip` | 单文件版与简明说明的压缩包 |
| `DetRetLabel-folder-win64.zip` | 文件夹版，完整解压后运行 `DetRetLabel-folder/DetRetLabel.exe`，保留 `_internal` |

无需安装 Python、Qt 或 Excel。两种版本均已通过自动测试与打包后运行检查。
“帮助”菜单内置 README、详细使用指南、作者和打包日期。

标注进度保存在所选图片目录的 `.detretlabel/annotations.json`，升级程序时保留图片目录即可。
源码下载和程序下载分开提供；程序包不包含个人图片、标注进度或测试数据集。

`SHA256SUMS.txt` 提供附件校验值；`build-manifest.json` 记录依赖版本及构建来源。

版本：{version} · 提交：{os.environ.get('GITHUB_SHA', '本地构建')}
"""
    (destination / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8", newline="\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Release tag, such as v0.1.0")
    parser.add_argument("--check-version", action="store_true", help="Check versions without staging files")
    args = parser.parse_args()
    if args.check_version:
        print(f"Version verified: {application_version(tag=args.tag)}")
    else:
        print(f"Release assets ready: {stage_assets(tag=args.tag)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
