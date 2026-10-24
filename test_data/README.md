# 准备测试素材

[返回 README](../README.md)

源码仓库保留 COCO128 测试包的说明、种类映射和生成脚本；下载图片、派生检测 JSON、缓存及个人标注不进入 Git 历史。构建程序和运行自动测试不需要下载这个数据集。

如需人工测试完整流程，在已安装项目依赖的环境中运行：

```powershell
.\.venv\Scripts\python.exe tools/prepare_test_data.py --output test_data/coco128_generated
```

该命令从原始来源下载 COCO128，生成 128 张图片、929 个目标框，以及多种格式和异常场景的 JSON。`coco128_generated` 必须是尚不存在的新目录；脚本不会覆盖已有图片或标注。

生成后，在 01 选择 `test_data/coco128_generated/images`，在 02 导入 `test_data/coco128_generated/01_results_xyxy.json`，其余操作参见[测试包说明](coco128/开始测试.md)。说明中原来的 `coco128` 路径改用本次生成的 `coco128_generated` 即可。

置信度为模拟值，错误场景是人工注入，适合测试工具功能。原始来源、许可和数据校验信息会保存在生成包内，详见测试包说明。
