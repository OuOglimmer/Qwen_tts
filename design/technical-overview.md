# Qwen TTS Voiceover Studio · 技术说明

> 目标读者：要接手、排查或继续开发这个项目的人。
> 读完之后你应该能回答：它由哪些进程组成、一次配音任务怎么走完、数据落在哪、接口怎么组织、为什么这么设计。
>
> 数据库结构见 `design/database-schema.md`（目标 v5 数据字典），设计理由与迁移步骤见 `design/database-design.md`。本文讲**运行**，不重复那两份的字段清单。

---

## 0. 这是什么

一个**离线运行在单机 GPU 上的多角色配音工作台**。给它一批台词和几个"声线"（音色），它逐条合成成音频，你试听、返修、挑选，最后导出一套带编号的成品文件。

能做的：

- 用自然语言描述生成声线（Qwen3-TTS `voice_design`），或用一段参考录音克隆声线（`clone`）
- 一个项目里管多个角色，每个角色一条声线
- 按行批量合成台词，单条重生成、整批按人物重生成，保留历史版本
- 试听挑选、成品手动排序、事后补录单句
- 导出为本地文件夹 / ZIP

**做不到**（能力边界，避免误判）：

- 没有语音翻译、语音转语音、变声。把一段中文录音"直接变成"英文是做不到的。
- 但**跨语种配音可以**：生成时的 `language` 是独立入参，与声线来源无关。中文声线配英文台词是允许的。
- 没有多用户、权限、账号。侧栏项目列表对访问该端口的所有人共享。

---

## 1. 系统全景

四个东西组成整个系统：**两个进程、一个 SQLite、一张 GPU**。

```
浏览器（本机 / 局域网同事）
  │  React 页面 · 音频播放 · File System Access API（本地写盘）
  │  HTTP :3000
  ▼
Next.js 14 · frontend/
  │  唯一的 UI 进程，不含业务逻辑
  │  rewrites：/api/:path* → http://127.0.0.1:7871/api/:path*
  │  HTTP :7871（同源代理，浏览器只认 3000）
  ▼
FastAPI · studio/api.py
  │  60+ 个 REST 接口：校验参数 → 调组件 → 序列化
  │  StudioService 门面，组装 8 个领域组件
  │
  ├──→ SQLite  app.db（WAL · 外键开启 · 11 张表，v5 目标）
  ├──→ ModelManager（单模型独占 + 全局串行锁）→ GPU 8 GB，一次一个进程
  └──→ 磁盘  DATA_DIR（voices/ · projects/ · temp/ · logs/ · backups/）
             音频永远是文件，数据库只存路径
```

三条贯穿全局的约束，后面每个设计选择都能回溯到它们：

1. **GPU 只有一张，模型加载一次要几个 GB。** 所以推理必须串行，且不能两个进程同时各加载一份。
2. **音频是二进制大文件。** 所以库里只存绝对路径，文件系统是权威，数据库是索引。
3. **这台机器可能同时服务局域网里的几个人。** 所以"往服务器写文件""弹目录框"这类动作默认关闭。

---

## 2. 跑起来

### 2.1 前置

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 系统 | Windows 10/11 | 跨进程 GPU 锁用了 `msvcrt`，非 Windows 会退化为不加锁 |
| GPU | NVIDIA，约 8 GB 显存 | 模型是 1.7B 级 |
| Python | 3.11+，虚拟环境在 `.venv/` | 启动脚本硬编码 `.venv/Scripts/python.exe` |
| Node.js | LTS | 前端 Next.js 14 |
| SoX | 需在 PATH 中 | 音频格式转换；`start_api.ps1` 会尝试从 winget 目录自动补 PATH |
| 模型 | `models/voice_design_1.7b`、`models/voice_clone_1.7b` | 用 `scripts/download_models.py` 拉取 |

### 2.2 启动

```bat
start_api.cmd        :: 后端  → python -m studio.api --host 0.0.0.0 --port 7871
start_frontend.cmd   :: 前端  → npm run dev --hostname 0.0.0.0 --port 3000
```

打开 `http://127.0.0.1:3000`。局域网同事用 `http://<本机IP>:3000`，只需开这一个端口。

> 后端绑 `0.0.0.0` 是为了让局域网直连 7871 也能工作，但**正常使用不需要**——前端代理已经把 `/api/*` 转过去了。

另外两个入口：

- `start_studio.cmd` / `scripts/desktop_studio_launcher.ps1` → 一键起后端 7871 + 前端 3000（各开一个窗口；launcher 版静默后台、日志在 `data/logs/desktop-launcher.log`）。
- `start_voice_design.cmd` / `start_voice_clone.cmd` → 独立 Demo，调试模型用，也会占 GPU 锁。

> 旧的 Gradio 单页界面（7870，`studio/ui.py` + `scripts/start_studio.ps1`）已随 schema v5 改造**删除**。
> `python -m studio` 现在等价于 `python -m studio.api`，只起后端 API。

### 2.3 环境变量

后端读 `.env`（项目根目录）与系统环境变量，`scripts/../config.py` 的 `_load_local_env()` 手写解析，不依赖第三方库。

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `QWEN_TTS_DATA_DIR` | `%LOCALAPPDATA%\QwenTTSVoiceoverStudio` | 数据目录。**若 `<repo>/data/app.db` 存在则优先用它**（兼容老布局） |
| `QWEN_TTS_MODEL_ROOT` | `<repo>/models` | 模型根目录 |
| `QWEN_TTS_GENERATION_MODEL_PATH` | `<root>/voice_design_1.7b` | 文→音（设计）模型 |
| `QWEN_TTS_CLONE_MODEL_PATH` | `<root>/voice_clone_1.7b` | 克隆模型 |
| `QWEN_TTS_ALLOW_HOST_FS` | 关 | `1` 才允许"后端写盘 / 弹目录框 / 打开文件夹" |
| `QWEN_TTS_CORS_ORIGINS` | `*` | CORS 白名单，逗号分隔 |
| `NEXT_PUBLIC_API_BASE` | 空（同源） | 前端直连后端时用，一般不需要 |

模型相关配置分两层，别混为一谈：

- **路径可配**（上面三个变量，改 `.env` 即可换模型版本或挪目录）。加载前会校验目录里有 `model.safetensors`，缺了直接报 `Model is incomplete`（`model_manager.py:143`）。当前部署的实际值是 `voice_design_1.7b` + `voice_clone_0.6b`——注意克隆用的是 0.6B，不是 `.env.example` 里写的 1.7B。
- **加载与推理参数写死**在 `studio/model_manager.py`：`device_map="cuda:0"`、`dtype=torch.bfloat16`（`:147-148`）、`design` 的 `max_new_tokens=512`（`:174`）、`clone` 的 `max_new_tokens = 96 + 字数×6` 并夹在 `[128, 2048]`（`:110-121`）、`non_streaming_mode=True`（`:204`）、不合格结果最多重试 5 次（`:216`）。这些与模型版本和 12 Hz tokenizer 的时间换算强耦合，改设备或精度必须改代码。
- 另有一份**重复的加载参数**在 `scripts/smoke_test.py:25-28`，改 `model_manager` 时要同步，否则冒烟测试测的不是线上那条路。

### 2.4 GPU 独占：两道锁

```python
# model_manager.py — 跨进程
CrossProcessGpuLock  →  DATA_DIR/gpu.lock 上打一个字节的 msvcrt 非阻塞锁
                        第二个进程启动即报错，而不是把显存撑爆

# 进程内
ModelManager         →  一把 threading.Lock 包住每次推理
                        并保证"同一时刻只有一个模型在显存里"
```

`ModelManager` 的职责是"**恰好持有一个模型，并把所有推理调用串行化**"。切换 design / clone 会卸载另一个。所以：

- 批量合成时，界面上的进度是一行一行往前挪的，不是并行跑。
- 两个人同时点"生成"，第二个人排队；同一个人连点两次，第二次被 `BackgroundJob` 直接拒掉（见 §6.4）。

---

## 3. 后端：八个组件

`studio/service.py` 的 `StudioService` 是**门面，不含业务逻辑**，只做组装与转发。真正的领域在 8 个组件里，每个组件独占一批表、一批目录。

### 3.1 依赖方向

依赖是**单向的**，不允许回指。每条都读作"左边依赖右边"：

```
exports → batch          → voice_assets → model_manager
exports → voice_workflow → model_manager
exports → supplement     → voice_assets
tools   → model_manager
```

> 这张图直接来自 `service.py` 的模块 docstring。`exports` 同时依赖三家，是因为"成品"这个概念本身就横跨 batch、单句、补录。

看得出来的三件事：

- `model_manager` 在最底层，谁都依赖它，它不依赖任何人。
- 直接调模型的只有三个：`voice_assets`（按已固化声线合成）、`voice_workflow`（生成候选、抽 speaker prompt）、`tools`（独立试听与格式转换）。
- `batch` 和 `supplement` **完全不碰模型**，都走 `assets.generate_with_voice()`。所以"批量合成到底用了什么参数"只有一个答案，不需要在两处对齐。
- `exports` 在最上层，它读 `batch`、`voice_workflow`、`supplement` 三家的结果，所以它最了解"成品"的完整含义。

### 3.2 组件职责表

| 组件 | 拥有的表 | 拥有的目录 | 职责 | 长任务 |
| --- | --- | --- | --- | --- |
| `projects` | `projects`、`activity_log` | `projects/<pid>/`（建/删） | 项目 CRUD、软删/物理删、操作流水、完整性检查 | — |
| `voice_workflow` | `voice_requests`、`candidates` | `projects/<pid>/candidates/`、`outputs/single_line/` | 第 1 步的状态机：槽位、候选生成、选定、固化、单句保留 | 候选生成 |
| `voice_assets` | `voices`、`project_voices` | `voices/<vid>/` | 声线资产库：入库、挂载、删除；按声线合成音频的**统一入口** | — |
| `batch` | `batch_items`、`batch_item_versions` | `projects/<pid>/outputs/<声线>/`、`manifest.json` | 台词行、整批合成、单条/整人重生成、版本保留 | 批量生成、按人物重生成 |
| `supplement` | `supplement_lines` | `projects/<pid>/outputs/supplement/` | 补录单句：即时合成一条，失败也留行 | —（同步） |
| `exports` | `output_order`（v5）、`app_settings` | 导出目标目录 | 成品顺序、manifest 生成、三种导出通道 | —（流式 zip） |
| `tools` | — | `temp/standalone/`、`temp/conversions/` | 独立工具：设计试听、克隆试听、格式转换 | — |
| `model_manager` | — | — | 模型装载/卸载、GPU 锁、推理串行、生成结果健全性检查 | — |

### 3.3 为什么这样分

一句话：**按"谁拥有这张表"切，而不是按"哪个页面用"切。**

- 页面会重排（以前有独立的「工具」页，现在没了），表不会。按表切，页面怎么改都不用动后端。
- 跨组件的调用只能是"上层调下层"。想让 `batch` 知道 `voice_workflow` 的槽位状态是不行的——必须回到 `exports` 那一层去组合。这避免了环形依赖。
- `api.py` 只依赖 `StudioService` 一个对象，所以加接口时不需要理解组件内部。

`StudioService` 里保留了 100 多个纯转发方法，是为了兼容历史调用面（`api.py` 与旧 Gradio UI 都走它）。**新代码请直接依赖 `service.batch`、`service.assets` 这样的具体组件**，别在门面上加新方法。

---

## 4. 一次配音任务的生命周期

界面上是四步，数据上是四条链路。

### 第 1 步 · 创建声线

目标：把"我需要一个中年男声，纪录片旁白"变成"库里有一条能用的声线资产"。

```
draft        空槽位，只有名称
  ↓ 填音色描述 + 试听文本 + 语种（两项都非空 → 自动升 ready）
ready        可生成
  ↓ 生成候选（每轮 1~8 条，后台任务，可取消）
candidate    有候选可试听
  ↓ 试听、勾选备选、选定一条
selected     已挑中，等待固化
  ↓ 固化（批量动作：本项目所有 selected 一起做）
fixed        voice_id 指向 voices 资产，此后可无限复用
```

| 动作 | 接口 | 表变化 | 磁盘 |
| --- | --- | --- | --- |
| 建槽位（可批量 1~50 个） | `POST /api/projects/{pid}/voice-requests` | 插 `voice_requests`，`status='draft'` | — |
| 填音色描述 / 试听文本 / 语种 | `PUT /api/voice-requests/{rid}` | `prompt`、`audition_text` 都非空时自动升 `ready` | — |
| 生成候选 | `POST /api/projects/{pid}/voice-requests/generate` | 插 `candidates`，槽位转 `candidate`，`candidate_round+1` | `projects/<pid>/candidates/<cid>/*.wav` |
| 轮询进度 | `GET /api/voice-generation/status` | — | — |
| 勾选备选 / 选定 | `POST .../candidates/{cid}/shortlist`、`.../select/{cid}` | `candidates.shortlisted` / `selected`，槽位转 `selected` | — |
| 保留成单句成品 | `POST .../candidates/{cid}/keep-single` | v5：`outputs`(origin=single)；v4：`voice_requests.direct_output_mp3` | `outputs/single_line/` |
| **固化** | `POST /api/projects/{pid}/voices/fix` | 插 `voices` + `project_voices`，槽位转 `fixed` | `voices/<vid>/` 四个文件 |
| 解冻重来 | `POST /api/voice-requests/{rid}/reopen` | 槽位退回 `candidate`，`voice_id` 置空 | — |

**固化是这个项目的关键动作**，一次写入四个文件：

```
voices/<voice_id>/
├── reference.wav       参考音频（设计的产物本身 / 上传的原始录音）
├── prompt.safetensors  从参考音频抽出的 speaker embedding + 文本特征
├── preview.mp3         试听片段
└── metadata.json       { id, name, source, prompt, language, ref_text, x_vector_only }
```

其中 `x_vector_only` 决定接下来合成时**用不用参考音频的文本内容**：

- `x_vector_only: true` → 只用说话人向量，不看参考音频里说了什么。参考录音有杂音、内容无关时用。
- `x_vector_only: false` → 同时用文本特征，音色更贴。要求 `ref_text` 与参考音频内容一致。

这个参数目前**只存在 `metadata.json` 里，没进数据库**——v5 要把它提升为 `voices.x_vector_only` 列（见 §5.4）。

固化必须用**你刚试听的那一段**：设计产物文件名就是后端返回的 `design_id`，点"入库"时回传它换回同一段音频。重新生成会换随机 seed，出来的不是同一把声音。这条约束贯穿 `/api/tools/design` → `/api/voices/design-archive` 两个接口。

### 第 2 步 · 配音生成

目标：一行台词 = 一个 mp3。

| 动作 | 接口 | 表变化 | 磁盘 |
| --- | --- | --- | --- |
| 贴台词（多行 = 多条） | `POST /api/projects/{pid}/batch` | 插 `batch_items`，`status='pending'`，`position` 递增 | — |
| 运行 | `POST /api/projects/{pid}/batch/run` | 后台线程逐行置 `running`→`completed`/`failed` | `outputs/<声线名>/0001_<声线>_v01.mp3` |
| 轮询 | `GET /api/projects/{pid}/batch/status` | — | — |
| 改文本 / 语种 | `POST /api/projects/{pid}/batch/save-edits` | 更新 `text`/`language`，版本置 0 | — |
| 清空未生成的 | `POST /api/projects/{pid}/batch/clear` | 删 `pending` 行 | — |

命名规则（`exports._batch_export_filename` 决定导出名，生成时由 `batch` 决定落盘名）：

```
0001_贾宝玉_v01.mp3
 │   │      └── 版本号，重生成一次 +1
 │   └────────── 声线显示名（safe_filename 过滤非法字符）
 └────────────── 台词序号，项目内递增、四位补零
```

每行最多保留 **3 个版本**（`batch.VERSIONS_KEPT`），更旧的音频文件在新版本成功后删除，但 `batch_item_versions` 里的记录保留。

### 第 3 步 · 试听返修

目标：不合格的挑出来重做，合格的选进成品。

| 动作 | 接口 | 说明 |
| --- | --- | --- |
| 单条重生成 | `POST /api/projects/{pid}/batch/{item_id}/regenerate` | 版本 +1，旧文件保留到上限 |
| 选入成品 / 取消 | `POST .../batch/{item_id}/select` | `status` 在 `completed`/`selected` 之间切换 |
| **按人物整批重做** | `POST /api/projects/{pid}/voices/{vid}/regenerate` | 后台线程；换声线后重跑该角色所有台词 |
| 轮询 | `GET /api/voice-regeneration/progress` | — |
| 单句成品列表 | `GET /api/projects/{pid}/single-outputs` | 第 1 步"保留为单句"的产物 |

注意 `batch_items.status` 这一个字段承担了三件事：`pending`（未生成）、`running`（生成中）、`completed`（已生成未选）、`failed`（失败）、`selected`（进成品）。v5 把它拆成 `gen_status` + `in_final` 两列，因为"生成成功"和"选入成品"是两个独立事实。

### 第 4 步 · 成品导出

成品页要展示的，是**三个来源合起来**的一张表：

```
三个来源，同一条交付序列
  ① batch_items，status = selected
  ② voice_requests.direct_output_mp3（单句保留）
  ③ supplement_lines，status = done
        ↓
  合并 → 套用已保存的手动排序 → 重新编号 1..N → 生成 manifest.json
```

| 动作 | 接口 | 说明 |
| --- | --- | --- |
| 成品列表 | `GET /api/projects/{pid}/outputs` | 后端拼三源，带 `order_key`（`batch:<id>` / `single:<id>` / `supplement:<id>`） |
| 手动排序 | `PUT /api/projects/{pid}/outputs/order` | 存 `order_key` 数组；空数组恢复默认 |
| 补录一句 | `POST /api/projects/{pid}/outputs/supplement` | 同步合成，失败也写一行带 `error` |
| 导出清单 | `GET /api/projects/{pid}/outputs/export-plan` | 只回"该写什么文件名 + 音频 URL"，落盘交给浏览器 |
| 打包下载 | `GET /api/projects/{pid}/outputs/archive.zip` | 流式 zip，后端不落盘 |
| 后端写盘 | `POST /api/projects/{pid}/outputs/export` | 仅 `QWEN_TTS_ALLOW_HOST_FS=1`，且目标必须在 `DATA_DIR` 内 |

**三种导出通道，为什么要有三种：**

```
① 浏览器直写（首选）    File System Access API，写进"打开页面的那台电脑"
                       需要 https 或 localhost；不支持时自动退化到 ②
② ZIP 流式下载          zipfile 写进不可 seek 的 _ChunkSink，边压边发
                       后端内存里过一遍，磁盘不留交付副本
③ 后端写盘（默认关）    同机自用时才有意义；部署成服务后它是攻击面
```

导出文件名由 `_build_manifest()` **统一决定**，三条通道共用同一份 manifest，所以"导出的文件夹"和"下载的 zip"在命名与元数据上完全等价。

---

## 5. 数据模型

### 5.1 三个域

| 域 | 核心表 | 回答的问题 |
| --- | --- | --- |
| 项目 | `projects` | 有哪些工作？每个做到哪了？ |
| 声线 | `voices`、`project_voices`、`voice_requests`、`candidates` | 有哪些声音？某个角色找到了吗？ |
| 产物 | `outputs`、`output_versions`、`output_order`、`jobs` | 出了哪些音频？哪些进成品？ |

### 5.2 表间关系（v5）

```
projects
├──< voice_requests ──< candidates
│        └─ source_candidate_id ──→ voices（血缘：这把声音从哪条候选来）
├──< project_voices >── voices
├──< outputs ──< output_versions
│        └─ voice_id ──→ voices（可空：单句保留时尚未固化）
├──< output_order >── outputs
├──< jobs
└──< activity_log
```

11 张表、102 个字段的完整定义在 `design/database-schema.md`。这里只讲四条不变量：

**① 项目是聚合根，删项目连坐。** 槽位、台词、产物、排序、流水全走 `ON DELETE CASCADE`；`jobs` 与 `voices.source_project_id` 走 `SET NULL`。删项目 = 库里清空 + `rmtree` 磁盘目录（`projects.purge_project`，先软删再物理删）。

**② 声线是全局资产，不随项目消失。** `voices` 没有"属于哪个项目"，只有"来源项目"（`source_project_id`，可空）。一个声线被多个项目复用的真实情况：21 条挂载记录里，`贾宝玉克隆林黛玉` 被 3 个项目共用。所以删声线要**显式拒绝**——只要还有别的项目在用它、或还有台词绑着它，就不许删（`RESTRICT`），而不是悄悄清掉。

**③ 成品从三源归一。** v5 把 `batch_items` / `voice_requests.direct_output_*` / `supplement_lines` 三条独立路径合并成一张 `outputs`，用 `origin` 区分来源。收益是导出不再 UNION 三次，且三源都有了版本历史（v4 只有 batch 有）。

**④ 状态与归属分开。** `gen_status`（pending/running/completed/failed）管"生成得怎么样"，`in_final` 管"进不进成品"。一个字段兼两职的后果，在 v4 里已经看得见：`request_progress()` 统计"单句成品数"时不敢看 `status='single'`，而是去数 `direct_output_mp3` 有没有值。

### 5.3 v4 → v5 映射

| v4 现状 | v5 | 原因 |
| --- | --- | --- |
| `batch_items` + `batch_item_versions` | `outputs` + `output_versions`（origin=batch） | 三源归一 |
| `voice_requests.direct_output_mp3` / `direct_candidate_id` | `outputs`(origin=single) | 摘掉寄生字段 |
| `supplement_lines` | `outputs`(origin=supplement) | 三源归一 |
| `app_settings['output_order:<pid>']`（JSON 数组） | `output_order` 表 | 可外键、可排序、可 JOIN |
| `app_settings['last_step:<pid>']` | **直接废弃** | 已可由数据推断（`infer_last_step`） |
| `app_settings['batch_export_dir:<pid>']` | `projects.export_dir` | 项目自己的状态不该在 KV 表 |
| `voices` 缺 `x_vector_only` | 加列，从 `metadata.json` 回填 | 关键推理参数必须可查 |
| `voice_requests.voice_id` 无外键 | 补 FK（`SET NULL`） | 规则下沉到 schema |
| 所有枚举裸 TEXT | 全部加 `CHECK` | 写错值当场失败 |
| — | 新增 `jobs` | v4 的任务只活在进程内存，重启即丢 |

### 5.4 迁移怎么做

不是"版本号分支 + 大迁移脚本"，而是**幂等增量**：

```python
# database.py initialize()
1. PRAGMA journal_mode = WAL
2. executescript(schema)          -- 全是 CREATE TABLE IF NOT EXISTS
3. PRAGMA table_info(表)          -- 逐表检查缺哪列
4. 缺列就 ALTER TABLE ADD COLUMN + UPDATE 回填
5. app_settings['schema_version'] 写当前版本
```

好处是任何版本的库开机都能自愈，不需要维护 1→2→3→4 的路径。代价是**删列、改类型、改约束做不了**（SQLite 的 `ALTER TABLE` 不支持），这些必须走 `design/database-design.md` 第 7 节那套"建新表搬数据 + 旧表留视图兼容"。

快照策略是"危险动作前先留一份"：迁移（`pre-migration`）、删项目（`pre-delete-project-*`）、归档项目、删声线、删批量行，各自调 `backup_snapshot()` 往 `DATA_DIR/backups/` 写一份库副本。删的是库，音频文件不在快照里——真正的回滚还要配套的音频目录。

---

## 6. 接口设计

### 6.1 统一约定

| 约定 | 具体做法 |
| --- | --- |
| 风格 | REST + JSON；路径以 `/api/` 开头；资源用复数 |
| 错误 | 后端抛 `HTTPException(status, detail="中文原因")`；前端 `handle()` 统一取出 `detail` 抛 `Error`，页面直接显示 |
| 归属 | 绝大多数路径带 `{project_id}`；`/api/voice-requests/*` 不带，因为槽位操作永远针对"当前项目" |
| 文件访问 | 库里存绝对路径，接口输出时用 `file_url()` 转成 `/api/files/<相对路径>`；`json_safe()` 把 `Path` 转字符串 |
| 缓存 | 前端 GET 有 5 秒内存缓存；轮询类请求必须带 `force: true` 绕过 |
| 写操作副作用 | 前端 `apiSend` 成功后触发 `onProjectWrite`，侧栏据此把该项目挪到列表第一位 |

### 6.2 接口分组

**系统**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/health` | 存活探针 |
| GET | `/api/system/status` | GPU / 模型 / 数据目录 / 磁盘占用 |
| POST | `/api/dialog/pick-folder` | 弹后端机器的目录选择框（需 host fs） |
| POST | `/api/system/open-folder` | 在后端机器上打开文件夹（需 host fs） |

**项目**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/projects?include_archived=` | 项目列表（带槽位/台词统计） |
| POST | `/api/projects` | 新建 |
| PUT | `/api/projects/{pid}` | 改名 |
| POST | `/api/projects/{pid}/activate` | 标记为"最近使用" |
| DELETE | `/api/projects/{pid}?mode=archive`（或 `purge`） | 软删 / 彻底删 |
| GET | `/api/projects/{pid}/activity` | 操作流水 |

**第 1 步 · 声线槽位与候选**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET / POST | `/api/projects/{pid}/voice-requests` | 列出 / 批量建槽位 |
| PUT | `/api/voice-requests/{rid}` | 存草稿（描述、试听文本、语种、改名） |
| DELETE | `/api/projects/{pid}/voice-requests/{rid}` | 删槽位（已固化的资产保留） |
| POST | `/api/projects/{pid}/voice-requests/generate` | 生成候选（后台任务） |
| GET | `/api/voice-generation/status` | 候选生成进度 |
| POST | `/api/voice-generation/cancel` | 取消 |
| GET | `/api/voice-requests/{rid}/candidates` | 该槽位的候选列表 |
| POST | `/api/voice-requests/{rid}/select/{cid}` | 选定某条 |
| POST | `/api/voice-requests/{rid}/candidates/{cid}/shortlist` | 加入/移出备选 |
| POST | `/api/voice-requests/{rid}/candidates/{cid}/keep-single` | 保留为单句成品 |
| POST | `/api/projects/{pid}/candidates/keep-all-single` | 批量保留 |
| POST | `/api/voice-requests/{rid}/reopen` | 解冻（撤销固化） |
| GET | `/api/projects/{pid}/voice-progress` | 六态计数 |

**声线资产库**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/library/voices` | 全局声线库 |
| GET / POST / DELETE | `/api/projects/{pid}/voices`、`/voices/attach`、`/voices/{vid}` | 项目清单：列出 / 挂载 / 移出 |
| DELETE | `/api/library/voices/{vid}` | 删资产（被引用则拒绝） |
| POST | `/api/projects/{pid}/voices/fix` | **固化**：选定的槽位 → 资产 |
| POST | `/api/voices/upload` | 上传录音入库（multipart，≤80 MB，音视频白名单） |
| POST | `/api/voices/design-archive` | 把一次设计试听入库（按 `design_id` 取回同一段音频） |

**第 2、3 步 · 批量与重生成**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET / POST | `/api/projects/{pid}/batch` | 列出 / 追加台词行 |
| POST | `/api/projects/{pid}/batch/run` | 整批合成（后台任务） |
| GET | `/api/projects/{pid}/batch/status` | 进度 |
| POST | `/api/projects/{pid}/batch/cancel` | 取消 |
| POST | `/api/projects/{pid}/batch/clear` | 清掉未生成的 |
| POST | `/api/projects/{pid}/batch/save-edits` | 批量改文本/语种 |
| POST | `/api/projects/{pid}/batch/{item_id}/regenerate` | 单条重生成 |
| POST | `/api/projects/{pid}/batch/{item_id}/select` | 选入/移出成品 |
| DELETE / POST | `/api/projects/{pid}/batch/{position}`、`/batch/delete` | 删单行 / 删多行 |
| POST | `/api/projects/{pid}/voices/{vid}/regenerate` | 按人物整批重做（后台任务） |
| GET / POST | `/api/voice-regeneration/progress`、`/cancel` | 进度 / 取消 |

**第 4 步 · 成品与导出**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/projects/{pid}/outputs` | 三源合并的成品表 |
| PUT | `/api/projects/{pid}/outputs/order` | 保存手动排序 |
| POST / DELETE | `/api/projects/{pid}/outputs/supplement[/{id}]` | 补录 / 删补录 |
| GET | `/api/projects/{pid}/single-outputs` | 单句成品列表（第 1 步保留的） |
| GET | `/api/projects/{pid}/outputs/export-plan` | 导出清单（浏览器写盘用） |
| GET | `/api/projects/{pid}/outputs/archive.zip` | 流式打包 |
| POST | `/api/projects/{pid}/outputs/export` | 后端写盘（需 host fs） |

**独立工具**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/tools/design` | 文字描述试听一段（返回 `design_id`） |
| POST | `/api/tools/clone` | 参考音频克隆试听一段 |
| POST | `/api/tools/convert` | 音频格式转换（wav/mp3/flac…） |
| POST | `/api/tools/conversions/archive` | 把转换结果打包下载 |

**文件**

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/files/{path:path}` | 数据目录下的文件（音频、图片） |
| GET | `/api/projects/{pid}/files/{path:path}` | 项目 `outputs/` 下的文件 |

### 6.3 三种执行模型

选哪种，取决于耗时和是否需要可见进度：

| 模型 | 用在哪 | 形态 |
| --- | --- | --- |
| **同步** | 增删改查、补录单句（一次推理）、固化、导出 | 请求进来干完就回，前端转圈 |
| **后台线程 + 轮询** | 候选生成、整批合成、按人物重生成 | `POST` 立刻回 `{started:true}`，前端每 1~2 秒 `GET .../status` 直到 `done` |
| **流式** | ZIP 打包 | `StreamingResponse` + 生成器，边压边发，不占磁盘 |

后台模型由 `studio/jobs.py` 的 `BackgroundJob` 统一实现，每个业务组件持有一个实例：

```python
BackgroundJob(name, done_key)
  start(prepare, runner)   # 有任务在跑 → raise RuntimeError(中文提示)
  update(**fields)         # runner 内部上报进度
  finish(**fields)         # 置 done
  snapshot()               # 轮询接口读的字典快照
  request_cancel()         # 协作式取消：runner 在每行之间检查
```

三个设计点：

- **`prepare` 在锁内执行**，所以"检查是否忙"和"建请求行"是原子的，不会出现两个请求都通过检查。
- **取消是协作式的**：设标志位，`runner` 在每条台词之间看一次。正在推理的那一条不会被中断（GPU 调用打断不划算）。
- **进度是内存里的一个 dict**，不落库。所以服务重启后进度就没了——v5 新增 `jobs` 表就是为了补这个洞。

### 6.4 安全边界

| 场景 | 措施 | 位置 |
| --- | --- | --- |
| 上传 | 后缀白名单 + 80 MB 上限 | `api.py: ALLOWED_UPLOAD_SUFFIXES` |
| 读任意文件 | `resolve()` 后必须 `relative_to(root)` | `/api/files/*`、`download_project_file` |
| 前端指定后端读哪个文件 | 路径段强制 uuid 正则 + 上面的双校验 | `/api/voices/design-archive`、`/api/tools/conversions/archive` |
| 往服务器写文件 | 默认 403；开启后目标仍限制在 `DATA_DIR` 内 | `require_host_fs()`、`export_completed_batch` |
| 删声线 | 被其他项目挂载或被台词引用 → 直接拒绝，不静默清引用 | `delete_voice_asset` |

原则只有一条：**凡是"由调用方指定后端去读写某个路径"的接口，路径必须被约束在一个已知根内**，靠 `resolve() + relative_to()` 双校验，不能靠字符串前缀。

---

## 7. 磁盘布局

```
DATA_DIR/                                 默认 %LOCALAPPDATA%\QwenTTSVoiceoverStudio
├── app.db                                SQLite（WAL 模式，同目录有 -wal / -shm）
├── gpu.lock                              跨进程 GPU 锁，一个字节
├── backups/                              迁移/删除前的快照
├── logs/batch-regeneration.log           按人物重生成的日志（2 MB 轮转 ×3）
├── temp/
│   ├── standalone/voicedesign/           设计/克隆试听的临时产物
│   ├── conversions/                      格式转换的临时产物
│   └── uploads/                          上传录音的落地副本
├── voices/<voice_id>/
│   ├── reference.wav
│   ├── prompt.safetensors
│   ├── preview.mp3
│   └── metadata.json
└── projects/<project_id>/
    ├── manifest.json                     批量台词的清单（batch 组件写）
    ├── candidates/<candidate_id>/        候选试听（wav + 预览）
    └── outputs/
        ├── <声线显示名>/                 批量产物：0001_贾宝玉_v01.mp3
        ├── single_line/                  第 1 步保留的单句成品
        ├── supplement/<声线名>/          补录产物
        └── quick/<voice_id 前 8 位>/     「全部声线」页的快速试听
```

**谁写谁读：**

| 目录 | 写 | 读 |
| --- | --- | --- |
| `voices/` | `voice_assets`（入库、重生成） | `voice_assets.generate_with_voice` |
| `projects/<pid>/candidates/` | `voice_workflow` | 试听接口、`keep-single` |
| `projects/<pid>/outputs/<声线>/` | `batch` | `exports` |
| `outputs/single_line/` | `voice_workflow` | `exports` |
| `outputs/supplement/` | `supplement` | `exports` |
| `outputs/quick/` | `voice_assets`（全声线页快速试听） | 仅试听，不进成品 |
| `temp/` | `tools` | 入库/打包时读 |

**已知缺口**：`temp/standalone/`、`temp/conversions/`、`temp/uploads/`、`outputs/quick/` **都没有清理机制**，会随时间涨。这些文件不参与业务，删掉不丢数据，但也没有任何代码会自动删。

---

## 8. 关键取舍

**为什么库里的音频只存路径，不存 BLOB？**
一个项目上百条音频、每条几百 KB 到几 MB，存 BLOB 会让 `app.db` 变成几 GB，备份、VACUUM、WAL 全被拖慢。代价是"文件与行可能不一致"——所以删除音频时**先删文件再删行**，导出/列表时都 `Path.exists()` 过滤一遍。

**为什么批量合成慢还要一行一行跑？**
GPU 一次只能载一个模型、跑一个请求。并行只会把显存撑爆或让每行都变慢。串行的额外好处是取消能精确停在行边界。

**为什么 `host fs` 默认关闭？**
服务一旦绑 `0.0.0.0`，"往服务器任意路径写文件"就等于把整台机器开放给局域网。默认关，单机自用再开。

**为什么前端要 keep-alive？**
切页不卸载（`visitedRef` + `hidden`），因为工作台里选中的台词、滚动位置、播放进度都是页面内 state，卸载就全丢。代价是后台轮询要在隐藏时主动暂停，各页面通过 `active` prop 拿到这个信号。

**为什么 GET 有 5 秒缓存？**
组件重挂载（切步骤、切项目回来）会重复请求同样的数据。5 秒内命中缓存就秒开。所有轮询接口必须 `force: true`，否则进度会"卡住 5 秒"。

**为什么不干脆做成多用户服务？**
当前所有表都没有 `owner_id`，`/api/projects` 返回全部项目。真要多用户，得先加归属、鉴权、租户隔离——这是另一层工程，v5 只在 `jobs` 和归属字段上留了口子，没做。

---

## 9. 术语表

| 词 | 含义 |
| --- | --- |
| 声线（voice） | 一把可复用的音色，对应 `voices` 里一行 + `voices/<id>/` 一组文件 |
| 槽位（request / slot） | 第 1 步里"这个角色需要一个声音"的待办格，对应 `voice_requests` 一行 |
| 候选（candidate） | 同一槽位下生成的一条试听，可以有一轮多条 |
| 固化（fix） | 把选定的候选正式写入 `voices` + `project_voices`，此后可批量使用 |
| 解冻（reopen） | 撤销固化，槽位退回 `candidate`，资产保留 |
| 单句成品（single） | 不固化、直接把某条候选保留成一个可交付文件 |
| 补录（supplement） | 成品阶段事后追加的一句台词 |
| 成品（deliverable） | 进入导出的那些音频；由 batch 选定 + 单句 + 补录三源合成 |
| 参考音频 / ref_text | 克隆声线用的素材录音，以及它对应的文本 |
| `x_vector_only` | 只用说话人向量、不使用参考音频文本内容 |

---

## 10. 想改代码，从哪下手

| 你想做的事 | 该看哪里 |
| --- | --- |
| 加一个接口 | `api.py` 加路由 → 对应组件的 service 方法；不要在 `StudioService` 上加新转发 |
| 改第 1 步的交互 | `frontend/features/voice/`（`VoiceWorkbench` + 4 个 hook） |
| 改合成参数、换模型 | `model_manager.py`（`design` / `clone` / `load_prompt`） |
| 改导出命名、加元数据字段 | `exports._build_manifest()` —— 三条导出通道唯一共用的地方 |
| 加一张表 | `database.py` 的 schema 字符串 + `initialize()` 里补列逻辑 + `design/database-schema.md` |
| 排查"生成的音频不对" | 先看 `voices/<vid>/metadata.json` 的 `x_vector_only` 与 `ref_text`，再看 `candidates/<cid>/` 的原始 wav |
| 排查"进度卡住" | `jobs.py` 的 `snapshot()`；轮询接口是否漏了 `force: true` |

验证手段：`scripts/` 下有 `api_smoke.py`、`smoke_test.py`、`migration_smoke.py`、`data_integrity_check.py`、`studio_e2e_smoke.py`。跑后端前记得把 `QWEN_TTS_DATA_DIR` 指向副本，别碰真实数据。
