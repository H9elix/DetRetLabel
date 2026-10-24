# -*- mode: python ; coding: utf-8 -*-
"""Build the two Windows distributions from one dependency analysis."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import runpy
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
stamp_folder = root / "build" / "windows-x64"
stamp_folder.mkdir(parents=True, exist_ok=True)
identity = runpy.run_path(str(root / "detretlabel" / "__init__.py"))
version = identity["__version__"]
stamp = {"build": datetime.now(timezone(timedelta(hours=8))).strftime("Build%Y%m%d"),
         "version": version, "author": identity["__author__"]}
stamp_path = stamp_folder / "build_info.json"
stamp_path.write_text(json.dumps(stamp, ensure_ascii=False, indent=2), encoding="utf-8")
version_path = stamp_folder / "version_info.txt"
version_text = (root / "deployment" / "version_info.txt").read_text(encoding="utf-8")
for key, value in {"@BUILD_ID@": stamp["build"], "@VERSION@": version,
                   "@AUTHOR@": stamp["author"],
                   "@FILE_VERSION@": repr((*map(int, version.split(".")), 0))}.items():
    version_text = version_text.replace(key, value)
version_path.write_text(version_text, encoding="utf-8")
help_data = [
    (str(root / "README.md"), "."),
    (str(root / "docs"), "docs"),
    (str(root / "examples"), "examples"),
    (str(root / "test_data" / "coco128" / "开始测试.md"), "test_data/coco128"),
    (str(root / "test_data" / "coco128" / "type-mapping.txt"), "test_data/coco128"),
    (str(root / "test_data" / "README.md"), "test_data"),
]
for name in ("run.bat", "build_windows.bat", "requirements-build.txt", "requirements-release.txt", "requirements.txt", "pyproject.toml"):
    help_data.append((str(root / name), "."))
for name in ("windows.spec", "version_info.txt"):
    help_data.append((str(root / "deployment" / name), "deployment"))
metadata = []
for distribution in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "openpyxl", "et_xmlfile"):
    metadata += copy_metadata(distribution)

a = Analysis(
    [str(root / "deployment" / "entry.py")],
    pathex=[str(root), str(root / "deployment")],
    binaries=[],
    datas=[(str(root / "config" / "reasons.json"), "config"),
           (str(stamp_path), "detretlabel"), *help_data, *metadata],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

single = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="DetRetLabel-single",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    version=str(version_path),
)

folder_exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DetRetLabel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    contents_directory="_internal",
    version=str(version_path),
)
folder = COLLECT(
    folder_exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name="DetRetLabel-folder",
)
