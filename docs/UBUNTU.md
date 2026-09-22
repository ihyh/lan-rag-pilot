# Ubuntu 离线局域网服务器

[返回首页](../README.md) · [离线包制作](OFFLINE_PACKAGE.md)

建议基线 Ubuntu 24.04 LTS amd64。服务器集中保存模型/文档，低配电脑只使用浏览器。以下只建立**同权限共享资料库的试点**；有部门/个人资料隔离要求时，当前缺少授权功能，正式上线被阻塞。

本页是部署副本的操作说明，不会自动修改仓库配置。示例使用空目录 /opt/rag、已校验包 /srv/rag-kit。已有实例先 [备份](OPERATIONS.md)，不能直接覆盖或重用未知数据卷。

## 1. 系统与端口前提

由 IT 在无外网路由/出口拒绝策略下，离线安装验收过的 deb 包。只使用包内已核验文件：

```bash
sudo dpkg -i /srv/rag-kit/ubuntu/debs/*.deb
sudo dpkg --audit
sudo systemctl enable --now docker
docker --version
docker compose version
```

若依赖未满足，回到准备机补齐包；不要运行联网 apt 修复。Docker 的管理权限近似主机管理员权限，只有授权运维持有。下面 docker 命令使用已授权账号，必要时按组织政策使用 sudo。

确认 Nginx、Python3 也已离线安装。GPU 可选：驱动及 Container Toolkit 由 IT 离线安装并配置，先用 nvidia-smi 核验主机；容器 GPU 仍需单独验收。无 GPU 时下面配置用 CPU，功能可测试但生成可能慢。

唯一对普通 LAN 客户端开放的入口是内网 HTTPS 443。8088 只在主机回环，11434 不发布到主机。SSH 只向管理网开放；不要把 Docker socket 或数据库暴露给客户端。

## 2. 导入镜像与模型

把源码复制到新空 /opt/rag，确认 scripts/ 存在。导入不会联网：

```bash
cd /opt/rag
docker load -i /srv/rag-kit/ubuntu/images.tar
docker image inspect rag-pilot:offline ollama:offline
```

将镜像 ID 与交付清单核对。后续使用专用新卷；先检查：

```bash
docker volume ls
```

若已有下面任一同名卷，**停止本节**，改走迁移流程，不向其解压。确认三个名称不存在后才创建：

```bash
docker volume create rag-pilot_rag_data
docker volume create rag-pilot_rag_models
docker volume create rag-pilot_ollama_data
docker run --rm --network none -v rag-pilot_rag_models:/target -v /srv/rag-kit/ubuntu:/kit:ro rag-pilot:offline tar -xzf /kit/embedding.tgz -C /target
docker run --rm --network none -v rag-pilot_ollama_data:/target -v /srv/rag-kit/ubuntu:/kit:ro rag-pilot:offline tar -xzf /kit/ollama-data.tgz -C /target
```

归档必须来自已核验的管理员包，不含越界路径/不可信链接。模型卷根目录应是 bge-small-zh-v1.5/；Ollama 卷下是 models/blobs 与 models/manifests。数据卷此时为空。

## 3. 保存独立 Compose 配置

在**部署副本** /opt/rag/docker-compose.yml 中保存以下完整内容。不是与仓库原文件叠加的 override；先保留原配置供审计。不要同时加载其他 Compose 文件或遗留 COMPOSE_FILE 环境变量。

```yaml
name: rag-pilot
services:
  rag:
    image: rag-pilot:offline
    pull_policy: never
    restart: unless-stopped
    env_file: .env
    environment:
      RAG_HOST: "0.0.0.0"
      RAG_PORT: "8088"
      RAG_DATA_DIR: /rag/data
      RAG_DB_PATH: /rag/data/rag.db
      RAG_UPLOAD_DIR: /rag/data/uploads
      RAG_MODELS_DIR: /rag/models
      HF_HUB_OFFLINE: "1"
      TRANSFORMERS_OFFLINE: "1"
    ports:
      - "127.0.0.1:8088:8088"
    volumes:
      - rag_data:/rag/data
      - rag_models:/rag/models:ro
    networks: [isolated]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/ready', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
  ollama:
    image: ollama:offline
    pull_policy: never
    restart: unless-stopped
    environment:
      OLLAMA_HOST: "0.0.0.0:11434"
      OLLAMA_NO_CLOUD: "1"
      OLLAMA_CONTEXT_LENGTH: "8192"
      OLLAMA_NUM_PARALLEL: "1"
    volumes:
      - ollama_data:/root/.ollama
    networks: [isolated]
networks:
  isolated:
    internal: true
volumes:
  rag_data:
    external: true
    name: rag-pilot_rag_data
  rag_models:
    external: true
    name: rag-pilot_rag_models
  ollama_data:
    external: true
    name: rag-pilot_ollama_data
```

只有 Ollama 容器内部监听所有接口，并无对主机/LAN 的 11434 映射。internal 网络是附加限制，不替代宿主机和网络出口控制。

GPU：在驱动、Toolkit 和 Compose 版本通过验证后，可在 ollama 服务级增加 `gpus: all`（至少 Compose 2.30，见 [官方字段说明](https://docs.docker.com/reference/compose-file/services/#gpus)）。这是部署管理员可选修改，不是上面 CPU 示例已启用 GPU。使用 `ollama ps` 验证实际占用，不为多用户盲目增加并发。

## 4. 创建 .env

在 /opt/rag/.env 保存，替换两项 REPLACE 值及内部域名。下列地址仅在上述 Compose 网络内有效：

```dotenv
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://ollama:11434/v1
DEEPSEEK_MODEL=qwen3:4b
DEEPSEEK_TIMEOUT_S=180
RAG_SECRET_KEY=REPLACE_WITH_RANDOM_SECRET
RAG_ROOT_PASSWORD=REPLACE_WITH_STRONG_INITIAL_PASSWORD
RAG_COOKIE_SECURE=true
RAG_SESSION_TTL_HOURS=8
RAG_PUBLIC_ORIGIN=https://rag.intra.example
RAG_EMBED_BACKEND=st
RAG_EMBED_MODEL=/rag/models/bge-small-zh-v1.5
RAG_MAX_CONCURRENT_LLM=1
```

`rag.intra.example` 是占位域名，必须换成组织内部 DNS 名称。用本机 `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` 生成会话密钥，妥善保存。root 初始密码要唯一且强，不提供通用密码。

也可另运行一次随机生成命令得到独立初始口令；不要把密钥与口令设置为相同值。本文建议使用足够长的 URL-safe 随机值，避免 Compose 对 `$` 等字符插值改变原值。自选特殊字符密码时必须按 Compose 语法正确引用并核验，不能直接照搬 Windows 脚本的引号规则。

限制读取并先校验配置（不要把含秘密的完整 config 输出提交到工单）：

```bash
cd /opt/rag
chmod 600 .env
docker compose config --quiet
docker compose config --services
docker compose up -d --pull never --no-build
docker compose ps
docker compose logs --tail 60 rag
docker compose exec ollama ollama list
```

成功标准：只有预期的 rag/ollama 服务，模型标签与清单一致，rag 加载真实嵌入后健康。这里不开浏览器登录 HTTP：Secure Cookie 要求下一步 HTTPS。

## 5. 内网 HTTPS 入口

IT 为实际内网域名签发证书，给客户端安装内部 CA 信任；不要申请需要运行服务器联网续期的证书。将证书链与私钥安全放置，私钥仅服务需要的账号可读。

在 Nginx 的独立站点配置中使用下面示例，替换域名、证书路径后启用；不要覆盖主机其他站点：

```nginx
server {
    listen 443 ssl;
    server_name rag.intra.example;
    ssl_certificate /etc/nginx/tls/rag.crt;
    ssl_certificate_key /etc/nginx/tls/rag.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 26m;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_read_timeout 190s;
    }
}
```

26m 为默认 25 MB 上传留 multipart 开销；代理和应用限制须一起审查。若调整生成超时，代理超时也需匹配。该示例只开 443，不自动提供 HTTP 重定向。

```bash
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
curl --fail http://127.0.0.1:8088/api/ready
```

通过后从批准的客户端打开实际 HTTPS 域名，确认无证书告警，完成 [首次使用](GETTING_STARTED.md)。使用组织 CA 的 curl 可以加 --cacert 指定可信证书，**不要用 -k 把证书错误当成功**。

## 6. 交付前检查

- 验证主机监听与最终端口映射：`ss -lntp`、`docker compose port rag 8088`，后者仅为 127.0.0.1:8088。
- 从允许网段访问 443 成功，非允许网段失败；客户端直连 8088/11434 应失败。检查 IPv6 和容器转发规则。
- 所有互联网出口被网络策略拒绝；HF 离线变量和 OLLAMA_NO_CLOUD=1 已生效，无后台自动更新。
- 重启主机后，Docker、Nginx 启动且问答/引用恢复。只有一个 RAG worker/副本。
- 执行 [备份与恢复演练](OPERATIONS.md)，记录模型摘要和问题验收结果。
- 所有账号是否允许读取同一个资料库？如果不是，**停止上线**。

仓库原 Compose 现在只把 8088 发布到回环地址（`127.0.0.1:8088:8088`），可安全地与本页的 Nginx 反代配合；但企业部署仍应使用本页的独立配置。历史上那份 HTTPS override 示例已从仓库删除，此处仅作说明，不要再去寻找或自行叠加 override 文件——Compose 的 ports 是叠加而非替换，合并机制见 [官方规则](https://docs.docker.com/reference/compose-file/merge/)。Docker 发布端口可能绕过普通 UFW 规则，必须核对实际转发路径，见 [Docker 防火墙说明](https://docs.docker.com/engine/install/ubuntu/)。

本页配置经过文档级核对，尚未在本次编写环境做 Ubuntu 冷启动、HTTPS 或 GPU 验收。完成上述检查才可将本地交付清单标记为通过。
