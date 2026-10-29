# 架构图集（Mermaid 源码）

> 图里的每个节点、每条依赖都对应仓库里的真实代码，标注格式为 `文件:行号`。
> 在支持 Mermaid 的查看器（IDE 预览、GitLab/GitHub 网页）里会直接渲染成图形。

目录：

1. [部署形态与请求链路](#1-部署形态与请求链路)
2. [项目业务流程图（工作台主链路）](#2-项目业务流程图工作台主链路)
3. [UML 类图：后端组件划分](#3-uml-类图后端组件划分)
4. [UML ER 图：数据模型](#4-uml-er-图数据模型)
5. [UML 状态图：批量台词项](#5-uml-状态图批量台词项)
6. [UML 时序图：单人跑一条批量任务](#6-uml-时序图单人跑一条批量任务)
7. [UML 并发时序图：多人同时使用（含现有缺陷）](#7-uml-并发时序图多人同时使用含现有缺陷)

---

## 1. 部署形态与请求链路

一台 Windows 主机同时是开发机和部署机；对外只开 3000，后端 7871 只绑本机回环。

```mermaid
flowchart LR
    subgraph Client["调用者浏览器（局域网同事，零安装）"]
        C1["打开 http://服务器IP:3000"]
        C2["localStorage：声线表单草稿、项目选中态<br/>frontend/features/voice/draft.ts"]
    end

    subgraph Host["服务器本机（Windows + RTX 单卡）"]
        FE["Next.js 前端 :3000<br/>frontend/next.config.mjs"]
        BE["FastAPI 后端 127.0.0.1:7871<br/>studio/api.py:960-965"]
        SVC["StudioService 组件层<br/>studio/service.py:35-47"]
        DB["SQLite app.db（WAL + 进程内串行锁）<br/>studio/database.py:22-47"]
        MM["ModelManager（进程内 RLock，只缓存一个模型）<br/>studio/model_manager.py:123-152"]
        GPU["GPU：VoiceDesign 1.7B ⇄ Clone 1.7B"]
        DISK["数据目录 DATA_DIR<br/>projects/ voices/ temp/ backups/<br/>studio/config.py:28-50"]
    end

    C1 -->|"页面 + 全部 /api/*（含 /api/files/* 音频）"| FE
    FE -->|"同源 rewrite 转发，proxyTimeout=600s"| BE
    BE --> SVC
    SVC --> DB
    SVC --> MM
    MM --> GPU
    SVC --> DISK
    FE -.->|"浏览器只访问 3000，CORS 无需配置"| BE
    DB -.->|"data_dir 由 QWEN_TTS_DATA_DIR 决定，仓库 data 不是真身"| DISK
```

要点：

- 浏览器只访问 3000，所以 **CORS 无需配置**，后端换绑定地址也不影响同事使用。
- 成品交付走两条不同路径：**ZIP 流式**（`/api/projects/{project_id}/outputs/archive.zip`，`studio/api.py:823-844`，服务器不留文件）和**浏览器写盘**（`export-plan` 出清单 + `showDirectoryPicker`，`studio/api.py:847-865`、`frontend/lib/localExport.ts:69`）。
- 写服务器本机磁盘/弹目录框的接口默认关闭，靠 `QWEN_TTS_ALLOW_HOST_FS` 收口（`studio/api.py:60-73`）。

---

## 2. 项目业务流程图（工作台主链路）

> 2026-10-16 按现行代码重绘。旧版流程图已过时：接口路径写成了不存在的
> `voice-slots`（实际是 `voice-requests`），且未体现「新建项目两种方式决定落点」这条规则。

### 2.1 创建项目：两种方式决定落点

新建向导（`frontend/features/projects/NewProjectWizard.tsx`）第一步只有两种方式，
**落点由此确定**（`frontend/features/projects/useProjects.ts` 的 `createProject` /
`consumeEntryStep`，组合根在 `frontend/app/page.tsx` 的 `openProject` 消费）：

| 方式 | 含义 | 勾选声线 | 落点 |
|------|------|----------|------|
| A | 创建新声线 | 否 | 第 1 步（创建声线） |
| B | 使用已有声线 | 是（至少 1 个） | 第 2 步（配音生成） |

进入**既有**项目（侧栏、总览页「进入」）一律落第 2 步。
项目在向导完成时才真正创建；方式 B 挂载声线只建 `project_voices` 关联，不复制音频文件。

### 2.2 主干链路

```mermaid
flowchart TD
    S0["新建项目向导<br/>输入名称 + 选方式"] --> SA{"方式"}
    SA -->|"A：不选声线"| S1
    SA -->|"B：勾选已有声线"| S2

    subgraph P1["第 1 步　创建声线（VoiceWorkbench）"]
        direction TB
        S1["声线来源"] --> S1a
        S1a{"来源"} -->|"文字描述音色"| S1b["后台生成候选（最多 8 个）<br/>POST .../voice-requests/generate"]
        S1a -->|"上传音频"| S1c["上传声线资产<br/>POST /api/voices/upload"]
        S1a -->|"复用声线库"| S1d["挂载全局库声线到项目清单<br/>POST .../voices/attach"]
        S1b --> S1e["试听 / 暂存 / 选定候选"]
        S1d --> S1f["固化：写入声线资产<br/>POST .../voices/fix"]
        S1e --> S1f
    end

    S1f --> S2

    subgraph P2["第 2 步　配音生成（BatchPage）"]
        direction TB
        S2["选已固化声线 + 粘贴台词入队<br/>POST .../batch"] --> S2a["启动批量生成（后台线程，逐条串行）<br/>POST .../batch/run"]
        S2a --> S2b{"每条结果"}
        S2b -->|成功| S2c["status=selected，自动计入成品"]
        S2b -->|失败| S2d["status=failed + error 落库<br/>可改文本后重试该条"]
        S2d -->|重试| S2a
    end

    S2c --> S3

    subgraph P3["第 3 步　试听返修（ResultsPage）"]
        direction TB
        S3["逐条试听，决定是否选入成品"] --> S3a{"返修粒度"}
        S3a -->|"单条"| S3b["POST .../batch/&#123;item_id&#125;/regenerate"]
        S3a -->|"整个声线"| S3c["POST .../voices/&#123;voice_id&#125;/regenerate（后台线程）"]
    end

    S3b --> S4
    S3c --> S4

    subgraph P4["第 4 步　成品导出（ProductsPage）"]
        direction TB
        S4["成品列表（批量选中 + 单句保留 + 补录）<br/>拖拽排序 PUT .../outputs/order"] --> S4a{"交付方式"}
        S4a -->|"浏览器写盘（多人推荐）"| S4b["GET .../outputs/export-plan<br/>前端逐文件写入本地目录"]
        S4a -->|"ZIP 下载"| S4c["GET .../outputs/archive.zip（流式，不落服务器盘）"]
        S4a -->|"仅单机自用"| S4d["POST .../outputs/export<br/>复制到后端本机文件夹"]
        S4 --> S4e["补录漏句：POST .../outputs/supplement"]
    end

    S0 -.-> M1["项目总览页：切换 activate / 重命名 / 删除（删除前全量快照）"]
    S0 -.-> M2["声线库页：跨项目复用、删除声线资产"]

    S2d -.->|"失败行无音频，保留 error 供成品页展示与删除"| S4
    S4b -.->|"不支持 showDirectoryPicker 时降级 ZIP"| S4c

    classDef gpu fill:#fde8e8,stroke:#c0392b,color:#7c1d1d;
    class S1b,S1c,S2a,S3b,S3c,S4e gpu;
```

红色节点都会走到 GPU（`ModelManager.design/clone`，`studio/model_manager.py:165-227`），
同一时刻只能有一个在推理——这是"多人同时使用"的物理约束来源。

要点：

- 第 1 步的固化是**批量动作**：一次固化本项目所有已选声线（`.../voices/fix`），
  也可以单个槽位「解冻」后重新生成与选择（`.../voice-requests/&#123;id&#125;/reopen`）。
- 第 3 步每条台词保留最近 3 个历史版本，返修不会覆盖旧音频。
- 第 4 步的「补录」用于漏句（`.../outputs/supplement`），补录项同样会走 GPU。
- 步骤条在顶栏（`frontend/features/workflow/steps.ts`），仅「工作台」页且有当前项目时显示。


---

## 3. UML 类图：后端组件划分

依赖是单向的（`studio/service.py:7-13` 的注释即契约）：`exports → batch → voice_assets → model_manager`，`tools → model_manager`。新增代码应直接用 `service.batch` 这类组件，`service.*` 的委托方法只是给 API 保留的历史兼容面。

```mermaid
classDiagram
    class ApiLayer {
        <<FastAPI app>>
        +require_project()
        +require_host_fs()
        +file_url()
    }

    class StudioService {
        <<composition facade>>
        +db
        +models
        +projects
        +workflow
        +assets
        +supplements
        +batch
        +exports
        +tools
    }

    class ProjectService {
        +create_project()
        +switch_project_context()
        +delete_project()
        +validate_data_integrity()
    }

    class VoiceWorkflowService {
        +create_voice_slots()
        +start_candidate_generation()
        +candidate_generation_status()
        +select_candidate()
        +keep_candidate_as_single_output()
        +fix_selected_voices()
    }

    class VoiceAssetService {
        +archive_uploaded_voice()
        +generate_with_voice()
        +delete_voice_asset()
    }

    class SupplementService {
        +create()
        +list_lines()
    }

    class BatchService {
        +add_batch_lines()
        +start_batch()
        +run_batch()
        +select_batch_item()
        +regenerate_batch_item()
        +start_voice_regeneration()
        +cancel_running_batch()
    }

    class ExportService {
        +export_plan()
        +iter_archive()
        +create_batch_archive()
        +order_output_rows()
    }

    class ToolsService {
        +convert_files()
        +design_sample()
        +clone_sample()
    }

    class Database {
        -path
        -lock_RLock
        +connection()
        +transaction()
        +execute()
        +fetch_one()
        +fetch_all()
        +get_setting()
        +set_setting()
        +log_activity()
        +backup_snapshot()
        +new_id()
    }

    class ModelManager {
        -lock_RLock
        -model
        -kind
        +load()
        +design()
        +clone()
        +create_prompt()
        +unload()
        +status()
    }

    class CrossProcessGpuLock {
        -path
        +acquire()
        +release()
    }

    class BackgroundJob {
        <<single slot>>
        -lock_RLock
        -thread
        -cancel_Event
        -progress_dict
        +start()
        +request_cancel()
        +update()
        +finish()
        +snapshot()
    }

    class ProjectContext {
        <<frozen dataclass>>
        +current_project_id
        +name
        +status
    }

    class Qwen3TTSModel {
        <<external>>
        +generate_voice_design()
        +generate_voice_clone()
        +create_voice_clone_prompt()
    }

    ApiLayer --> StudioService
    StudioService *-- ProjectService
    StudioService *-- VoiceWorkflowService
    StudioService *-- VoiceAssetService
    StudioService *-- SupplementService
    StudioService *-- BatchService
    StudioService *-- ExportService
    StudioService *-- ToolsService
    StudioService *-- Database
    StudioService *-- ModelManager

    ProjectService --> Database
    VoiceWorkflowService --> Database
    VoiceAssetService --> Database
    SupplementService --> Database
    BatchService --> Database
    ExportService --> Database
    ToolsService --> Database

    VoiceWorkflowService --> ModelManager : voice_design
    VoiceAssetService --> ModelManager : clone
    ToolsService --> ModelManager
    ModelManager *-- CrossProcessGpuLock
    ModelManager ..> Qwen3TTSModel : from_pretrained

    VoiceWorkflowService *-- BackgroundJob : candidate_job
    BatchService *-- BackgroundJob : batch_job
    BatchService *-- BackgroundJob : regen_job
    ProjectService ..> ProjectContext : returns
    ExportService --> BatchService
    ExportService --> VoiceWorkflowService
    ExportService --> SupplementService
    BatchService --> VoiceAssetService
    SupplementService --> VoiceAssetService
```

各类的语义要点（图上放不下）：

| 类 | 关键约束 | 位置 |
| --- | --- | --- |
| `ApiLayer` | CORS 默认放开所有来源；`file_url` 只发数据目录内的路径且屏蔽 backups；写本机类接口受 `QWEN_TTS_ALLOW_HOST_FS` 控制 | `api.py:33-73` |
| `ProjectService` | `create_project` 同名即返回已有 id；`switch_project_context` 写全局 `last_project_id`；`delete_project` 先全量快照再 `rmtree` | `projects.py:24-145` |
| `BatchService` | `start_batch` 走单槽 `BackgroundJob`；`cancel_running_batch` 无条件 `request_cancel()` 后再按 project_id 回滚行 | `batch.py:253-367` |
| `VoiceWorkflowService` | `candidate_generation_status()` 无项目参数，返回进程唯一快照；`prepare()` 在 job 锁内跑，建行与占用检查原子 | `voice_workflow.py:359-454` |
| `VoiceAssetService` | `generate_with_voice` 是 wav→mp3→删 wav，产物只留 mp3；依赖 `voices/{voice_id}/metadata.json` 决定 x_vector_only | `voice_assets.py:144-170` |
| `ExportService` | `export_plan` 是文件名与 manifest 的唯一来源；`iter_archive` 全程流式不落本机盘 | `exports.py:198-299` |
| `Database` | 进程级 `RLock` 串行所有读写，每次调用新建连接，`timeout=30`；`backup_snapshot` 会 copytree 整个 projects 与 voices 目录，保留 30 份 | `database.py:22-47`、`231-321` |
| `ModelManager` | 只缓存一个模型，kind 变化即 unload+reload；`clone` 对过短/近静音结果最多重试 5 次；推理全程持 `_lock` | `model_manager.py:123-241` |
| `CrossProcessGpuLock` | `msvcrt` 字节段锁，锁到进程退出；非 Windows 直接跳过（无跨进程保护） | `model_manager.py:17-58` |
| `BackgroundJob` | `start()` 见线程存活即抛 `RuntimeError`；cancel 是循环边界的标志位，正在跑的那一条必然跑完 | `jobs.py:17-110` |

---

## 4. UML ER 图：数据模型

`studio/database.py:52-169`。音频文件本身在磁盘（`DATA_DIR/projects/{project_id}/…`），表里存绝对路径。

```mermaid
erDiagram
    PROJECTS ||--o{ VOICE_REQUESTS : "槽位/声线需求"
    PROJECTS ||--o{ VOICES : "声线资产（删除置 NULL）"
    PROJECTS ||--o{ BATCH_ITEMS : "台词"
    PROJECTS ||--o{ SUPPLEMENT_LINES : "补录句"
    PROJECTS ||--o{ ACTIVITY_LOG : "操作流水（无调用者字段）"
    VOICE_REQUESTS ||--o{ CANDIDATES : "候选试听"
    VOICE_REQUESTS |o--o| VOICES : "voice_id 固化后指向"
    BATCH_ITEMS ||--o{ BATCH_ITEM_VERSIONS : "多版本保留"
    VOICES ||--o{ BATCH_ITEMS : "RESTRICT，仍被绑定不许删"
    VOICES ||--o{ SUPPLEMENT_LINES : "RESTRICT"

    PROJECTS {
        TEXT id PK
        TEXT name UK "UNIQUE — 多人会撞名"
        TEXT status "draft"
        TEXT created_at
        TEXT updated_at
    }
    VOICE_REQUESTS {
        TEXT id PK
        TEXT project_id FK
        INTEGER position
        TEXT name "UNIQUE(project_id,name)"
        TEXT prompt
        TEXT audition_text
        TEXT language
        TEXT status "candidate / selected / fixed"
        INTEGER candidate_round
        TEXT voice_id FK
        TEXT direct_output_mp3 "迁移列：单句保留成品"
    }
    CANDIDATES {
        TEXT id PK
        TEXT request_id FK
        INTEGER seed
        TEXT wav_path
        TEXT preview_path
        INTEGER selected
        INTEGER shortlisted
        INTEGER round
    }
    VOICES {
        TEXT id PK
        TEXT project_id FK
        TEXT name
        TEXT source "design / clone / upload"
        TEXT ref_text
        TEXT reference_wav
        TEXT preview_mp3
        TEXT prompt_path "voice_clone_prompt json"
        TEXT status
    }
    BATCH_ITEMS {
        TEXT id PK
        TEXT project_id FK
        TEXT voice_id FK
        INTEGER position
        TEXT text
        TEXT language
        TEXT status "pending running completed selected failed"
        TEXT output_mp3
        INTEGER version
        TEXT error
    }
    BATCH_ITEM_VERSIONS {
        TEXT id PK
        TEXT item_id FK
        INTEGER version "UNIQUE(item_id,version)"
        TEXT output_mp3
        TEXT text
    }
    SUPPLEMENT_LINES {
        TEXT id PK
        TEXT project_id FK
        TEXT voice_id FK
        TEXT text
        TEXT status
        TEXT output_mp3
        TEXT error
    }
    APP_SETTINGS {
        TEXT key PK "含全局 last_project_id"
        TEXT value
    }
    ACTIVITY_LOG {
        TEXT id PK
        TEXT project_id FK
        TEXT entity_type
        TEXT entity_id
        TEXT action
        TEXT detail
    }
```

---

## 5. UML 状态图：批量台词项

`studio/batch.py` 的写入点。注意"成功即入成品"是设计决定（声线此时已定），`selected → completed` 是返修页的退出口。

```mermaid
stateDiagram-v2
    [*] --> pending : add_batch_lines / 入队
    pending --> running : run_batch 逐条取下一条
    running --> selected : 生成成功，写 output_mp3 并记版本
    running --> failed : 抛异常，写 error
    failed --> running : 重试（run_batch 取 pending+failed）
    selected --> completed : 取消选入成品
    completed --> selected : 选入成品
    pending --> [*] : 删除行（跑批循环会跳过已删行）
    failed --> [*] : 删除行
    running --> pending : 用户取消，回滚为等待（cancel_running_batch）
    selected --> [*]

    note right of running
        cancel_requested 是循环边界的标志位，
        正在推理的那一条一定跑完才停
    end note
```

---

## 6. UML 时序图：单人跑一条批量任务

这条链路的并发语义：HTTP 立即返回，线程后台跑，前端轮询。同步请求线程不会被长任务占住。

```mermaid
sequenceDiagram
    autonumber
    participant U as 浏览器
    participant N as Next 前端 3000
    participant A as FastAPI 后端
    participant B as BatchService
    participant J as BackgroundJob 单槽
    participant W as 工作线程 batch-xxxx
    participant M as ModelManager
    participant D as Database
    participant F as 磁盘输出目录

    U->>N: POST batch/run
    N->>A: 转发（同源 rewrite）
    A->>B: start_batch
    B->>J: start(prepare, runner)
    J->>J: 持锁检查线程存活并执行 prepare
    Note over J: prepare 与占用检查在同一把锁内，<br/>校验项目存在并统计待生成条数
    J->>W: 启动守护线程
    J-->>A: task 快照（project_id、total、succeeded）
    A-->>U: 200 started=true 加 task（毫秒级返回）

    loop 每条台词（串行）
        W->>D: SELECT 行是否还在 + 取 voice
        W->>D: UPDATE status running
        W->>M: clone(text, language, prompt_items)
        Note over M: 进程内锁：全服务同一时刻只有一次推理<br/>kind 不匹配则 unload 后重新 from_pretrained
        M->>F: write_wav → wav_to_mp3 → unlink wav
        W->>D: UPDATE status='selected',output_mp3 + 记版本
        W->>J: update(succeeded,failed)
    end

    W->>F: _write_manifest(project_id)
    W->>D: log_activity("批量生成完成")
    W->>J: finish(succeeded,failed,done=true)

    Note over U,J: 以下轮询与上面的跑批线程并行进行
    loop 每 2.5s
        U->>N: GET batch/status
        N->>A: 转发
        A->>B: status_counts 加 batch_task_status
        B->>J: snapshot(add_running=true)
        A-->>U: task、counts、running
    end
    U->>U: 进度签名不变则不重渲染
```

---

## 7. UML 并发时序图：多人同时使用（含现有缺陷）

把上面聊到的 4 个 P0 问题画在一张图上。`⚠` 标记的交互是当前代码的真实行为，不是设想。

```mermaid
sequenceDiagram
    autonumber
    participant A as 用户 A 浏览器
    participant B as 用户 B 浏览器
    participant API as FastAPI（同一进程）
    participant CJ as candidate_job 单槽
    participant BJ as batch_job 单槽
    participant MM as ModelManager（一份模型缓存）
    participant GPU as GPU 串行
    participant DB as Database（RLock 串行）

    A->>API: POST /projects/{pa}/voice-requests/generate
    API->>CJ: start() → 线程 candidates-aaa
    CJ-->>A: 200 started

    B->>API: POST /projects/{pb}/voice-requests/generate
    API->>CJ: start()
    CJ-->>B: ⚠ RuntimeError → HTTP 409<br/>"已有候选生成任务在运行"（无排队、无归属）

    loop A 每 1.5s，B 每 1.5s 同时轮询
        A->>API: GET /api/voice-generation/status
        API-->>A: task{project_id=pa,...}
        B->>API: GET /api/voice-generation/status
        API-->>B: ⚠ 同一份 task（project_id=pa）<br/>前端 project_id 不匹配就 return → B 界面像卡死，不知道 GPU 被别人占着
    end

    A->>API: POST /projects/{pa}/batch/run
    API->>BJ: start() → 线程 batch-aaaa
    B->>API: POST /projects/{pb}/batch/run
    API->>BJ: start()
    BJ-->>B: ⚠ 409（同一台机只有一个批量槽）

    Note over B,BJ: 数分钟后 A 的批量还在跑
    B->>API: POST /projects/{pb}/batch/cancel（只想取消自己的）
    API->>BJ: ⚠ request_cancel() 无条件命中全局唯一 job
    BJ-->>A: A 的任务在下一条边界被打断
    Note over BJ,DB: 只有 pb 的 running 行被回滚为 pending<br/>pa 的行留在 running

    par A 在配音（clone）
        A->>API: POST .../batch/{item_id}/regenerate
        API->>MM: clone → load("base")
        MM->>GPU: 1.7B 权重载入
        GPU-->>MM: 音频
    and B 在设计声线（voice_design）
        B->>API: POST .../voice-requests/generate
        API->>MM: design → load("voice_design")
        MM->>MM: ⚠ kind 不匹配 → unload() + 重新 from_pretrained
        MM->>GPU: 又一次模型加载
    end
    Note over MM,GPU: ⚠ 8GB 装不下两份 → 两人交替使用 = 每次调用都重载模型<br/>单次耗时从秒级涨到几十秒，双方都变慢

    A->>API: GET /api/files/projects/{pa}/outputs/quick/xxx.mp3
    API->>DB: （无项目归属校验）
    API-->>A: 音频
    B->>API: ⚠ GET /api/files/projects/{pa}/outputs/...（知道路径即可读 A 的音频）
    API-->>B: 200 —— `/api/files` 只校验"在数据目录内 + 是音频"
    Note over API: 项目级通道 /api/projects/{pid}/files/... 有隔离，两通道并存等于没隔离

    A->>API: POST /projects/{pa}/activate
    API->>DB: set_setting("last_project_id", pa)
    B->>API: POST /projects/{pb}/activate
    API->>DB: ⚠ 同一个键被覆盖成 pb（全局一份；多个客户端读写同键）
    B->>API: POST /projects {name:"红楼梦"}
    API->>DB: ⚠ 同名查到即返回 A 的 project_id → B 写进 A 的项目
```

并发时序的三个层次，按修复代价从低到高：

| 层 | 现象 | 最小修法 |
| --- | --- | --- |
| 归属 | 409/进度/取消都不区分谁 | 提交时带调用者标识（cookie），状态接口回 `{occupied_by, project_id}`，cancel 校验归属 |
| 排队 | 第二个人被硬拒 | 单槽改成 FIFO 队列 + 排队位次显示（GPU 仍串行，只是不丢任务） |
| 模型抖动 | 交替换入换出 | 按 kind 合并成一条 GPU 队列，同 kind 连续跑完再换；或约定分时段 |

---

## 附：前端页面与状态结构

```mermaid
flowchart TD
    ROOT["app/page.tsx 组合根<br/>持有唯一 useProjects 实例"]
    ROOT --> PS["ProjectSwitcher（侧栏下拉）"]
    ROOT --> NAV{"侧栏导航"}
    NAV -->|声线| WF["WorkflowPage<br/>step 1..4 用 key={projectId} 强制重挂载"]
    NAV -->|配音| BP["BatchPage"]
    NAV -->|成品| PR["ProductsPage（audioOnly）"]
    NAV -->|声线资产| AS["AssetsPage + QuickGeneratePanel"]
    NAV -->|上传声线| UV["UploadVoicePage"]
    NAV -->|项目管理| PJ["ProjectsPage（切换/新建/删除）"]

    WF --> W1["1 VoiceWorkbench<br/>useVoiceGeneration 轮询全局 status"]
    WF --> W2["2 BatchPage<br/>useBatch 轮询 /batch/status 2.5s"]
    WF --> W3["3 ResultsPage<br/>VoiceRegenPanel 轮询全局 progress"]
    WF --> W4["4 ProductsPage<br/>5s 轮询 + 拖拽排序"]

    PS --> UP["useProjects.ts<br/>首次进页面取列表第一项（本地优先）<br/>切换时 POST activate（失败静默）"]
    W1 --> LS["localStorage：voice-draft:{projectId}<br/>+ 已见任务 request_id 标记"]

    classDef poll fill:#fff3cd,stroke:#b7791f,color:#7c5c12;
    class W1,W2,W3,W4 poll;
```
