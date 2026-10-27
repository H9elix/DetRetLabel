"""Build and verify both Windows distributions, then create transfer archives."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "windows-x64"
WORK = ROOT / "build" / "windows-x64"

USAGE = """DetRetLabel Windows 64 位版

单文件版：双击 DetRetLabel-single.exe；仅复制这个 exe 即可运行，无需安装 Python。
单文件启动时会自动解压内置依赖到系统临时目录，启动会比文件夹版稍慢。

文件夹版：打开 DetRetLabel-folder，双击 DetRetLabel.exe。
_internal 是依赖文件夹，请与 exe 放在一起。交接时复制整个 DetRetLabel-folder 文件夹。
两种版本均不需要预装 Python、Qt 或 Excel。
作者：H9elix。作者、打包日期、README 和详细使用指南均可在窗口顶部“帮助”菜单中查看。

最基本操作：
1. 在 01 选择图片文件夹。
2. 在 02 添加算法 JSON，确认字段映射；可选添加 GT 参考 JSON。
3. 在 03 按 1 标正检，按 2 标误检，再选原因或 Enter 跳过。
4. 算法审核结束自动进入 04；也可按 R 提前进入。仅在 04 中按 B 人工补漏框。
5. 空格完成 04，自动跳到下一张未完成图片；输入框中先确认文字或将焦点切回图片。
6. 点击“导出 Excel…”导出当前复核报告，未完成时也可以导出。

GT 仅供参考；03、04 都完成才算整图完成。
标注自动保存到图片目录内的 .detretlabel/annotations.json，Ctrl+S 可立即保存。
换机继续标注时，带上完整图片目录（含 .detretlabel）和原始算法 JSON，使用相同字段映射。
测试图片和个人标注不包含在程序包里。
"""


def main() -> int:
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64"):
        raise SystemExit("Build using 64-bit Python on Windows.")
    # All generated paths are fixed within this project, never arbitrary deletion targets.
    for path in (OUTPUT, WORK):
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise SystemExit("Build output must remain inside the project.")
        path.mkdir(parents=True, exist_ok=True)
    log_path = WORK / "pyinstaller.log"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(OUTPUT),
               "--workpath", str(WORK), "--clean", str(ROOT / "deployment" / "windows.spec")]
    # Native tools elsewhere on PATH can contain unrelated DLLs with Qt dependency names.
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM", "VIRTUAL_ENV"):
        environment.pop(key, None)
    windows_paths = [str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]]
    environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), sys.base_prefix, *windows_paths])
    print("Building both Windows distributions...", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    stamp = json.loads((WORK / "build_info.json").read_text(encoding="utf-8"))

    executables = [OUTPUT / "DetRetLabel-single.exe", OUTPUT / "DetRetLabel-folder" / "DetRetLabel.exe"]
    test_root = ROOT / "artifacts" / "packaging" / datetime.now().strftime("%Y%m%d-%H%M%S")
    test_root.mkdir(parents=True, exist_ok=True)
    # Tests must not accidentally resolve Python or Qt through the developer environment.
    environment["PATH"] = os.pathsep.join(windows_paths)
    results = []
    for label, executable in zip(("single", "folder"), executables):
        print(f"Checking {label} executable...", flush=True)
        destination = test_root / label
        subprocess.run([str(executable), "--smoke-test", str(destination)], cwd=test_root,
                       env=environment, check=True, timeout=120)
        result = json.loads((destination / "result.json").read_text(encoding="utf-8"))
        if not result["ok"] or not result["frozen"] or result["platform"] != "windows":
            raise RuntimeError(f"Frozen application check failed: {destination}")
        if result.get("author") != "H9elix" or result.get("build") != stamp["build"]:
            raise RuntimeError(f"Incorrect author or build stamp: {destination}")
        results.append({"variant": label, "result": str(destination / "result.json"), "checks": len(result["checks"])})

    usage = f"作者：H9elix    {stamp['build']}\n\n{USAGE}"
    (OUTPUT / "使用说明.txt").write_text(usage, encoding="utf-8-sig")
    (OUTPUT / "DetRetLabel-folder" / "使用说明.txt").write_text(usage, encoding="utf-8-sig")
    print("Creating transfer archives...", flush=True)
    from zipfile import ZIP_DEFLATED, ZipFile
    with ZipFile(OUTPUT / "DetRetLabel-single-win64.zip", "w", ZIP_DEFLATED) as archive:
        archive.write(executables[0], executables[0].name)
        archive.write(OUTPUT / "使用说明.txt", "使用说明.txt")
    shutil.make_archive(str(OUTPUT / "DetRetLabel-folder-win64"), "zip", OUTPUT, "DetRetLabel-folder")
    versions = {name: importlib.metadata.version(name) for name in
                ("PyInstaller", "pyinstaller-hooks-contrib", "PySide6", "shiboken6", "openpyxl", "et_xmlfile")}
    files = []
    for path in [*executables, OUTPUT / "DetRetLabel-single-win64.zip", OUTPUT / "DetRetLabel-folder-win64.zip"]:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        files.append({"path": path.relative_to(OUTPUT).as_posix(), "bytes": path.stat().st_size,
                      "sha256": hasher.hexdigest()})
    manifest = {"author": stamp["author"], "version": stamp["version"], "build": stamp["build"],
                "built_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
                "python": platform.python_version(), "versions": versions, "smoke_tests": results, "files": files,
                "source": {key: os.environ[key] for key in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF_NAME", "GITHUB_RUN_ID") if key in os.environ}}
    (OUTPUT / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Build and verification complete: {OUTPUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
