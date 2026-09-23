# 注入验证

在仓库根目录运行 `python tests/injections/run_injections.py`。脚本先确认原测试通过，再逐个改回旧缺陷，要求对应断言变红，最后确认恢复为绿色。它只修改临时仓库副本，不改当前源码、数据库或模型。

快速检查锚点：`python tests/injections/check_anchors.py`（CI 里只跑这一步，代价低）。只跑某一组：`python tests/injections/run_injections.py --group probe`（可选 `probe`、`isolation`、`latency`、`retrieval`、`encoding`）。`isolation` 的 Windows `taskkill` 注入只在 Windows 上运行。

加新注入时，在新的一组 `inject_*.py` 里加一行 `(标签, 仓库内路径, 原文, 缺陷版本, 期望失败的断言片段)`，再到 `check_anchors.py` 的 `GROUPS` 与 `run_injections.py` 的 `TESTS` 里登记这一组。锚点必须**唯一匹配**：同一行代码出现两次时用 `\n` 前缀锚定行首（`\n` 在 CRLF 文件里同样匹配），否则短缩进模式会作为长缩进那行的子串被匹配两次。
