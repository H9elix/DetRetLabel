# GitHub 源码与自动发布

[返回 README](../README.md) · [本地 Windows 打包](Windows打包.md)

源码仓库：[H9elix/DetRetLabel](https://github.com/H9elix/DetRetLabel)。
程序下载：[Releases](https://github.com/H9elix/DetRetLabel/releases)。
构建进度：[Actions](https://github.com/H9elix/DetRetLabel/actions)。

## 自动流程

| 操作 | 自动执行的工作 |
| --- | --- |
| 向 main 推送代码 | Windows 测试、打包 single / folder、运行打包后检查、保存构建附件 |
| 提交 Pull Request | 同样执行检查，不发布 Release |
| 推送 `v0.1.0` 这样的版本标签 | 完成全部检查后创建对应 GitHub Release，上传两种程序包并发布 |
| Actions 中选择 Run workflow | 检查所选分支；选已有版本标签时可补跑其发布流程 |

工作流位于 [.github/workflows/windows-release.yml](../.github/workflows/windows-release.yml)。Windows 构建使用 Python 3.14.5，依赖固定在 [requirements-release.txt](../requirements-release.txt)。

构建和发布分为两个任务：构建任务只读源码；发布任务在构建成功后获得上传 Release 的权限。使用 GitHub 自动提供的 `GITHUB_TOKEN`，无需另外创建或填写个人令牌。若账号或组织限制了 Actions，需允许本仓库运行工作流和写入 Release。

## 发布一个新版本

1. 在 `detretlabel/__init__.py` 和 `pyproject.toml` 中将版本号改成相同的 `主版本.次版本.修订号`，例如 `0.2.0`。
2. 提交代码并推送 main。
3. 创建并推送对应标签。标签必须是 `v` 加源码版本号，否则自动检查会停止发布。

例如发布 `0.2.0`，在项目目录执行：

```powershell
git add -A
git commit -m "Prepare v0.2.0"
git push origin main
git tag -a v0.2.0 -m "DetRetLabel v0.2.0"
git push origin v0.2.0
```

在 Actions 等待 `Windows build and release` 完成，然后打开对应 Release 下载。exe 的作者和版本属性从源码自动生成；`BuildYYYYMMDD` 使用北京时间的构建日期，与 GitHub runner 所在时区无关。

普通 main 提交不创建新的 Release。每个正式版本使用新标签，不移动已发布版本的标签，也不复用版本号。

## Release 中的文件

| 附件 | 用途 |
| --- | --- |
| `DetRetLabel-single.exe` | 可独立运行的单文件程序 |
| `DetRetLabel-single-win64.zip` | 单文件程序及简明说明 |
| `DetRetLabel-folder-win64.zip` | 完整文件夹版；解压后运行其中的 exe，保留 `_internal` |
| `USAGE.txt` | 简明操作说明；使用英文附件名保证 GitHub 下载名与校验清单一致 |
| `build-manifest.json` | 作者、版本、Build、依赖、提交和运行检查信息 |
| `SHA256SUMS.txt` | 上述附件的 SHA-256 校验值 |
| GitHub 自动提供的 Source code | 该标签对应的源码压缩包，不是已打包程序 |

程序包不含测试集和个人进度。源码中的测试素材仅保留说明与生成脚本，按[素材准备方法](../test_data/README.md)可另行下载和生成。

## 失败、重试和查看日志

- 测试或任一种 exe 运行检查失败，就不会发布 Release。
- Actions 保留 Windows 程序包和诊断附件 14 天，正式 Release 附件不受这个构建附件期限影响。
- 打开失败任务查看日志，或下载 `windows-diagnostics` 查看打包日志、验证结果及界面截图。
- 发布时先创建草稿，所有附件上传完成才公开。草稿阶段失败，可在 Actions 中重跑原任务，补传该草稿。
- 已发布的版本不会自动覆盖；需要修改时使用新版本号和新标签。

`.gitignore` 排除了 `.venv`、本地 exe / DLL、构建目录、报告、图片目录中的 `.detretlabel` 进度和下载数据。本地测试集及标注仍保留在磁盘上。

机制参考：[GitHub 的标签触发规则](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#push)、[Release 创建与已有标签校验](https://cli.github.com/manual/gh_release_create)。
