# comfyui2api

把 **ComfyUI** 封装成一个 **OpenAI 兼容**的 HTTP API 服务，并支持：

- 文生图 / 图生图 / 文生视频 / 图生视频（以 `comfyui-api-workflows/*.json` 为工作流来源）
- 热加载（监听工作流目录变更，自动重新加载）
- 队列与任务状态（pending/queued/running/completed/failed）
- 进度推送：通过连接 ComfyUI 的 `/ws?clientId=...` WebSocket 接口，把执行节点/进度/错误等事件转发给前端 WebSocket，实现实时进度条
- 兼容 New-Api

## 目录

- 工作流目录：`comfyui-api-workflows/`（必须是 ComfyUI 的 **File -> Export (API)** 格式）
- 输出目录：默认 `runs/`（每个任务一个子目录）

## 快速开始（本机运行）

1) 确保 ComfyUI 已启动（默认 `http://127.0.0.1:8188`）。

2) 安装依赖并启动：

```powershell
cd E:\AI_Workstation\comfyui2api
python -m pip install -r .\requirements.txt
python -m pip install -e .

$env:COMFYUI_BASE_URL = "http://127.0.0.1:8188"
$env:COMFYUI_INPUT_DIR = "E:\\AI_Workstation\\ComfyUI_windows_portable\\ComfyUI\\input"

python -m comfyui2api
```

服务默认监听 `0.0.0.0:8000`。

### ComfyUI 在 WSL 中运行

如果 `comfyui2api` 跑在 Windows，而 ComfyUI 跑在 WSL，通常可以直接继续使用：

```powershell
$env:COMFYUI_BASE_URL = "http://127.0.0.1:8188"
$env:IMAGE_UPLOAD_MODE = "comfy"
```

推荐把 `IMAGE_UPLOAD_MODE` 设为 `comfy`，这样输入图片会通过 ComfyUI 的 HTTP 上传接口进入 WSL 内的 `input/`，不依赖 Windows 和 WSL 共享文件路径。

只有当你明确配置了一个 Windows 可访问的 WSL `input` 目录时，才建议使用 `local` 或 `auto` fallback。

## 环境变量

- `API_LISTEN`：默认 `0.0.0.0`
- `API_PORT`：默认 `8000`
- `API_TOKEN`：可选，设置后需要 `Authorization: Bearer <token>`
- `PUBLIC_BASE_URL`：可选，生成输出文件 URL 用；不设置则根据请求自动推断

- `COMFYUI_BASE_URL`：默认 `http://127.0.0.1:8188`
- `IMAGE_UPLOAD_MODE`：`auto|comfy|local`，默认 `auto`
  - `comfy`：走 ComfyUI `POST /upload/image`（推荐：API 与 ComfyUI 不共享磁盘时）
  - `local`：写本地 `COMFYUI_INPUT_DIR`
  - `auto`：优先 `comfy`，失败再 fallback 到 `local`
- `COMFYUI_INPUT_DIR`：ComfyUI 的 `input` 目录（当 `IMAGE_UPLOAD_MODE=local` 或 `auto` fallback 时需要；如果 ComfyUI 在 WSL，通常不需要配置）
- `WORKFLOWS_DIR`：默认 `.\comfyui-api-workflows`
- `RUNS_DIR`：默认 `.\runs`
- `INPUT_SUBDIR`：默认 `comfyui2api`（写入 `input` 下的子目录）

- `WORKER_CONCURRENCY`：默认 `1`（同时跑多少个任务）

- `DEFAULT_TXT2IMG_WORKFLOW`：默认 `文生图_z_image_turbo.json`
- `DEFAULT_IMG2IMG_WORKFLOW`：默认 `图生图_flux2.json`
- `DEFAULT_TXT2VIDEO_WORKFLOW`：默认空（需要你提供对应工作流）
- `DEFAULT_IMG2VIDEO_WORKFLOW`：默认 `img2video.json`

## API

### OpenAI 兼容

- `GET /v1/models`：把工作流列表作为 models 返回
- `POST /v1/images/generations`：文生图
- `POST /v1/images/edits`：图生图（multipart，字段 `image`）
- `POST /v1/images/variations`：图生图变体（multipart，字段 `image`）
- `POST /v1/videos`：新API/OpenAI 视频任务创建（multipart；可选 `input_reference` 作为图生视频输入）
- `GET /v1/videos/{video_id}`：查询视频任务状态（processing/succeeded/failed + progress）
- `GET /v1/videos/{video_id}/content`：下载视频内容
- （New-API 格式）`POST /v1/video/generations`：创建视频生成任务（JSON，返回 task_id/status）
- （New-API 格式）`GET /v1/video/generations/{task_id}`：查询任务状态（queued/in_progress/completed/failed）
- （兼容旧接口）`POST /v1/videos/generations`：文生视频（需要配置默认工作流）
- （兼容旧接口）`POST /v1/videos/edits`：图生视频（multipart，字段 `image`）

默认是**同步**返回；如需异步（返回 `job_id` 以便前端进度条），在请求里加 `x-comfyui-async: 1`。

示例（文生图）：

```bash
curl -s http://127.0.0.1:8000/v1/models

curl -s -X POST http://127.0.0.1:8000/v1/images/generations -H "Content-Type: application/json" -d "{\"prompt\":\"a cute cat, pixel art\"}"
```

异步示例（返回 `job_id`，用于前端进度条）：

```bash
curl -s -X POST http://127.0.0.1:8000/v1/images/generations -H "Content-Type: application/json" -H "x-comfyui-async: 1" -d "{\"prompt\":\"a cute cat, pixel art\"}"
```

### 任务/队列（扩展）

- `GET /v1/workflows`：列出 `WORKFLOWS_DIR` 下已加载的工作流（含 kind/mtime）
- `POST /v1/jobs`：通用提交（可指定 `workflow` / `prompt_node` / `image_node` / `overrides` 等）
- `GET /v1/jobs/{job_id}`：查询任务状态
- `GET /v1/queue`：队列概览
- `WS /v1/jobs/{job_id}/ws`：实时事件（progress/executing/status/error/completed）

#### 调用方：如何使用工作流 & 替换 prompt/image

1) **准备工作流文件**  
把工作流导出为 **API 格式**（ComfyUI：`File -> Export (API)`），放到 `WORKFLOWS_DIR`（默认 `./comfyui-api-workflows`）下。

2) **列出工作流（给调用方选 model/workflow）**

```bash
curl -s http://127.0.0.1:8000/v1/workflows
```

（可选）**查看某个工作流可替换的节点引用（prompt/image 候选）**

```bash
curl -s http://127.0.0.1:8000/v1/workflows/img2video.json/targets
```

3) **提交任务：替换提示词（prompt/negative_prompt）**

- 你可以直接传 `prompt` / `negative_prompt`，服务会尝试在工作流里**自动定位**可替换的文本节点。  
- 如果工作流里有多个候选文本节点导致**无法唯一定位**，会返回报错并列出候选项；这时给出明确的 `prompt_node` / `negative_prompt_node` 重试即可。
- 节点引用格式：`"<node_id>.<input_key>"`（例如 `68:6.text`、`57:27.text`）。

```bash
curl -s -X POST http://127.0.0.1:8000/v1/jobs -H "Content-Type: application/json" -d @- <<'JSON'
{
  "kind": "txt2img",
  "workflow": "文生图_z_image_turbo.json",
  "prompt": "a cute cat, pixel art",
  "prompt_node": "57:27.text"
}
JSON
```

4) **提交任务：替换输入图片（LoadImage）**

- 如果你的工作流包含 `LoadImage`，可以用下面两种方式替换：
  - 传 `image`：ComfyUI `input/` 目录下的**相对路径**（例如 `comfyui2api/xxx.jpg`）
  - 传 `image_base64`：base64 或 data URL（服务会按 `IMAGE_UPLOAD_MODE` 自动上传到 ComfyUI 或写入 `COMFYUI_INPUT_DIR/INPUT_SUBDIR`）
- 同样地，如果有多个 `LoadImage` 候选节点，指定 `image_node`（例如 `46.image`）即可。

```bash
curl -s -X POST http://127.0.0.1:8000/v1/jobs -H "Content-Type: application/json" -d @- <<'JSON'
{
  "kind": "img2img",
  "workflow": "图生图_flux2.json",
  "prompt": "make it cinematic lighting",
  "image": "comfyui2api/your_input.jpg",
  "image_node": "46.image"
}
JSON
```

5) **通用替换：overrides（改任意节点输入）**

当你要改 seed/steps/cfg/尺寸/任意自定义节点参数时，用 `overrides`（字典）：

```json
{
  "overrides": {
    "57:3.seed": 123,
    "57:3.steps": 6
  }
}
```

提示：`node_id` 和 `input_key` 最可靠的获取方式是直接打开你的 API 工作流 JSON，查找目标节点的 key（例如 `"57:3"`）以及 `inputs` 里的字段名（例如 `"seed"`、`"text"`、`"image"`）。

## Docker（可选）

`docker-compose.yml` 已提供一个基础模板（需要你按实际网络填写 `COMFYUI_BASE_URL` 和 `COMFYUI_INPUT_DIR`）。
