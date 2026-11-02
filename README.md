# 泊读 · Harbor RSS

一个为 NAS 自托管设计的 RSS 阅读器。三栏阅读、Folo OPML 迁移、全文提取，以及通过 DeepSeek 或 OpenAI 兼容 API 翻译整篇文章。单容器 + SQLite，无需 Redis、PostgreSQL 或外部账号。

> 这是独立实现的首版项目，借鉴 Folo 的阅读方式及 FreshRSS / Miniflux 的订阅迁移思路，没有复制这些项目的源码。它不是 Folo 的服务端，也不兼容 Folo 客户端协议。

## 功能

- 桌面三栏阅读与手机阅读布局；搜索、未读状态、收藏、订阅文件夹。
- RSS 2.0、Atom、JSON Feed；支持 RSSHub 输出的公开订阅 URL。
- OPML / JSON / CSV / TXT 订阅导入，去重及 OPML 导出；嵌套文件夹往返保留。
- 后台定时抓取，单源失败不影响其他订阅；订阅管理显示抓取错误。
- Readability 网页正文提取，清理不可信 HTML。
- 全文翻译：默认先提取网页正文，逐段翻译，不主动截断源文本，不生成摘要。
- 翻译队列、进度、已完成段落持久化、失败续传、重启恢复及结果缓存。
- 原文 / 译文 / 双栏对照；重新打开文章可查看最近翻译任务。
- 可配置 API 基础地址、模型、密钥及目标语言；REST API 可供自己的脚本调用。
- 单用户口令登录、HttpOnly Cookie、Bearer Token、同源写入检查、API Key 加密存储。
- 公网 HTTP 抓取限制、DNS 地址固定及每次重定向检查，防止读取 NAS 内网服务。

## NAS Docker 部署

要求：支持 Docker Compose 的 NAS，能访问 PyPI、Docker Hub 和你的订阅/API 服务。镜像基于 Python 3.12，设计面向 Linux amd64 / arm64；本地未验证 ARM NAS。

```sh
git clone https://github.com/RyanLiu1208/harbor-rss.git
cd harbor-rss
cp .env.example .env
```

编辑 `.env`，设置 **至少 24 位随机访问口令**。推荐生成 32 字节随机值：

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

将输出填到 `ACCESS_TOKEN=` 后面。不要使用示例口令。`DEEPSEEK_API_KEY` 可留空，启动后在网页中设置。

```sh
docker compose up -d --build
docker compose logs -f --tail=100
```

访问 `http://NAS的IP:8080`，用 `.env` 中的 `ACCESS_TOKEN` 登录。端口冲突时修改 `PORT`。绿联、群晖、威联通等 NAS 的 Docker / Container Manager 中，也可以把整个仓库目录作为 Compose 项目导入，工作目录必须包含 Dockerfile、requirements 文件及 app 目录。

Compose 默认使用 Docker 命名卷 `harbor-data`，容器以 UID 10001 的非 root 用户运行，避免 NAS 共享目录权限差异。若改成 `./data:/data`，须先确保该目录对 UID 10001 可写。

### 更新与备份

```sh
git pull --ff-only
docker compose up -d --build
```

更新不会删除数据卷。备份时停止容器，然后复制整个数据目录（包含数据库、WAL 文件和加密密钥）：

```sh
docker compose stop
mkdir -p backup
docker cp harbor-rss:/data/. ./backup/
docker compose start
```

同时安全保管 `.env`。恢复时停止容器，将备份复制到 `/data` 并确保 UID 10001 可读写后启动。`secret.key` 是解密网页中保存的 API Key 所必需的，不能只备份数据库。不要执行 `docker compose down -v`，它会删除数据卷。

## 从 Folo 迁移

1. 在 Folo 中导出 OPML（官方项目支持 OPML 导入导出；不同版本入口位置可能变化）。
2. 在泊读打开「设置与迁移 → 导入与导出」。
3. 选择 `.opml` 文件，点击导入；也可直接粘贴 OPML 内容。
4. 等待后台刷新。订阅抓取错误可在「订阅管理」查看。

保留订阅 URL、自定义名称及文件夹；重复 URL 跳过。**OPML 不包含文章历史、已读状态或收藏，也不包含 Folo 平台私有内容的访问能力**。这些状态无法通过 OPML 迁移；这里没有声称与 Folo 完全等价。Folo 社交视图、列表、视频或无公开 feed 的内容，需要先取得可访问的 RSSHub/RSS 地址。付费墙、登录、验证码和纯动态网页不能保证提取。

### 其他导入格式

JSON 支持字符串 URL 数组、对象数组，或 `{ "feeds": [...] }` / `{ "subscriptions": [...] }`。

```json
[
  {"url":"https://example.com/feed.xml", "title":"示例订阅", "folder":"科技/AI"},
  {"feed":{"url":"https://example.org/atom.xml","title":"另一个订阅"}, "category":"设计"}
]
```

CSV 使用带表头的 `url,title,folder`；也支持地址字段 `xmlUrl` / `feedUrl`。

```csv
url,title,folder
https://example.com/feed.xml,示例订阅,科技/AI
```

TXT：每行一个完整 HTTP/HTTPS URL，空行和 `#` 开头的注释忽略。导入文件最大 4 MB，单次最多 5000 个订阅。任意无效条目会使该次导入整体失败，避免不明确的部分导入。OPML 的嵌套目录用 `/` 表示，因此文件夹名本身不要使用 `/`。

## DeepSeek 全文翻译

打开「设置与迁移 → AI 翻译」：

| 设置 | 默认值 |
| --- | --- |
| API 基础地址 | `https://api.deepseek.com` |
| 模型 | `deepseek-flash` |
| API Key | 你的 DeepSeek API Key |
| 目标语言 | 简体中文 |

`deepseek-flash` 来自当前 DeepSeek 官方模型文档；可按服务提供方的可用模型手动修改。DeepSeek 请求默认关闭思考模式以适合翻译。其他兼容服务不会收到此专用参数。基础地址填到 `/v1` 等 API 前缀为止，**不要填写 `/chat/completions`**，程序会自动追加该路径。

选择文章，点击「全文翻译」。如果还没有提取正文，会先尝试抓取原网页全文；提取失败则报错，不会悄悄把摘要当全文。翻译对象是抓取器提取出的正文，无法保证包含评论、嵌入式媒体或网站登录后的内容。

每段最多 5000 字符，逐段保存结果；遇到请求异常自动重试最多 3 次。模型返回非 `stop` 结束状态会被视为不完整译文，任务失败后可继续重试；此前成功的段落不会再次调用 API。进程退出可能发生在服务商已经计费、但本地尚未保存该段结果之间，这种情况下恢复会重复该段调用。

全文内容、语言、模型、API 基础地址相同且已有成功任务时复用缓存。原始排版中的表格、图片、链接样式不会完整保留到纯文本译文；代码与 URL 交给模型按提示保留。原文仍可随时查看。AI 译文质量和内容完整性应按需要核对。

翻译只在点击按钮或调用接口后发生，会把文章正文发送至你填写的服务地址并产生该服务的 API 费用。没有预置 Key，不会使用 Folo 的 AI 账户。

## REST API

所有 `/api/*` 接口（登录除外）需要 `Authorization: Bearer <ACCESS_TOKEN>` 或已登录 Cookie。`GET /health` 是公开健康检查。完整 OpenAPI JSON 可在登录后通过 `/api/schema` 查看。

```sh
export HARBOR_URL=http://NAS的IP:8080
export HARBOR_TOKEN='你的访问口令'

curl "$HARBOR_URL/api/feeds" -H "Authorization: Bearer $HARBOR_TOKEN"

curl "$HARBOR_URL/api/articles?view=unread&limit=20" \
  -H "Authorization: Bearer $HARBOR_TOKEN"

# 1 是已有文章 ID；默认自动提取全文。立即返回任务 ID（首次提取可能较慢）。
curl -X POST "$HARBOR_URL/api/articles/1/translate" \
  -H "Authorization: Bearer $HARBOR_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"language":"简体中文"}'

curl "$HARBOR_URL/api/jobs/返回的job_id" \
  -H "Authorization: Bearer $HARBOR_TOKEN"
```

| 方法 / 路径 | 用途 |
| --- | --- |
| `POST /api/login` | `{ "token": "访问口令" }`，建立 Cookie 会话 |
| `POST /api/logout` | 清除当前 Cookie |
| `GET /api/feeds` | 订阅、文件夹、未读计数与错误 |
| `POST /api/feeds` | `{ "url": "...", "title": "可选", "folder": "可选" }` |
| `DELETE /api/feeds/{id}` | 删除订阅及文章、翻译任务 |
| `POST /api/import` | `{ "content": "OPML/JSON/CSV/TXT 字符串" }` |
| `GET /api/export` | OPML 下载 |
| `POST /api/refresh` | 后台刷新所有订阅 |
| `GET /api/articles` | `feed_id, folder, view=all/unread/starred, q, offset, limit` |
| `GET /api/articles/{id}` | 正文、提取后的全文和最近翻译任务 ID |
| `PATCH /api/articles/{id}` | `{ "read": true, "starred": true }`，两个字段均可省略 |
| `POST /api/articles/{id}/extract` | 抓取并缓存网页正文 |
| `POST /api/articles/{id}/translate` | `{ "language": "简体中文", "force": false, "use_feed_content": false }` |
| `GET /api/jobs/{id}` | `queued/running/done/failed`、进度及译文 |
| `POST /api/jobs/{id}/retry` | 继续失败任务的剩余段落 |
| `GET /api/settings` | 获取配置；仅返回是否存在 Key，不返回 Key |
| `PUT /api/settings` | `base_url, model, language, api_key`；Key 留空保留 |
| `GET /api/schema` | 完整 OpenAPI schema |

显式设置 `use_feed_content: true` 可翻译订阅中已有的正文，**该内容也可能只是摘要**。`force: true` 会新建任务并重新产生 API 调用。`clear_key: true` 可删除网页中保存的 Key；若容器环境仍含 `DEEPSEEK_API_KEY`，会回退使用环境 Key，需要同时清空环境变量并重建容器。

## 运行约束

- 单用户、自托管首版；无多人权限、推送通知、Folo 跨设备账号同步或 Google Reader/Fever API 兼容层。
- 只运行 **一个 Uvicorn worker、一个容器副本**。后台任务在进程内运行，状态持久化在 SQLite；增加 worker 会导致重复抓取或翻译。
- 每个源每次最多处理最近 500 篇文章；JSON Feed 条目的日期暂按首次抓取时间保存。没有自动删除旧文章，长期使用应检查数据卷空间。
- 翻译同时执行一个任务，队列最多 100 个待处理任务；串行抓取，海量订阅首次同步会较慢。
- 不访问私有地址、回环地址、链路本地地址和 Tailscale CGNAT 地址。自建内网 RSSHub/API 不能直接连接；需要使用可访问的公网 HTTPS 服务。本版本不提供放开内网限制的开关。
- 浏览器中正文图片可直接访问原站图片，可能暴露读者 IP；API Key 只在服务端使用。
- 内网 HTTP 访问时 `COOKIE_SECURE=false`。对外访问应经 HTTPS 反向代理并设为 `true`，代理须保留原 Host，避免被同源写入检查拒绝；不信任 `X-Forwarded-*`。
- 改动 `ACCESS_TOKEN` 并重建容器会撤销此前 Cookie 和 API 访问凭据。API Token 与管理员口令权限相同。
- 不支持外部 HTTP 代理环境变量；网络请求固定至验证后的公网 IP，TLS 仍按原始域名验证证书。

## 开发与验证

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ACCESS_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn app.main:app --host 127.0.0.1 --port 8080
python -m pytest -q
```

运行环境为 Python 3.12+。前端为原生 JavaScript/CSS，无 npm 构建步骤，无远程字体和运行时 CDN 依赖。测试使用临时数据库和模拟翻译服务，不需要真实 API Key。

本地已验证 Python 后端测试、JS 语法和真实公网 RSS 抓取。真实 DeepSeek 计费调用尚未验证；Windows 开发环境没有 Docker，镜像构建与容器健康检查由 `.github/workflows/ci.yml` 提供，需查看 GitHub Actions 的实际结果。浏览器自动化不可用时不将视觉测试列为已通过。

## 参考与许可

- [Folo](https://github.com/RSSNext/Folo)：阅读交互和 OPML 迁移参考。
- [FreshRSS](https://github.com/FreshRSS/FreshRSS)：自托管及开放订阅管理参考。
- [Miniflux API](https://miniflux.app/docs/api.html)：REST 接口及 OPML 工作流参考。
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 与 [模型文档](https://api-docs.deepseek.com/quick_start/pricing/)：翻译接口。

本项目原创源码使用 MIT 许可；Python 依赖继续适用各自许可证。
