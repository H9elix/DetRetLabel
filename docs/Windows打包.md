# Windows 打包与运行

[返回 README](../README.md) · [详细使用指南](使用指南.md)

GitHub 自动构建、版本标签和 Release 下载见 [GitHub 发布说明](GitHub发布.md)。

提供两种 Windows 64 位分发版本，两者均包含 Python、Qt、图片解码插件、Excel 导出组件及默认原因配置。使用者无需另装 Python、Qt 或 Excel。

作者为 **H9elix**。“帮助”菜单显示 `BuildYYYYMMDD` 并提供 README、详细使用指南的离线预览。构建时将北京时间的打包日期固定写入程序资源和 exe 属性，不按启动日期计算；构建清单也记录相同标识。源码运行使用 `detretlabel/build_info.json` 中的源码快照标识。

## 产物位置

```text
dist/windows-x64/
  DetRetLabel-single.exe              单文件版，可单独复制运行
  DetRetLabel-folder/
    DetRetLabel.exe                   文件夹版入口
    _internal/                        Python、DLL、Qt 插件及默认配置
    使用说明.txt
  DetRetLabel-single-win64.zip         单文件版和简明说明
  DetRetLabel-folder-win64.zip         完整文件夹版
  使用说明.txt
  build-manifest.json                 构建环境、版本、文件大小、SHA-256 和验证结果
```

单文件版双击 `DetRetLabel-single.exe` 即可。运行时会将内置依赖解压到系统临时目录，退出后清理；它不是静态链接程序，但不需要额外分发依赖文件。临时目录需要可写。

文件夹版双击 `DetRetLabel-folder/DetRetLabel.exe`。它从 `_internal` 加载运行库，启动通常更快。不要移动、改名或删去 `_internal`；换机时复制整个 `DetRetLabel-folder`，或完整解压对应 ZIP。

程序包不包含测试图片、个人输入 JSON 或标注进度。标注仍保存到所选图片目录的 `.detretlabel/annotations.json`。两种版本可使用相同数据；换机方法见[使用指南的保存与交接部分](使用指南.md#8-保存恢复与换机交接)。

## 重新打包

在 Windows 上使用 64 位 Python 构建。已准备项目 `.venv` 时，可双击根目录的 [build_windows.bat](../build_windows.bat)，它会安装构建依赖并生成两种版本。

也可以在项目目录的 PowerShell 中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools/build_windows.py
```

构建脚本使用 [deployment/windows.spec](../deployment/windows.spec)，一次分析依赖，生成单文件 exe 和文件夹版。重复构建会更新同名打包产物；请先关闭已启动的打包程序。

构建工具版本固定在 [requirements-build.txt](../requirements-build.txt)，GitHub 发布使用 [requirements-release.txt](../requirements-release.txt) 固定运行依赖。发布新版本时同步修改 `detretlabel/__init__.py`、`pyproject.toml`；[deployment/version_info.txt](../deployment/version_info.txt) 是模板，其版本与作者由构建脚本自动填入。

## 自动验证

打包后脚本会清除验证进程的 Python / Qt 环境路径，以打包后的 exe 运行一组独立检查，不改动个人图片、注册表设置或原有标注：

- 原生 Qt 窗口启动，默认误检 / 漏检原因可读取。
- 帮助菜单中的作者及打包日期正确，内置 README / 使用指南可显示、相互跳转及查找。
- JPG、JPEG、PNG、BMP、TIF、TIFF、WEBP 图片可写入、读取。
- 中文及空格目录中的图片可扫描，Linux / Windows JSON 路径均可匹配。
- 算法和 GT 字段映射、框叠加、种类显示映射可用。
- 正检 / 误检审核、自动进入 04、人工补漏、进度保存和恢复可用。
- Excel 报告导出后可重新读取，三张表及统计正确。

记录保存在 `artifacts/packaging/<时间>/single/` 和 `folder/`，包括 `result.json`、界面截图、临时测试图片及报告。仅当两种 exe 都通过检查时，脚本才生成分发 ZIP 和 `build-manifest.json`。

构建日志在 `build/windows-x64/pyinstaller.log`。验证失败时查看对应 `result.json`；它是构建检查入口生成的诊断文件，不是标注进度文件。

本机验证能确认这台 Windows 上的打包运行情况；未在其他 Windows 版本的全新机器上实测。发布到其他机器后，可先用自带测试素材核对导入和导出操作。

打包机制参考：[PyInstaller 单文件 / 文件夹选项](https://pyinstaller.org/en/stable/usage.html#what-to-generate)、[运行时资源路径](https://pyinstaller.org/en/stable/runtime-information.html)。
