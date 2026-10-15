# Windows 故障排查

## 无法打开页面

确认运行的是哪份源码、哪个端口，避免把开发目录和正在运行的副本混为一谈。检查启动窗口或 `logs/service-*.err.log`；后台任务使用正确的 Python 3.12、起始目录和本项目入口。

```powershell
Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue
Invoke-RestMethod http://127.0.0.1:8088/api/health
Invoke-RestMethod http://127.0.0.1:8088/api/ready
ollama list
ollama ps
```

只停止自己确认归属的服务进程，不要批量结束所有 Python/Ollama。就绪探针会说明嵌入加载或生成模型连接失败；健康探针成功不能代替就绪检查。

## 模型不可用或回答很慢

核对 `.env` 模型名与 `ollama list`，本机接口使用 `http://127.0.0.1:11434/v1`，默认不继承系统代理 `RAG_LLM_TRUST_ENV_PROXY=0`。代理只能影响真正需要它的下载过程，不应把本机推理绕到代理。

生成耗时与检索耗时分开看；模型冷启动、内存不足和游戏争抢显存都可能拖慢回答。先释放资源再做固定对照，不直接降低召回来换取表面速度。CPU 安装不能仅通过修改字符串获得 CUDA 能力。

## 登录失败

root 初始密码只在首次创建数据库时使用；数据库已有用户时，修改 `RAG_ROOT_PASSWORD` 不会重设密码。安全 Cookie 只能通过 HTTPS 发送，核对 `RAG_PUBLIC_ORIGIN` 与实际访问地址。不要使用跳过启动校验代替修复配置。

## 手机 DNS 或 HTTPS 错误

确认手机 Tailscale 已连接到同一网络，电脑在线；用 `tailscale serve status` 复制实际 HTTPS 地址。检查网络 DNS 设置和 Serve HTTPS 是否启用。不要改用公网 Funnel 来绕过私网问题。Windows 主机休眠后无法继续提供问答，熄屏本身不影响。

## 文档处理与答案不完整

确认文档处理完成、有可提取文本、账号有权限且文档范围正确。旧 `.doc` 可转换为 DOCX；扫描 PDF 需 OCR。先检查精确来源，再区分解析、切块、召回和生成问题，不只修改提示词。

解析或切块调整不会自动应用到旧索引。备份后显式重处理目标文档并验证来源覆盖。原文只含标签时不应补写完整定义；遇到冲突时应核对原文，不能以流畅的答案替代证据。

解析在子进程隔离并有超时。Windows 没有本项目 POSIX 内存限制的等效硬上限，仍需控制文件大小和可用内存。保留失败文件与错误日志，先在隔离目录复现。
