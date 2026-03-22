# 🚀 comfyui2api

将 **ComfyUI** 封装为 **OpenAI 兼容** 的 HTTP API 服务，让你可以像调用大型语言模型一样，无缝对接现有的 AI 应用和前端界面。

## ✨ 核心特性

- 🎨 **多模态支持**：文生图 / 图生图 / 文生视频 / 图生视频（以 `comfyui-api-workflows/*.json` 为工作流来源）。
- 🔄 **热加载支持**：监听工作流目录变更，修改工作流后自动重新加载，无需重启服务。
- ⏳ **队列与状态管理**：完善的任务生命周期（`pending` / `queued` / `running` / `completed` / `failed`）。
- 📡 **实时进度推送**：桥接 ComfyUI 的 WebSocket 接口，将执行节点、进度、错误等事件透传给前端，轻松实现实时进度条。
- 🤝 **全面兼容**：原生兼容 **New-Api** 等聚合分发系统。

---

## 📂 目录约定

- **工作流目录**：`comfyui-api-workflows/`（工作流必须是 ComfyUI 的 **File -> Export (API)** 格式）
- **输出目录**：`runs/`（默认设置，每个任务生成一个独立的子目录）

---

## ⚡ 快速开始（本机运行）

> 💡 **前提条件**：确保你的 ComfyUI 已经启动（默认地址：`http://127.0.0.1:8188`）。

### 1. 手动启动

安装依赖并启动服务：

```powershell
cd E:\AI_Workstation\comfyui2api
uv sync --locked

# 配置环境变量
$env:COMFYUI_BASE_URL = "http://127.0.0.1:8188"
$env:COMFYUI_INPUT_DIR = "E:\\AI_Workstation\\ComfyUI_windows_portable\\ComfyUI\\input"

# 启动 API 服务
uv run --locked --no-sync -m comfyui2api
```
服务默认监听在 `0.0.0.0:8000`。

### 2. 🖱️ 一键启动（推荐 Windows 用户）

在 Windows 环境下，可以直接双击 `start.bat` 或使用 PowerShell 启动脚本：

```powershell
.\start.ps1
```

**一键脚本的自动化特性：**
- 使用 `uv sync --locked` 管理项目虚拟环境与依赖。
- 默认使用项目级 `.venv\Scripts\python.exe` 作为运行解释器。
- 默认设置 `COMFYUI_BASE_URL=http://127.0.0.1:8188` 与 `IMAGE_UPLOAD_MODE=comfy`。
- 自动探测 ComfyUI 是否可达。
- 如果请求的端口被占用或保留，会自动回退寻找下一个可用端口（请留意终端打印的 `Listening on:` 实际端口）。

**常用启动参数示例：**
```powershell
.\start.ps1 -ListenHost 127.0.0.1 -Port 9000
.\start.ps1 -CheckOnly       # 仅检查环境不启动
.\start.ps1 -SkipComfyCheck  # 跳过 ComfyUI 连通性检查
.\start.ps1 -EnvFile .\.env  # 指定环境变量文件
```

> ⚠️ **注意**：如果 `.env` 中设置了 `COMFYUI_STARTUP_CHECK=true` 且 ComfyUI 当前不可达，API 会在启动阶段直接退出。如果想先启动 API 服务，请在 `.env` 中设置为 `false`。

### 🐧 WSL 用户特别说明

如果 `comfyui2api` 运行在 Windows 系统上，而 ComfyUI 运行在 WSL 中，通常无需复杂配置，只需：

```powershell
$env:COMFYUI_BASE_URL = "http://127.0.0.1:8188"
$env:IMAGE_UPLOAD_MODE = "comfy"
```

> 💡 **提示**：推荐将 `IMAGE_UPLOAD_MODE` 设为 `comfy`。这样输入图片会直接通过 ComfyUI 的 HTTP 接口上传到 WSL 的 `input/` 目录中，完全无需配置 Windows 与 WSL 的共享路径！只有明确配置了可互访的路径时，才建议使用 `local` 或 `auto` 模式。

---

## ⚙️ 环境变量

你可以通过系统环境变量或 `.env` 文件进行配置：

### 基础与网络
| 变量名 | 默认值 | 描述 |
| --- | --- | --- |
| `API_LISTEN` | `0.0.0.0` | 绑定的 IP 地址 |
| `API_PORT` | `8000` | 监听的端口 |
| `API_TOKEN` | *空* | 设置后调用接口需提供 `Authorization: Bearer <token>` |
| `PUBLIC_BASE_URL` | *自动推断* | 生成输出文件的绝对 URL 域名前缀 |

### ComfyUI 交互
| 变量名 | 默认值 | 描述 |
| --- | --- | --- |
| `COMFYUI_BASE_URL` | `http://127.0.0.1:8188` | ComfyUI 服务的地址 |
| `COMFYUI_STARTUP_CHECK`| `true` | 启动时探测系统状态，失败则退出 |
| `IMAGE_UPLOAD_MODE` | `auto` | `comfy` (调接口上传，推荐) / `local` (写本地文件) / `auto` (优先 comfy，失败回退) |
| `COMFYUI_INPUT_DIR` | *空* | ComfyUI 的 `input` 本地绝对路径 (仅在写本地模式下需要) |

### 路径与并发控制
| 变量名 | 默认值 | 描述 |
| --- | --- | --- |
| `WORKFLOWS_DIR` | `.\comfyui-api-workflows` | API 工作流存放目录 |
| `RUNS_DIR` | `.\runs` | 任务输出文件的存放目录 |
| `INPUT_SUBDIR` | `comfyui2api` | 写入 ComfyUI input 时的专属子目录 |
| `WORKER_CONCURRENCY` | `1` | 同时并发运行的任务数量 |
| `JOB_RETENTION_SECONDS`| `604800` (7天) | 已完成/失败任务在内存和磁盘中的保留时间 |
| `MAX_JOBS_IN_MEMORY` | `1000` | 内存中最多保留的任务记录数 |
| `JOB_CLEANUP_INTERVAL_S`| `60` | 后台清理过期任务的扫描间隔（秒） |

### 默认工作流与安全
| 变量名 | 默认值 | 描述 |
| --- | --- | --- |
| `DEFAULT_TXT2IMG_WORKFLOW` | `文生图_z_image_turbo.json` | 默认文生图工作流 |
| `DEFAULT_IMG2IMG_WORKFLOW` | `图生图_flux2.json` | 默认图生图工作流 |
| `DEFAULT_TXT2VIDEO_WORKFLOW`| *空* | 默认文生视频工作流（需自行提供） |
| `DEFAULT_IMG2VIDEO_WORKFLOW`| `img2video.json` | 默认图生视频工作流 |
| `SIGNED_URL_SECRET` | 继承 `API_TOKEN` | 媒体下载短期签名的加密密钥 |
| `SIGNED_URL_TTL_SECONDS` | `3600` | 生成的媒体访问链接有效期（秒） |

---

## 🌐 API 接口说明

### 🤖 OpenAI 兼容接口

默认情况下接口均为 **同步返回**。如需异步并返回 `job_id` (以便前端渲染进度条)，请在 Request Header 中加上 `x-comfyui-async: 1`。

- `GET /v1/models`：将工作流列表伪装为 models 返回。
- `POST /v1/images/generations`：文生图。
- `POST /v1/images/edits`：图生图（需提交 multipart，字段为 `image`）。
- `POST /v1/images/variations`：图生图变体（需提交 multipart，字段为 `image`）。
- `POST /v1/videos`：视频任务创建（支持 JSON 或 multipart；可选 `input_reference` 作为图生视频输入）。
- `GET /v1/videos/{video_id}`：查询视频状态（返回进度及短期签名 `url`）。
- `GET /v1/videos/{video_id}/content`：直接下载视频流。

#### 兼容其他协议的扩展接口
- `POST /v1/video/generations`：New-API 标准的视频生成任务创建。
- `GET /v1/video/generations/{task_id}`：New-API 标准的任务状态查询。
- `POST /v1/videos/generations`：兼容旧版接口的文生视频。
- `POST /v1/videos/edits`：兼容旧版接口的图生视频。

**调用示例（文生图 同步）：**
```bash
curl -s -X POST http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a cute cat, pixel art"}'
```

**调用示例（文生图 异步）：**
```bash
curl -s -X POST http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "x-comfyui-async: 1" \
  -d '{"prompt":"a cute cat, pixel art"}'
```

> 💡 **媒体访问说明**：
> - 响应体里的 `url` / `video_url` / 图片 `response_format=url` 返回的是 **短期签名链接**（防止未经授权的直链盗刷）。
> - 标准下载接口（如 `/content`）依然支持 `Authorization: Bearer <token>` 访问。

### 🛠️ 任务 / 队列（原生扩展接口）

如果 OpenAI 格式不能满足你的复杂工作流需求，可以直接调用原生接口：

- `GET /v1/workflows`：列出所有工作流（包含最后修改时间、类型及加载失败的排错信息）。
- `GET /v1/workflows/{name}/targets`：查看 prompt/image 可供替换的候选节点。
- `GET /v1/workflows/{name}/parameters`：查看工作流的高级映射参数和建议模板。
- `GET /v1/workflows/{name}/parameters/template`：直接返回可复制的 sidecar 模板 JSON。
- `POST /v1/jobs`：通用任务提交（最强大的接口，可指定所有底层节点重写）。
- `GET /v1/jobs/{job_id}`：查询任务详情。
- `GET /v1/queue`：查看当前队列概览。
- `WS /v1/jobs/{job_id}/ws`：WebSocket 端点，推送实时事件流（progress/executing/status 等）。

---

## 📖 进阶用法：如何玩转工作流与参数替换

### 1. 准备你的工作流
在 ComfyUI 中调好效果后，点击 `File -> Export (API)` 保存为 JSON，放入 `WORKFLOWS_DIR`（默认 `comfyui-api-workflows`）中。

### 2. Sidecar 高级参数映射（可选）
如果你想让前端传 `seed`、`fps` 就能自动修改工作流里的对应节点，可以为工作流创建一个同名配置文件（存放在 `.comfyui2api` 文件夹中）：

```text
comfyui-api-workflows/
  ├── img2video.json
  └── .comfyui2api/
      └── img2video.params.json  # 配置映射
```

**`img2video.params.json` 示例：**
```json
{
  "version": 1,
  "kind": "img2video",
  "prompt_node": "339.custom_prompt",
  "image_node": "167.image",
  "parameters": {
    "fps": {
      "type": "float",
      "maps": [{"target": "285.value"}]
    },
    "duration": {
      "type": "int",
      "maps":[{"target": "291.value"}]
    }
  }
}
```

### 3. 提交任务：动态替换提示词
系统会自动猜测哪个节点是输入 Prompt。如果存在多个候选节点导致报错，只需在请求中显式指定 `prompt_node`（格式为 `节点ID.字段名`）。

```bash
curl -s -X POST http://127.0.0.1:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "kind": "txt2img",
  "workflow": "文生图_z_image_turbo.json",
  "prompt": "a cute cat, pixel art",
  "prompt_node": "57:27.text"
}
JSON
```

### 4. 提交任务：替换输入图片
如果工作流包含 `LoadImage` 节点，支持两种传入方式：
- **`image`**: 相对路径（如 `comfyui2api/xxx.jpg`）。
- **`image_base64`**: Base64 字符串或 Data URL（API 会自动帮你上传）。

```bash
curl -s -X POST http://127.0.0.1:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "kind": "img2img",
  "workflow": "图生图_flux2.json",
  "prompt": "make it cinematic lighting",
  "image": "comfyui2api/your_input.jpg",
  "image_node": "46.image"
}
JSON
```

### 5. `overrides`（重写任意节点）
对于尺寸、Steps、CFG、Seed 等任意细节修改，你可以通过 `overrides` 字段精确制导：

```json
{
  "overrides": {
    "57:3.seed": 123,
    "57:3.steps": 6,
    "12:0.width": 1024
  }
}
```
> 💡 **获取 `node_id` 的最稳妥方式**：用文本编辑器打开你导出的 API 工作流 JSON，直接查找你要改的节点 ID（如 `"57"`），以及 `inputs` 字典里的目标 Key。

---

## 🐳 Docker 部署（可选）

项目根目录下提供了 `docker-compose.yml` 基础模板。部署前，请根据你实际的内网/外网网络环境，在配置文件中填入对应的 `COMFYUI_BASE_URL` 和相关参数，直接 `docker compose up -d` 即可起飞。
