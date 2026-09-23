# 注入验证

在仓库根目录运行 `python tests/injections/run_injections.py`。脚本先确认原测试通过，再逐个改回旧缺陷，要求对应断言变红，最后确认恢复为绿色。它只修改临时仓库副本，不改当前源码、数据库或模型。

快速检查锚点：`python tests/injections/check_anchors.py`。只跑某一组：`python tests/injections/run_injections.py --group probe`（可选 `probe`、`isolation`、`latency`、`encoding`）。`isolation` 的 Windows `taskkill` 注入只在 Windows 上运行。
