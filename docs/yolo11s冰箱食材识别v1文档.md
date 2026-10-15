# 独立食材视觉分析系统 V1：需求文档与接口文档

版本：V1.0 开工基线  
日期：2026-10-15  
交付形式：两份独立 Markdown 文档。当前为文档内容草案，尚未创建文件。

默认分工：你负责前端、摄像头采集和接口联调；Lrio 负责后端、数据、模型训练及视觉算法。先冻结接口并使用模拟结果并行开发，再接真实模型。

---

# 文档一：详细需求文档

建议文件名：`docs/requirements.md`

## 1. 项目定位

建设独立的 PC 网页食材视觉分析系统，支持：

1. 单张图片食材检测。
2. 上传视频的目标检测、跟踪及多帧融合。
3. 浏览器实时摄像头检测与跟踪。
4. 识别结果展示及 JSON 导出。
5. 后续通过接口接入 MealFlow。

系统负责输出视觉观察结果，不直接操作 MealFlow 数据库。

### 1.1 首版不包含

- 手机端专项适配、原生 App、桌面安装包。
- RTSP／网络摄像头接入、多个摄像头同时分析。
- 自动判断食材放入或取出。
- 自动推算重量、体积、保质期及新鲜度。
- 跨视频、跨会话的同一食物身份识别。
- 在线标注平台、网页训练平台及模型热切换。
- 公网多用户账户、计费与大规模并发。

首版面向本机及受信局域网开发使用；如需公网发布，另行增加身份验证、权限和配额。

## 2. 使用方式与部署

- 用户通过 PC 浏览器访问 Vue 网页。
- 浏览器采集当前用户电脑的摄像头。
- FastAPI 后端调用本地模型完成分析。
- 后端可部署在你的电脑、Lrio 的电脑或专用机器；承载推理的机器必须在线。
- 训练环境与应用运行环境分开管理。
- 开发时通过 Vite 代理访问后端，正式部署使用同源 HTTP／WebSocket 反向代理。
- 本机摄像头开发使用 `localhost`；局域网网页使用摄像头时配置 HTTPS／WSS。

## 3. 功能需求

### 3.1 系统工作台

首页展示：

- 当前模型名称、版本和类别表版本。
- 支持识别的食材类别。
- 后端连接及模型可用状态。
- 图片识别、视频分析、实时摄像头三个入口。
- 当前视觉任务占用状态。

页面明确区分真实模型与模拟模式。模拟模式必须醒目标注，不作为效果验收依据。

### 3.2 图片识别

操作流程：

**选择图片 → 预览 → 开始识别 → 查看结果 → 导出或重新识别。**

具体要求：

- 单次上传一张图片。
- 支持 JPEG、PNG、静态 WebP。
- 左侧展示图片及检测框，右侧展示检测列表与分类汇总。
- 检测列表包括类别名称、置信度及目标编号。
- 分类汇总统计当前图片中的检测框数量。
- 点击列表项，高亮对应检测框。
- 支持显示或隐藏类别名称、置信度和检测框。
- 显示处理耗时。
- 无结果属于正常分析结果，提示更换角度、改善光照或检查支持类别。
- 导出结果 JSON；首版不提供识别清单修改入库功能。

图片纠正方向、透明区域合成和缩放由前后端统一约定，保证检测框与图像一致。

### 3.3 上传视频分析

操作流程：

**上传视频 → 创建分析任务 → 查看进度 → 播放原视频和检测叠层 → 查看轨迹与时序摘要 → 导出 JSON。**

具体要求：

- 首版接收 MP4 容器、H.264 视频编码，允许音轨但不分析音频。
- 限制 100 MB、60 秒、最高 1920×1080、最高 60 FPS。
- 后端统一旋转方向、移除音轨并生成可播放的规范化视频。
- 按目标 5 FPS 采样，源帧率低于该值时不插帧。
- 保存每个分析帧的源视频时间、检测框、轨迹编号与分类结果。
- 播放使用规范化视频；到下一分析帧前保持上一帧检测结果，并标注分析采样率。
- 支持跳转到某条轨迹的首次出现时间。
- 支持查看轨迹代表帧。
- 显示分析帧进度；规范化等阶段仅显示阶段名称，不展示虚假百分比。
- 支持取消分析及删除任务。
- 一次最多运行一个视觉任务，不提供等待队列。

首版不生成带框结果视频下载；通过网页播放叠层和 JSON 导出满足展示需求。

### 3.4 实时摄像头

操作流程：

**进入页面 → 授权摄像头 → 选择设备 → 开始分析 → 查看结果 → 停止并导出会话摘要。**

具体要求：

- 支持浏览器内置或 USB 摄像头。
- 初始请求 1280×720、30 FPS，设备不支持时接受浏览器实际协商参数。
- 原始预览与分析结果分开展示。
- 原始预览保持流畅；结果区域展示已分析的精确帧及其检测框，避免把旧框错误叠到新画面。
- 分析帧最长边不超过 960 像素，JPEG 质量 0.8。
- 目标发送频率不超过 5 FPS，且只允许一帧等待结果；处理较慢时自动降低发送频率，不积压。
- 展示分析 FPS、服务端处理耗时及客户端往返耗时。
- 只检测视频，不申请麦克风权限。
- 切换摄像头必须先结束当前会话，新会话重新计算轨迹编号。
- 摄像头权限被拒绝、设备占用、网络断开及模型异常均提供明确提示。
- 停止、离开页面或连接异常时停止采集并释放设备。
- 不自动重连续接旧轨迹；用户重新开始时创建新会话。
- 单会话最多 10 分钟，到时自动结束，可重新开始。

### 3.5 时序分析的具体定义

首版通过“检测＋跟踪＋跨帧融合”实现时序分析：

| 能力 | 定义 |
|---|---|
| 目标跟踪 | 在同一段连续视频或实时会话中关联目标 |
| 稳定目标 | 同一轨迹至少被观察 3 次，首次至末次观察间隔至少 400 ms |
| 类别融合 | 对同一轨迹的逐帧分类按置信度累计投票，最高累计得分类别作为融合类别 |
| 出现时间 | 该轨迹首次和末次被实际检测到的时间 |
| 观察时长 | 相邻有效观察时间差之和；超过 1 秒的间隔不累计 |
| 代表帧 | 轨迹中属于融合类别、置信度最高的观察帧；并列取最早帧 |
| 分类摘要 | 稳定轨迹数，以及各分析帧中该类别检测框数量的峰值 |

融合得分定义为：获胜类别累计置信度／所有类别累计置信度，不解释为真实正确率。

轨迹数与单帧峰值都必须标注为视觉统计，不能称为真实库存数量。完全遮挡、出画后返回或跟踪中断可能产生新轨迹。

初始采用 BoT-SORT，关闭可选 ReID，显式指定跟踪配置，避免受库默认跟踪器变化影响；每个会话独立维护跟踪状态。[官方跟踪说明](https://docs.ultralytics.com/modes/track)

## 4. 数据、模型与训练

### 4.1 类别管理

正式食材清单由你后续提供，当前不擅自确定。

收到清单后，Lrio 负责形成类别表：

- `class_id`：模型输出编号。
- `class_key`：稳定英文标识。
- `display_name`：中文名称。
- 标注说明及易混淆类别说明。

类别表具有独立版本。增加、删除或重排模型类别时更新版本，不能静默改变编号含义。

前端通过接口读取类别，不写死类别列表。缺少正式类别表时，只能开展模拟开发或通用模型冒烟测试，不能验收食材识别效果。

### 4.2 训练方案

- 主模型使用 YOLO11s 预训练权重微调。
- YOLO11n 作为轻量对照实验。
- 使用 CVAT 标注，导出 YOLO 检测格式。
- 优先收集真实冰箱及厨房场景，覆盖暗光、反光、遮挡、包装和混放。
- 加入空场景、非目标食材和其他物体作为负样本。
- 按拍摄会话或来源分组去重后划分训练、验证、测试集，避免相邻视频帧泄漏。
- 初始比例约 70%／15%／15%；最终保证各类别在评估集中有足够样本。
- 初始输入 640、最多 100 轮、早停 patience 20；batch 根据训练机显存配置并记录。
- 验证集选择权重和阈值，独立测试集用于最终报告。

### 4.3 模型交付包

Lrio 交付：

- `best.pt`。
- `classes.json`。
- `manifest.json`，含模型版本、类别版本、权重 SHA-256、输入尺寸及依赖版本。
- 固定的检测阈值与 `botsort.yaml`。
- 数据版本、训练参数和评估报告。
- 图片与视频测试样例。
- 安装、推理和复现命令。

模型权重、原始数据和训练产物不直接提交 Git。记录数据来源与许可；商业使用前核对模型及数据授权。

## 5. 非功能与验收

### 5.1 运行规则

- 单后端进程、单视觉任务，图片请求、视频任务和实时会话共享容量限制。
- 视频任务状态及结果元数据保存在独立 SQLite 中；媒体和逐帧结果保存在本地数据目录。
- 成功结果保留 24 小时；过期后删除媒体与大结果文件，保留过期状态。
- 实时帧不落盘，停止后仅保留当前网页内存中的会话摘要，刷新即丢失。
- 视频失败或取消后删除不完整媒体及结果。
- 后端重启时，未完成任务标记失败；不自动续跑。
- 页面刷新后可从视频任务列表继续查看状态。
- 首版不把用户素材自动加入训练集。

### 5.2 验收目标

- 三种输入路径均可完成完整流程。
- 同一连续场景中能展示轨迹关联、多帧融合及稳定目标筛选。
- 不同视频和实时会话之间无轨迹状态串用。
- 失败、取消和断开后释放计算及摄像头资源。
- 独立清晰无遮挡测试场景目标：整体 Precision ≥90%、Recall ≥80%，同时报告逐类数据和 mAP50–95。
- 至少使用 10 段独立视频评估遮挡、移动镜头、出画返回及相似食材；人工记录轨迹中断、ID 切换和重复轨迹，不用检测 mAP 代替跟踪评估。
- 在最终指定的推理机器上，以固定样例连续运行 60 秒；实时分析目标为至少 3 FPS、结果往返耗时 P95 不超过 1 秒。未达标时披露实测值，优化后再验收。
- 摄像头连续运行 10 分钟，无无界队列及持续增长的内存占用。
- 模拟测试和真实模型测试分别报告。

## 6. 双人分工与里程碑

| 阶段 | 你负责 | Lrio 负责 | 完成条件 |
|---|---|---|---|
| M0 接口冻结 | TypeScript 类型、页面骨架、模拟服务 | Pydantic 模型、视觉模块骨架、模拟响应 | 同一份样例双方都能使用 |
| M1 图片闭环 | 上传、预览、框选、列表与导出 | 图片校验、推理接口、模型状态 | 模拟和真实模型分别跑通 |
| M2 视频闭环 | 上传进度、任务轮询、播放器叠层 | 视频任务、解码、跟踪、时序汇总 | 完整视频分析及取消 |
| M3 实时闭环 | 相机、采样、WebSocket、停止清理 | 实时会话、协议校验、跟踪状态 | 会话稳定运行及异常恢复 |
| M4 验收交付 | 浏览器测试、截图、使用说明 | 训练报告、性能测试、部署说明 | 联合验收通过 |

正式数据采集与模型训练可与 M1～M3 并行；不能要求前端等待正式权重。

后续 MealFlow 集成另设阶段，不在本期修改其库存业务。

---

# 文档二：接口与协作契约

建议文件名：`docs/api-contract.md`

## 1. 通用约定

### 1.1 地址与版本

- HTTP：`/api/v1`
- WebSocket：`/api/v1/live/ws`
- JSON 字段：`snake_case`
- 日期时间：UTC ISO 8601，后缀 `Z`
- 媒体时间：整数毫秒，从图片／视频／会话起点计算
- 置信度及融合得分：`0～1`
- 请求、任务和会话 ID：服务端生成 UUID
- 正常响应直接返回业务对象。
- 所有结果包含 `schema_version: "1.0"` 和 `mode: "real" | "mock"`。

新增可选字段保持兼容；删除字段、改变坐标语义或字段类型必须升级 API 版本。

### 1.2 类别示例

文档中的 `tomato` 仅为协议示例，不代表正式类别已确定。

```json
{
  "class_id": 0,
  "class_key": "tomato",
  "display_name": "番茄"
}
```

### 1.3 坐标和方向

- `bbox` 使用归一化 `xyxy`：`[x1, y1, x2, y2]`。
- 原点在左上角，范围 `0～1`。
- 坐标基于方向纠正、去除模型 letterbox padding 后的实际分析画面。
- 图片透明区域统一合成为白色背景。
- 视频时间基于规范化视频的展示时间戳，不用帧编号推算时间。
- 前端以规范化媒体尺寸绘制，不自行再次旋转或镜像分析结果。
- 实时预览可镜像显示，但送入分析和结果画布均不镜像。

### 1.4 错误格式

```json
{
  "error": {
    "code": "MODEL_UNAVAILABLE",
    "message": "模型尚未就绪",
    "request_id": "uuid",
    "details": {}
  }
}
```

| HTTP 状态 | 错误码 | 场景 |
|---|---|---|
| 404 | `RESOURCE_NOT_FOUND` | 不存在的任务或产物 |
| 409 | `INVALID_TASK_STATE` | 当前状态不允许该操作 |
| 410 | `RESULT_EXPIRED` | 已过期任务的结果访问 |
| 413 | `PAYLOAD_TOO_LARGE` | 上传体积超限 |
| 415 | `UNSUPPORTED_MEDIA` | 类型、容器或编码不支持 |
| 422 | `INVALID_INPUT` | 内容损坏、尺寸超限、参数无效 |
| 429 | `ENGINE_BUSY` | 视觉引擎已被占用 |
| 503 | `MODEL_UNAVAILABLE` | 权重或类别配置不可用 |
| 500 | `INTERNAL_ERROR` | 未预期错误 |

请求体中的文件路径、系统异常栈和密钥不返回客户端。

## 2. 公共对象

### 2.1 Detection

```json
{
  "detection_id": 0,
  "class_id": 0,
  "class_key": "tomato",
  "display_name": "番茄",
  "confidence": 0.92,
  "bbox": [0.1, 0.2, 0.4, 0.6],
  "track_id": null
}
```

- `detection_id`：当前分析帧内从 0 开始的编号。
- `track_id`：图片固定为 `null`；视频与实时分析未成功关联时也可为 `null`。
- 轨迹唯一键为“任务或会话 ID＋track_id”，不能只使用 track_id。

### 2.2 FrameResult

```json
{
  "frame_index": 0,
  "timestamp_ms": 0,
  "width": 960,
  "height": 540,
  "processing_ms": 85,
  "detections": []
}
```

`frame_index` 为分析帧序号，不是原视频帧序号。`processing_ms` 包含服务端预处理、检测、跟踪和结果组织，不包含上传耗时。

### 2.3 TrackSummary

字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `track_id` | integer | 会话内轨迹编号 |
| `class_key` | string | 融合类别 |
| `display_name` | string | 中文名称 |
| `fusion_score` | number | 置信度累计投票占比 |
| `observations` | integer | 有效观察次数 |
| `first_seen_ms` | integer | 首次观察时间 |
| `last_seen_ms` | integer | 末次观察时间 |
| `observed_duration_ms` | integer | 按需求定义计算的观察时长 |
| `stable` | boolean | 是否达到稳定目标条件 |
| `representative_frame_index` | integer | 代表分析帧序号 |

### 2.4 ClassSummary

```json
{
  "class_key": "tomato",
  "display_name": "番茄",
  "stable_track_count": 2,
  "peak_visible_count": 2
}
```

`stable_track_count` 统计融合类别归入该类的稳定轨迹。`peak_visible_count` 统计逐帧原始检测类别数量的最大值。两者不能直接映射为库存数量。

## 3. 系统状态接口

### `GET /health`

只检测服务存活，不触发模型加载。

```json
{
  "status": "ok",
  "schema_version": "1.0",
  "mode": "real"
}
```

### `GET /model`

```json
{
  "schema_version": "1.0",
  "mode": "real",
  "status": "ready",
  "model_version": "food-yolo11s-001",
  "class_catalog_version": "food-v1",
  "device": "cpu",
  "classes": [],
  "limits": {
    "image_max_bytes": 10485760,
    "image_max_pixels": 20000000,
    "video_max_bytes": 104857600,
    "video_max_duration_ms": 60000,
    "video_sample_fps": 5,
    "live_max_fps": 5,
    "live_max_duration_ms": 600000
  }
}
```

模型状态：`loading / ready / unavailable`。不可用时增加 `reason`，模型版本等未知字段为 `null`。

模型在应用启动后的初始化阶段加载；加载中服务可响应状态查询，分析请求返回 503。

## 4. 图片识别接口

### `POST /images/analyze`

请求：`multipart/form-data`，必填字段 `file`。

处理：

1. 验证实际文件字节、尺寸及可解码性。
2. 纠正方向并转换 RGB。
3. 调用检测器。
4. 生成规范化 JPEG 预览。
5. 返回检测及类别汇总。

响应：`200 OK`

```json
{
  "schema_version": "1.0",
  "mode": "real",
  "request_id": "uuid",
  "model_version": "food-yolo11s-001",
  "class_catalog_version": "food-v1",
  "expires_at": "2026-10-16T02:00:00Z",
  "preview_url": "/api/v1/artifacts/uuid",
  "frame": {
    "frame_index": 0,
    "timestamp_ms": 0,
    "width": 1280,
    "height": 720,
    "processing_ms": 120,
    "detections": []
  },
  "class_counts": [
    {
      "class_key": "tomato",
      "display_name": "番茄",
      "visible_count": 2
    }
  ]
}
```

无检测时两个列表为空，不报错。规范化预览保留 24 小时；不保留原始上传图片。

图片请求期间独占视觉引擎，其他分析请求返回 429。完成或异常后释放占用。

## 5. 视频任务接口

### `POST /video-jobs`

请求：`multipart/form-data`，必填字段 `file`。

- 体积检查在上传过程中执行，不能等全部载入内存后才限制。
- 引擎空闲时接受并预留容量；忙时返回 429。
- 元数据解码验证、规范化和分析在后台执行。
- 不建立排队列表。

响应：`202 Accepted`

```json
{
  "schema_version": "1.0",
  "mode": "real",
  "job_id": "uuid",
  "status": "queued",
  "created_at": "2026-10-15T02:00:00Z",
  "status_url": "/api/v1/video-jobs/uuid"
}
```

### `GET /video-jobs?limit=20&offset=0`

返回按创建时间倒序的任务列表：

```json
{
  "items": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

`limit` 范围 1～100，`offset` 为非负整数。

### `GET /video-jobs/{job_id}`

返回：

- ID、模式、创建时间、结束时间和过期时间。
- `status`、`stage`。
- 模型及类别版本。
- 视频元数据。
- `progress`。
- 完成后的结果入口。
- 失败时的错误对象。

状态转换：

```text
queued → running → succeeded
queued/running → cancelling → cancelled
queued/running/cancelling → failed
succeeded → expired
```

阶段：`validating / normalizing / analyzing / finalizing`。

`progress` 格式：

```json
{
  "processed_frames": 30,
  "total_frames": 100,
  "percent": 30
}
```

尚未确定分析帧总数时，`total_frames` 和 `percent` 为 `null`。百分比只表示分析进度；`succeeded` 才表示任务全部完成。

### `POST /video-jobs/{job_id}/cancel`

- 运行中返回 `202` 并进入 `cancelling`。
- 后端在当前解码或推理步骤结束后响应取消。
- 已在取消或已取消时幂等返回当前状态。
- 已成功、失败或过期时返回 409。
- 取消完成后清理临时产物并释放引擎。

### `GET /video-jobs/{job_id}/result`

仅成功且未过期时返回：

```json
{
  "schema_version": "1.0",
  "mode": "real",
  "job_id": "uuid",
  "model_version": "food-yolo11s-001",
  "class_catalog_version": "food-v1",
  "video": {
    "width": 1280,
    "height": 720,
    "duration_ms": 10000,
    "sample_fps": 5,
    "media_url": "/api/v1/artifacts/uuid"
  },
  "tracks": [],
  "class_summaries": [],
  "frames_url": "/api/v1/video-jobs/uuid/frames",
  "export_url": "/api/v1/video-jobs/uuid/export"
}
```

未完成返回 409，过期返回 410。

### `GET /video-jobs/{job_id}/frames?offset=0&limit=100`

返回分析帧：

```json
{
  "items": [],
  "total": 50,
  "offset": 0,
  "limit": 100
}
```

`limit` 范围 1～300。V1 视频最多约 300 个分析帧，前端可取完整列表后按时间播放。

### `GET /video-jobs/{job_id}/frames/{frame_index}/preview`

返回该分析帧 JPEG，用于轨迹代表画面展示。帧按规范化视频时间提取，坐标和帧结果一致。

### `GET /video-jobs/{job_id}/export`

返回下载用 JSON，包含结果摘要、轨迹和全部分析帧；设置附件下载响应头。

### `DELETE /video-jobs/{job_id}`

- 仅终态任务允许删除；运行中返回 409，要求先取消。
- 删除元数据及全部关联文件。
- 返回 `204 No Content`；不存在时也返回 204。

### `GET /artifacts/{artifact_id}`

- 只访问服务端登记的产物，不接受文件路径参数。
- 视频支持 HTTP Range，便于浏览器跳转播放。
- 未知产物返回 404，已过期返回 410。
- 使用相对 URL，同一协议可经 MealFlow 后端代理访问。

## 6. 实时摄像头 WebSocket 协议

### 6.1 建立连接

地址：`/api/v1/live/ws`

客户端连接后 5 秒内发送：

```json
{
  "type": "start",
  "schema_version": "1.0"
}
```

服务端成功预留视觉引擎后回复：

```json
{
  "type": "started",
  "session_id": "uuid",
  "mode": "real",
  "model_version": "food-yolo11s-001",
  "class_catalog_version": "food-v1",
  "max_fps": 5,
  "max_frame_bytes": 1048576,
  "max_duration_ms": 600000
}
```

忙时返回 `ENGINE_BUSY` 错误并关闭连接；模型未就绪时返回 `MODEL_UNAVAILABLE`。

### 6.2 发送分析帧

V1 使用 JSON＋Base64 JPEG，降低双方协议实现成本。

```json
{
  "type": "frame",
  "seq": 1,
  "timestamp_ms": 0,
  "image_base64": "<不包含 data URL 前缀的 JPEG Base64>"
}
```

规则：

- `seq` 从 1 开始严格递增。
- `timestamp_ms` 为从开始采集起计算的单调时钟毫秒数，严格递增。
- 每帧解码后 JPEG 不超过 1 MiB，尺寸不超过 960×960。
- WebSocket 消息上限设置为 2 MiB，覆盖 Base64 开销。
- 首版只允许一帧在途；收到结果后才能发送下一帧。
- 下一次发送与上次发送至少间隔 200 ms。
- 等待结果期间不保存待发送帧；允许发送时重新抓取最新画面。
- 服务端每处理一帧返回一次结果；禁止使用无限队列。
- 前端保留当前在途帧，用它绘制结果图；收到结果后释放旧图像。

### 6.3 帧结果

```json
{
  "type": "frame_result",
  "session_id": "uuid",
  "seq": 1,
  "frame": {
    "frame_index": 0,
    "timestamp_ms": 0,
    "width": 960,
    "height": 540,
    "processing_ms": 100,
    "detections": []
  },
  "tracks": [],
  "class_summaries": []
}
```

`tracks` 返回当前会话的累计轨迹摘要；前端以 `session_id＋track_id` 更新状态。

### 6.4 保活与停止

空闲时客户端每 10 秒发送：

```json
{"type": "ping"}
```

服务端回复：

```json
{"type": "pong"}
```

30 秒未收到客户端消息，服务端结束会话并释放资源。客户端单帧等待结果超过 10 秒时结束连接并提示超时。

客户端停止：

```json
{"type": "stop"}
```

服务端回复：

```json
{
  "type": "stopped",
  "session_id": "uuid",
  "reason": "client_stop",
  "tracks": [],
  "class_summaries": []
}
```

`reason` 可为 `client_stop / duration_limit / idle_timeout`。

停止请求在当前帧处理结束后执行。服务端释放跟踪状态与引擎并正常关闭连接。异常断开也必须清理，不依赖收到 `stop`。

### 6.5 错误事件

```json
{
  "type": "error",
  "code": "INVALID_FRAME",
  "message": "帧内容无法解码",
  "recoverable": false
}
```

V1 协议错误、非法帧、超限、乱序和推理失败均终止会话。使用 `1008` 表示协议／输入违规，`1011` 表示服务内部错误，`1013` 表示繁忙；正常结束为 `1000`。

## 7. Python 视觉模块接口

该接口是 Lrio 内部算法与后端编排的边界，也便于独立测试。

```python
class VisionEngine:
    def metadata(self) -> ModelMetadata: ...

    def detect(self, image_rgb: NDArray) -> FramePrediction: ...

    def create_tracker(self) -> TrackingSession: ...


class TrackingSession:
    def process(
        self,
        image_rgb: NDArray,
        *,
        frame_index: int,
        timestamp_ms: int,
    ) -> TrackingPrediction: ...

    def summary(self) -> TemporalSummary: ...

    def close(self) -> None: ...
```

约定：

- 输入为 RGB、`uint8`、`H×W×3` 数组，方向已纠正。
- 模块不处理 HTTP、上传文件、数据库和 MealFlow 映射。
- 所有输出转换为可 JSON 序列化对象，不向上层泄漏 Tensor。
- 每个视频或实时会话创建独立 `TrackingSession`。
- 底层 Ultralytics predictor／tracker 若携带可变状态，必须明确重置或隔离；仅创建外层 Python 对象不视为隔离完成。
- 图片检测不能污染下一次跟踪状态。
- 主引擎由后端统一串行调度，不进行同一模型实例的并发调用。
- `close()` 可重复执行，正常与异常路径均调用。
- 时序规则及阈值集中配置，训练交付包记录版本。

## 8. 模拟、测试与协作要求

### 8.1 模拟结果

- 双方共同维护契约样例：图片有目标、图片空结果、视频成功、视频失败、实时帧和轨迹摘要。
- 模拟模式使用相同数据结构，`mode` 必须为 `mock`。
- 前端先基于样例完成页面；后端使用相同样例建立响应验证。
- 正式模式缺权重时返回不可用，不静默切换模拟模式。
- FastAPI 提供 `/openapi.json` 和 `/docs`；WebSocket 按本文协议维护独立测试。

### 8.2 必测场景

- 图片 EXIF 旋转、透明背景、损坏、超限和空结果。
- 视频编码不支持、旋转信息、变帧率、解码失败、取消和过期。
- 视频跳转播放与分析帧叠层同步。
- WebSocket 乱序、重复帧、非法 Base64、超大帧和慢客户端。
- 多个客户端争用引擎，失败后不残留占用。
- 图片、不同视频和实时会话之间无状态污染。
- 服务重启后未完成任务转失败，已成功结果仍可查询。
- 使用模拟检测器运行接口测试，真实模型另做集成和性能测试。

## 9. 后续 MealFlow 接入契约

独立系统不增加 `inventory_id`、库存数量或保质期字段。

集成阶段由 MealFlow：

1. 调用图片、视频或实时接口。
2. 根据 `class_catalog_version＋class_key` 映射食材 key。
3. 对未知映射要求人工选择。
4. 将视觉结果作为候选清单。
5. 让用户核对实际数量、单位和到期日。
6. 按此前确定的规则覆盖所选库存，默认保留已有保质期。

图片的 `visible_count`、视频的轨迹数和峰值数量均只作参考。独立项目的网页与 MealFlow 库存弹框共用视觉服务，保留各自界面。

## 10. 开工默认值与前置输入

- 本期使用独立仓库，不直接改动 MealFlow。
- 技术栈采用 Vue 3＋TypeScript＋FastAPI＋本地 YOLO11s。
- 单进程、单视觉任务；正式部署前依据实测再扩容。
- 你主负责前端，Lrio 主负责后端及视觉；接口契约共同维护。
- 正式类别清单仍由你补充，阻塞正式标注及训练，不阻塞应用和协议开发。
- 暂不承诺日历工期；按 M0～M4 的可验收成果推进。
- 开发摄像头采样时关注 WebSocket 发送缓冲，禁止积压帧。[WebSocket 缓冲说明](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/bufferedAmount)
