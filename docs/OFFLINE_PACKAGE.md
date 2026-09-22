# 管理员离线包制作

[返回首页](../README.md) · [交付清单模板](../deploy/MODEL_MANIFEST.md)

本页仅供安装管理员。仓库没有现成的通用离线包，也没有自动识别硬件/补齐所有驱动的安装器。制包必须匹配目标 OS、CPU 架构、Python、GPU 驱动及组织政策。

## 三个环境必须分开

| 环境 | 允许的动作 |
|---|---|
| 受控联网准备机 | 下载官方源码、软件、依赖与模型；做供应链核验；不能带入内部知识库资料 |
| 断网验收机 | 与目标环境一致，从零安装交付包，验证没有隐式下载 |
| 正式运行机及受管客户端 | 仅批准的本机/局域网通信；禁止互联网、云 API、在线更新和补包 |

以下标为“准备机”的下载命令**不得在运行机执行**。模型/依赖缺失时回到准备流程，不能临时开放出口。

## 1. 先固定交付内容

每个平台单独制作一个包，记录完整源码 commit、打包时间、OS/架构、安装器版本/签名、依赖锁定文件、镜像 ID、模型标签/摘要/来源版本、许可证、测试结果。参考 [清单模板](../deploy/MODEL_MANIFEST.md)。

源码从 [项目仓库](https://github.com/ihyh/lan-rag-pilot)取得。仅在获准联网的准备机操作，选定审核通过的完整 commit 后导出；运行机不直接 `git pull`。

准备机 PowerShell，在新空目录执行：

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag-source
Set-Location C:\rag-source
git status --short
git log -5 --oneline
$releaseCommit = 'REPLACE_WITH_APPROVED_FULL_COMMIT'
if ($releaseCommit -like 'REPLACE*') { throw '先填写已审核的源码 commit' }
git show --no-patch --format=fuller $releaseCommit
git archive --format=zip --output=C:\rag-source.zip $releaseCommit
Get-FileHash -Algorithm SHA256 C:\rag-source.zip
```

核对导出内容包含 app、scripts、requirements.txt，再将源码放入包的 project 目录。`git archive` 只导出已提交文件；构建镜像、制包和文档必须对应同一批准提交。未提交的修改不会包含在归档中；如需交付另行批准的补丁，单独记录 patch 和摘要。

实际 `.env`、data、models、会话、备份、凭据、虚拟环境、私人评测和本地规划记录不随源码交付。运行机不保存 GitHub 令牌、SSH 私钥或公网 API 凭据。软件包接收后按[运维升级](OPERATIONS.md)备份并在独立实例演练。

本地修改、GitHub 合并、离线包交付和服务器验收分别记录。GitHub 更新不代表运行服务已更新；历史快照保留在 Git 中，不作为现行部署状态。

推荐包布局（这是管理员要制作的目录，不是仓库已附带的文件）：

```text
rag-kit/
  project/                       源码，app/ 位于其中
  models/bge-small-zh-v1.5/       自包含嵌入模型目录
  ollama-models/                  Windows 使用，含 blobs/ 与 manifests/
  windows/
    installers/                  Python、Ollama、必要驱动/运行库
    requirements.lock.txt
    wheelhouse/
  ubuntu/
    debs/                        Docker/Compose/Nginx/Python3及依赖闭包
    images.tar                   rag-pilot:offline、ollama:offline
    embedding.tgz                根目录直接是 bge-small-zh-v1.5/
    ollama-data.tgz               根目录含 models/blobs、models/manifests
  RELEASE.md
```

Windows/Ubuntu 可分别交付，只需包含对应部分。模型库和原业务数据不能混在软件包中。检查源码、模型及依赖再分发许可；当前仓库没有可据以承诺任意再分发的 LICENSE 文件。

## 2. Windows 依赖：同平台准备

在干净的 Windows 11 x64 准备机上，使用与目标相同的 Python 3.12.x。从官方渠道取得完整安装材料并核验发行者签名；不要交付需要在线下载组件的小型启动器。

准备机 PowerShell；假设上述目录已创建：

```powershell
Set-Location C:\rag-kit
py -3.12 -m venv prep-env
.\prep-env\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\prep-env\Scripts\python.exe -m pip install -r .\project\requirements.txt
.\prep-env\Scripts\python.exe -m pip check
.\prep-env\Scripts\python.exe -m pip freeze | Set-Content -Encoding ascii .\windows\requirements.lock.txt
.\prep-env\Scripts\python.exe -m pip download --only-binary=:all: --extra-index-url https://download.pytorch.org/whl/cpu -r .\windows\requirements.lock.txt -d .\windows\wheelhouse
```

只在干净环境生成 lock，避免混入私人包或本机路径。requirements.txt 是版本范围而非锁定文件，必须保留本次实际解析版本与文件哈希。额外索引只用于受控准备；审核下载来源和解析结果。若某依赖没有兼容 wheel，先在准备环境处理并重新验收，不在目标机编译或联网。

pip 支持下载依赖后用 `--no-index --find-links` 离线安装；跨平台下载必须正确指定目标 ABI 等参数，本教程采用同平台准备降低风险。[pip 官方说明](https://pip.pypa.io/en/stable/cli/pip_download/)

## 3. 导出完整嵌入模型

在准备机选择并记录 Hugging Face 模型的**完整 commit revision**，不要只记录可变的 main。Windows PowerShell 设置下面变量为已审核值后运行：

```powershell
$env:RAG_EMBED_REVISION = 'REPLACE_WITH_APPROVED_FULL_MODEL_COMMIT'
if ($env:RAG_EMBED_REVISION -like 'REPLACE*') { throw '先填写审核通过的模型 commit' }
.\prep-env\Scripts\python.exe -c "import os; from sentence_transformers import SentenceTransformer; m=SentenceTransformer('BAAI/bge-small-zh-v1.5', revision=os.environ['RAG_EMBED_REVISION'], device='cpu'); m.save('models/bge-small-zh-v1.5')"
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.\prep-env\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('models/bge-small-zh-v1.5', device='cpu'); print(m.encode(['离线验收']).shape)"
```

成功应输出 `(1, 512)`。还要在真正断网、没有历史 HF 缓存的验收机复测。导出自包含目录可避免只复制缓存链接却漏掉实际文件；完整下载及 revision 的语义见 [Hugging Face 官方说明](https://huggingface.co/docs/huggingface_hub/guides/download)。

Ubuntu 包需用最终 rag 镜像加载该目录验证。不同平台采用不同依赖版本时尤其不能只凭 Windows 加载成功就放行。

## 4. 准备 Ollama 模型

Windows 准备机使用独立的 OLLAMA_MODELS 目录，退出旧进程并用新环境重新启动后，在**准备机**执行 `ollama pull qwen3:4b`。模型按 [硬件指南](MODELS.md)选择，不用 cloud 标签。

验证能离线运行、`ollama list` 的标签/ID 与清单一致，再退出 Ollama，将目录内的 blobs 和 manifests 一起交付。不要只复制 blobs，也不要携带准备账号的私钥或其他个人状态。

设置 OLLAMA_NO_CLOUD=1 并重启可关闭云功能，最终仍要断网验收。[Ollama FAQ](https://docs.ollama.com/faq)

Ubuntu 可直接在 Linux 准备机制作独立模型卷，见下一节；不要假设不同版本的本地缓存任意兼容。

## 5. Ubuntu 镜像及系统依赖

使用 Ubuntu 24.04 LTS amd64 准备/验收环境。取得 Docker Engine、CLI、containerd、Buildx、Compose 插件，以及 Nginx、Python3 和系统依赖的完整离线 deb 集合；GPU 部署还需要兼容驱动和 NVIDIA Container Toolkit。固定版本，保留官方签名验证结果。

Docker 官方提供对应发行版/架构的 deb。依赖闭包需要在匹配的干净系统镜像中验证，不能只下载几个顶层包就认为足够。[Ubuntu 官方安装说明](https://docs.docker.com/engine/install/ubuntu/)

准备机 Bash，源码在 /srv/rag-kit/project；Ubuntu 目录已创建。先填写经审批的 Ollama 镜像 digest：

```bash
cd /srv/rag-kit/project
docker build -t rag-pilot:offline .
export OLLAMA_IMAGE='ollama/ollama@sha256:REPLACE_WITH_APPROVED_DIGEST'
case "$OLLAMA_IMAGE" in *REPLACE*) echo '先填写镜像摘要'; exit 1;; esac
docker pull "$OLLAMA_IMAGE"
docker tag "$OLLAMA_IMAGE" ollama:offline
docker image save -o /srv/rag-kit/ubuntu/images.tar rag-pilot:offline ollama:offline
docker image inspect rag-pilot:offline ollama:offline
```

构建只发生在准备机，Dockerfile 的 apt/pip 和基础镜像需要联网。保存最终镜像，不指望目标机重建得到同样依赖。将 build 日志、镜像 ID 和镜像内 `pip freeze` 结果保存在交付记录，勿包含密钥。

只有 Linux 准备机时，可用最终镜像直接导出 BGE，无需 Windows。以下仍是**联网准备阶段**，先填与交付清单一致的模型 commit：

```bash
mkdir -p /srv/rag-kit/models
export RAG_EMBED_REVISION='REPLACE_WITH_APPROVED_FULL_MODEL_COMMIT'
case "$RAG_EMBED_REVISION" in *REPLACE*) echo '先填写模型 commit'; exit 1;; esac
docker run --rm -e RAG_EMBED_REVISION -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 -v /srv/rag-kit/models:/export rag-pilot:offline python -c "import os; from sentence_transformers import SentenceTransformer; m=SentenceTransformer('BAAI/bge-small-zh-v1.5', revision=os.environ['RAG_EMBED_REVISION'], device='cpu'); m.save('/export/bge-small-zh-v1.5')"
```

在准备机用本次独立目录制作 Ollama 模型（名字冲突先换名称，不删除旧容器）：

```bash
mkdir -p /srv/rag-kit/ollama-prep
docker run -d --name rag-ollama-prep -v /srv/rag-kit/ollama-prep:/root/.ollama ollama:offline
docker exec rag-ollama-prep ollama pull qwen3:4b
docker exec rag-ollama-prep ollama list
docker stop rag-ollama-prep
sudo tar -czf /srv/rag-kit/ubuntu/ollama-data.tgz -C /srv/rag-kit/ollama-prep models
tar -czf /srv/rag-kit/ubuntu/embedding.tgz -C /srv/rag-kit/models bge-small-zh-v1.5
```

打包 models 子目录即可，不携带 Ollama 身份密钥。容器模型目录/硬件运行方式参考 [Ollama Docker](https://docs.ollama.com/docker)。可用第 3 节导出的完整 BGE 目录，但必须用最终 Linux 镜像在无网络下验证：

```bash
docker run --rm --network none -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -v /srv/rag-kit/models:/rag/models:ro rag-pilot:offline python -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('/rag/models/bge-small-zh-v1.5', device='cpu'); print(m.encode(['offline check']).shape)"
```

## 6. 校验、分发与放行

把每个平台包制成单一外层归档，避免传输丢文件。准备机生成 SHA-256，管理员通过**独立可信渠道/签名清单**交付预期值；散列只验完整性，不证明来源可信。

Windows 核验示例（先填可信预期值，路径必须指向收到的外层包）：

```powershell
$expectedDigest = 'REPLACE_WITH_TRUSTED_SHA256'
$actualDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath C:\incoming\rag-kit.zip).Hash
if ($actualDigest -ne $expectedDigest) { throw '校验失败，禁止解压安装' }
```

Linux 使用 `sha256sum /srv/incoming/rag-kit.tar.gz` 并与可信清单逐字比对；不一致停止。核验后按组织流程扫描介质、检查归档路径与符号链接，再解压到新空目录。不要运行来源未知的解压后脚本。

放行前必须在断网、无预置模型缓存的干净验收机完成：

- 从交付包安装所有组件，无补充下载、无隐式拉取。
- 嵌入真实后端 st 就绪，Ollama 标签/摘要正确，实际问答及引用成功。
- 重启后重新就绪；停止服务后备份并恢复到新实例，数据/账号/引用可核对。
- 端口与出口检查符合 [安全验收](SECURITY.md)，IPv4、IPv6 和容器流量都覆盖。
- 完整记录硬件、版本、耗时、通过/失败项；失败包不得交付正式运行。

本次文档编写环境没有 Docker，未执行 Ubuntu 冷安装或 GPU 部署；管理员必须补做上述目标验收，不能将命令示例当作已完成部署。
