# 数据库设计方案：项目 / 声线 / 音频产物

> 对象：SQLite `app.db`（`%LOCALAPPDATA%\QwenTTSVoiceoverStudio\app.db`，当前 `SCHEMA_VERSION = 4`）  
> 代码依据：`studio/database.py`（schema 与迁移）、`studio/voice_workflow.py` / `voice_assets.py` / `batch.py` / `supplement.py` / `exports.py`（写入方）  
> 本文只出方案，不改代码。数据量实测（2026-10-20）：projects 15 / voice_requests 30 / candidates 82 / voices 47 / project_voices 21 / batch_items 101 / batch_item_versions 114 / supplement_lines 3 / activity_log 534。

---

## 1. 需要记录什么（领域分析）

三条业务主线，`落到数据库其实是`**`四个层次`**`：`

### 1.1 项目（Project）

| 内容    | 说明                                   | 现状落点                                           |
| ----- | ------------------------------------ | ---------------------------------------------- |
| 身份    | id、name                              | `projects`                                     |
| 生命周期  | status（draft / active / deleted，软删除） | `projects.status`                              |
| 工作台状态 | **不持久化**（由数据推断，见下）                   | 直接废弃 `app_settings` 的 `last_step:<pid>`        |
| 导出目录  | 本机导出路径记忆                             | `app_settings` 的 `batch_export_dir:<pid>`      |
| 成品顺序  | 成品页手动拖拽后的全量排序                        | `app_settings` 的 `output_order:<pid>`（JSON 数组） |
| 时间    | created_at / updated_at              | `projects`                                     |

**要点**：项目是聚合根。删除项目 = CASCADE 掉槽位、台词、补录、流水，同时 `rmtree` 磁盘目录（`projects.py:113` 先软删除再物理删）。声线资产**不**随项目删除（`voices.project_id` 是"来源项目"语义，`ON DELETE SET NULL`）。

**为什么步次不记录**：`projects.py:207` 的 `infer_last_step()` 已经能从数据推断——没有声线清单→第 1 步，有清单没台词→第 2 步，有台词未全部完成→第 3 步，全部完成→第 4 步。库里那条 `last_step:<pid>` 只是覆盖推断值的 UI 偏好，不是业务事实。删除后行为退化为"按数据进度落步"，与向导设计意图一致；`api.py` 的两个接口（`get_last_step` / `set_last_step`）随之废弃即可。

### 1.2 声线（Voice）—— 四个层次，别混为一谈

现有代码已经把它们分成四层，这是对的，问题在各层的字段完整性：

| 层次      | 表                | 职责                                                                        |
| ------- | ---------------- | ------------------------------------------------------------------------- |
| ① 声线资产  | `voices`         | 全局可复用资产：参考音频、克隆 prompt、试听、标签备注。**不属于任何项目**（`project_id` 只是来源标注，可空）        |
| ② 项目清单  | `project_voices` | 某项目绑定了哪些资产 + 项目内显示名（`display_name` 项目内唯一）                                 |
| ③ 工作台槽位 | `voice_requests` | 第 1 步的"声线需求工作纸"：名称、音色描述、试听文本、语种、状态机（draft→ready→candidate→selected→fixed） |
| ④ 候选试听  | `candidates`     | 槽位下每轮生成的候选音频（seed、round、selected、shortlisted）                             |

声线资产需要记录的字段：

- **来源**：`source`（实际取值 `voice_design` / `upload` 两种——`voice_workflow.py:746` 固化和 `archive_designed_voice` 都写 `voice_design`，上传写 `upload`；design 文档里的 `design/clone` 是过时描述）
- **素材三件套**：`reference_wav`（参考音频路径）、`ref_text`（素材对应文字）、`ref_language`（素材语种）；`x_vector_only`（是否仅用 x-vector，**目前只存在于磁盘 `metadata.json`，没落库**）
- **设计描述**：`prompt`（自然语言音色描述，voice_design 来源用）、`language`（试听文本语种）
- **推理载体**：`prompt_path`（`prompt.safetensors`，clone 时 load_prompt 用）
- **试听**：`preview_mp3`
- **元信息**：`tags`（逗号分隔文本）、`notes`、`status`（active / replaced——重新固化会把旧资产置 replaced 而不是删）
- **血缘**：**缺失**——"这把声线从哪个候选固化来的"没有字段记录

### 1.3 音频产物（Output）—— 三个入口，现在三张结构

| 入口   | 落点                                                         | 特征                                                 |
| ---- | ---------------------------------------------------------- | -------------------------------------------------- |
| 批量台词 | `batch_items` + `batch_item_versions`                      | 有 position、有版本历史（保留近 3 版）、status=selected 表示"选入成品" |
| 单句保留 | `voice_requests.direct_output_mp3` + `direct_candidate_id` | 把候选试听复制到 `outputs/single_line/`，**寄生在槽位表上**，无版本    |
| 补录单句 | `supplement_lines`                                         | 即录即得，无 position、无版本                                |

三者每行都要记录：归属项目、声线、台词文本、语种、生成状态、成品文件路径、错误信息、时间。导出时 `exports.py:_completed_export_rows` 把三个查询 UNION 起来，拼 `batch:` / `single:` / `supplement:` 前缀的 `order_key`，再套上 `output_order:<pid>` 存的手动排序，重新编号 1..N 生成文件名和 manifest。

另外两条**不落库**的产物路径（保持现状即可，属一次性临时文件）：`outputs/quick/`（快速试听）、`temp/`（工具页转换、standalone 设计）。

### 1.4 系统数据

- `app_settings`：schema_version、全局 `last_project_id`、以及上面那些寄生键
- `activity_log`：操作流水（534 行，只增不减）
- 后台任务状态：**进程内 `BackgroundJob`，不落库**——重启后 running 行被改回 pending，任务历史（何时跑、跑多久、成功几条）不可追溯

---

## 2. 现有设计（schema v4）全貌

### 2.1 表清单与关系

```
projects (1) ──< (N) voice_requests ──< (N) candidates
projects (1) ──< (N) project_voices >── (1) voices
projects (1) ──< (N) batch_items ──< (N) batch_item_versions
projects (1) ──< (N) supplement_lines
projects (1) ──< (N) activity_log
voice_requests (0..1) ──> (1) voices     （voice_id，无 FK 约束！）
voices ──< batch_items / supplement_lines （RESTRICT，但检查逻辑是手写 SQL）
```

删除策略三种混用：`projects` 被删 → CASCADE（槽位/台词/补录/清单/流水）；`voices` 被删 → `project_id` SET NULL；`batch_items.voice_id` / `supplement_lines.voice_id` → RESTRICT（但"还被谁用着"的检查是 `voice_assets.py:346` 一段三重 UNION 手写 SQL 模拟的）。

### 2.2 数据落点分布（问题根源）

| 数据                | DB                     | 磁盘                                                               | KV/JSON                           |
| ----------------- | ---------------------- | ---------------------------------------------------------------- | --------------------------------- |
| 项目基本信息            | ✅ projects             | —                                                                | —                                 |
| 导出目录              | —                      | —                                                                | ⚠️ app_settings 前缀键               |
| 成品手动排序            | —                      | —                                                                | ⚠️ app_settings JSON 数组           |
| 声线资产              | ✅ voices               | reference.wav / preview.mp3 / prompt.safetensors / metadata.json | ⚠️ x_vector_only 只在 metadata.json |
| 候选音频              | ✅ candidates（路径）       | wav + preview                                                    | —                                 |
| 批量/补录/单句产物        | ✅ 三张表（路径）              | outputs/ 下三个子目录                                                  | —                                 |
| 音频元数据（时长/大小/hash） | ⚠️ 仅 voices.audio_hash | —                                                                | —                                 |
| 任务历史              | —                      | —                                                                | —                                 |

---

## 3. 现有设计的问题清单

### P0 — 结构性，日常维护直接受罪

1. **`app_settings` 杂物抽屉化**：`output_order:<pid>`（成品顺序=领域数据）是一整坨 JSON，无法外键约束、无法 JOIN、并发写整键互相覆盖、删了产物行 JSON 里还留着悬空 key（`order_output_rows` 靠 `by_key.pop` 容错）。`batch_export_dir:<pid>` 同理——项目自己的状态却不放在 projects 表。`last_step:<pid>` 属另一类问题：它不是该放哪，而是压根不该持久化（见 1.1），直接废弃。

   现状可以量化：12 行 / 5 类键，其中 6 行 `output_order` 合计 **3051 个字符**、编码了 **66 个有序引用**（西游记 22 个、蜡笔小新 14 个、猪猪侠 11 个、哪吒闹海 9 个、红楼梦 7 个、流浪月亮 3 个）。删除项目时靠 `key LIKE '%:<pid>'`（`projects.py:173`）按字符串前缀扫着擦——这是字符串匹配，不是外键，写错一个 `:` 就擦不干净。`ui.py:1164` 还在写一个库里根本没有行的 `batch_export_excerpt_length:<pid>`，说明这个抽屉还在继续长。
2. **成品三源三结构**：三个入口三张表，导出路每次三查询 UNION + 前缀拼 key；`supplement` 和 `single` 没有版本历史，返修即覆盖旧音频；"是否选入成品"的语义在三处各不相同（`status='selected'` / 有 `direct_output_mp3` / `status='done'`）。
3. **血缘断裂**：`voices` 没有来源候选字段。`voice_requests.voice_id` 只有正向指针且无 FK；重新固化（`_fix_selected_voices_locked`）会 insert 新资产、把旧的置 `replaced`、删 `project_voices` 行——旧资产与候选的对应关系只存在于当时的代码上下文里，事后无法回答"这把声线哪个候选来的"。

### P1 — 完整性与可维护性

1. **关键参数不落库**：`x_vector_only` 只在 `metadata.json`，`generate_with_voice` 每次现读磁盘 JSON 决定 load_prompt 行为；DB 与文件可能不一致，且没有校验机制。
2. **音频元数据缺失**：时长、文件大小、成品 hash 都没落库。导出 manifest、列表展示、体积统计都要现读盘；`temp/` 与 `outputs/quick/` 无清理机制，**孤儿文件无法与 DB 对账**。
3. **枚举无约束**：`status` / `source` / `language` 全是自由 TEXT，没有 CHECK。`language` 在 Python 侧有 `LANGUAGES` 白名单，库里不设防——直接写库或迁移脚本写错值不会报错。
4. **`voice_requests.voice_id` 缺外键**：`delete_voice_asset` 要手写 SQL 把它置回 NULL；删除规则靠代码而非 schema 保证。
5. **`project_voices` 无 position**：清单排序退化成 `created_at DESC`，无法显式调整。
6. **`batch_items.output_wav` 死列**：wav 生成后立即删（`generate_with_voice`），这列永远 NULL。
7. **`activity_log` 无保留策略**：只增不减，`entity_id` 无外键，`detail` 自由文本不可查询。
8. **路径列全部是绝对路径**：`voices.reference_wav` / `preview_mp3` / `prompt_path`、`candidates.wav_path` / `preview_path`、`batch_items.output_mp3`、`supplement_lines.output_mp3` 落库的都是 `C:\Users\...\QwenTTSVoiceoverStudio\...` 全路径（实测 233 条无一例外）。后果：换机器、改 `QWEN_TTS_DATA_DIR`、把 `backup_snapshot` 的副本挪个位置恢复，**整库所有路径当场失效**，且没有修复入口。`file_url()` 每次都要 `Path(path).resolve().relative_to(DATA_DIR)` 把绝对路径再"猜"回相对路径，猜不中就直接返回 `null`（前端音频静默消失）。
9. **生成参数与模型版本不落库**：`ModelManager.clone()` 只传 `text` / `language` / `max_new_tokens` / `non_streaming_mode`，采样参数全走库默认 `do_sample=True, top_k=50, top_p=1.0, temperature=0.9, repetition_penalty=1.05`（`qwen3_tts_model.py:320-328`），既没显式设定、也没记录；`candidates` 只存 `seed`，`voices` 的 17 列里没有任何模型标识，`metadata.json` 也只有 `id/name/source/prompt/language/ref_text/x_vector_only`。后果：换模型版本或调采样参数之后，**无法区分哪些音频是旧参数产的，也无法复现**——`seed` 相同也不再等价（换模型后同一 seed 出的是另一把声音）。

### P2 — 小毛病 / 反模式

1. `idx_settings_prefix ON app_settings(key)`：主键已覆盖等值查询，前缀 LIKE 是反模式。`last_step:<pid>` 废弃后（见 1.1），`app_settings` 只剩等值查询，这条索引可直接删。
2. 时间戳用 ISO 8601 文本：同格式可排序，但用不了 SQLite 日期函数，范围查询慢。数据量小，暂不值得改。
3. 全局单键 `last_project_id`：多人同时用会互相覆盖（架构文档已标注的并发缺陷）——本次不解决，但 schema 上应预留归属字段。
4. `backup_snapshot` 整目录 copytree（projects + voices 全量音频）保留 30 份：磁盘翻倍。属运维策略，DB 侧只需加"资产已备份"标记位即可做增量备份。

---

## 4. 优化方案（目标 schema v5）

### 4.1 设计原则

1. **领域数据进表，KV 表只留真全局配置**——成品顺序、导出目录提升为正式字段或表；纯 UI 偏好（工作台步次）直接不落库，能推断的就不记录。
2. **音频产物统一一张表**，`origin` 区分来源；生成状态与"是否成品"解耦。
3. **血缘完整**：candidate → voice → output 全链路可查。
4. **元数据落库**：每个登记在册的音频都有 hash / 时长 / 大小，可对账、可查重。
5. **约束显式化**：枚举 CHECK、外键补齐、删除策略统一表达。
6. **不动磁盘目录布局**，迁移只改 DB，文件原地不动。

### 4.2 三种归宿：配置文件 / KV 表 / 正式表

「配置放文件不是更简单吗」是个常见反问。判断标准**不是「全局还是局部」**，而是三个问题：谁在改它？它改的时候要不要和别人一起成功？它需不需要被查询、关联、约束？

| 归宿                              | 谁改       | 随什么走                         | 具备的能力                | 本项目现存                                                    |
| ------------------------------- | -------- | ---------------------------- | -------------------- | -------------------------------------------------------- |
| **配置文件**（`.env`、`config.py` 常量） | 部署者，改完重启 | 随**代码**，能进 Git、能 code review | 人能读能改                | 端口、`QWEN_TTS_DATA_DIR`、`QWEN_TTS_MODEL_ROOT`、`LANGUAGES` |
| **KV 表**（`app_settings`）        | 程序在运行时写  | 随**数据**，跟 `app.db` 一起备份      | 能读写，不能查询 / 约束 / JOIN | `schema_version`、`last_project_id`                       |
| **正式表 / 字段**                    | 用户操作触发   | 随**数据**                      | 可 JOIN、可 CHECK、可事务   | v5 的 `output_order` 表、`projects.export_dir`              |

项目**已经有**配置文件通道（`config.py:10` 的 `_load_local_env()` 读 `ROOT/.env`），所以把这三样塞进文件不是「新增一种做法」，而是**把已经在库里的数据搬到一个更差的地方**：


- **`output_order:<pid>`**：用户每拖一次排序就写一次。放文件要自己做原子写 + Windows 文件锁；**没有事务**——删产物和改顺序必须一起成败；无法约束「引用的产物还在不在」；整库备份 / 迁移时要多带一份文件，多一个漏；用户手改会把 JSON 改坏，而 `stored_output_order()` 现在 `except` 一吞就静默退化成默认顺序，坏了也看不出来。
- **`batch_export_dir:<pid>`**：本来就是每个项目一份。搬进配置文件得写成 `[projects.<uuid>].export_dir` —— 那已经是一张表了，只是换了个介质，还丢了 SQL。
- **`last_step:<pid>`**：真正的结论不是「换个地方存」，而是**不该持久化**（见 1.1），由 `infer_last_step()` 按数据进度推断。

唯一「看起来像配置」的 `schema_version` 恰恰**最不能**进文件：它记录的是数据库自身的状态，必须和 DDL 变更在同一个事务里提交。放到文件里一定会分叉成「文件说 5、库里其实 4」，而 `init_schema()` 的自愈逻辑正是靠这一行判断要不要补表。

`last_project_id` 是唯一可以合法搬去配置文件的一项（纯本机 UI 记忆）。留在库里的理由是删项目时要联动改写（`projects.py:116/173/190`），同库同事务更省事；代价是多人共用一个 `DATA_DIR` 时会互相覆盖（已知缺陷，见 P2-3）。

反过来说，**允许进 KV 的只有三类**，v5 之后这张表实际只会用到前两类：

| 允许的类型   | 形态            | 例子                | 为什么必须是库             |
| ------- | ------------- | ----------------- | ------------------- |
| 库自身的元信息 | 单个值           | `schema_version`  | 要与 DDL 变更同事务，文件做不到  |
| 本机单值偏好  | 单个值           | `last_project_id` | 可搬文件，留库更省事          |
| 一次性运维标记 | 单值（时间戳 / 0-1） | 上次备份时间、上次孤儿对账时间   | 跟着备份走才有意义，放文件会与数据脱节 |

准入时可以只记一条经验法则：**KV 表里的 key 一旦出现 `:<id>` 后缀，就说明它其实是一张表**——因为"每个实体一份"已经是关系的形状，只是被压进了字符串。

一句话：**配置文件管「这次怎么部署」，KV 表管「这个库自己的元信息」，正式表管「用户造出来的东西」。** 这三个键都属于第三类。

### 4.3 目标 DDL

```sql
-- ---------- 项目域 ----------
CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','active','archived','deleted')),
    export_dir  TEXT,
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE project_voices (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    voice_id     TEXT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    position     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (project_id, voice_id),
    UNIQUE (project_id, display_name),
    UNIQUE (project_id, position)
);
-- 复合主键的索引只在 project_id 前缀下可用；删声线时 CASCADE 按 voice_id 找子行，
-- 没有这条索引就是全表扫
CREATE INDEX idx_project_voices_voice ON project_voices(voice_id);

-- ---------- 声线域 ----------
CREATE TABLE voices (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    source              TEXT NOT NULL
                        CHECK (source IN ('voice_design','upload')),
    prompt              TEXT,                       -- 音色自然语言描述
    language            TEXT NOT NULL,              -- 试听文本语种
    ref_language        TEXT NOT NULL DEFAULT '',   -- 参考音频语种
    ref_text            TEXT,
    x_vector_only       INTEGER NOT NULL DEFAULT 0, -- 从 metadata.json 提升
    reference_wav       TEXT NOT NULL,
    reference_hash      TEXT,
    reference_ms        INTEGER,                    -- 素材时长
    preview_mp3         TEXT NOT NULL,
    prompt_path         TEXT NOT NULL,
    tags                TEXT NOT NULL DEFAULT '',
    notes               TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','replaced')),
    source_project_id   TEXT REFERENCES projects(id) ON DELETE SET NULL,
    source_candidate_id TEXT REFERENCES candidates(id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_voices_hash ON voices(reference_hash) WHERE status='active';

CREATE TABLE voice_requests (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    prompt          TEXT NOT NULL DEFAULT '',
    audition_text   TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','ready','candidate','selected','single','fixed')),
    candidate_round INTEGER NOT NULL DEFAULT 0,
    voice_id        TEXT REFERENCES voices(id) ON DELETE SET NULL,  -- 补齐外键
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (project_id, name)
);
-- direct_output_mp3 / direct_candidate_id 移除，单句成品进 outputs（origin='single'）

CREATE TABLE candidates (
    id           TEXT PRIMARY KEY,
    request_id   TEXT NOT NULL REFERENCES voice_requests(id) ON DELETE CASCADE,
    seed         INTEGER,
    round        INTEGER NOT NULL DEFAULT 0,
    selected     INTEGER NOT NULL DEFAULT 0,
    shortlisted  INTEGER NOT NULL DEFAULT 0,
    wav_path     TEXT NOT NULL,
    preview_path TEXT NOT NULL,
    params       TEXT,                -- 生成参数 JSON（design_id、模型版本等）
    created_at   TEXT NOT NULL
);

-- ---------- 音频产物域（batch + supplement + single 三源合一） ----------
CREATE TABLE outputs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    voice_id    TEXT REFERENCES voices(id) ON DELETE RESTRICT,  -- 可空：单句保留时尚未固化
    voice_name  TEXT NOT NULL DEFAULT '',   -- 导出快照，防改名漂移
    origin      TEXT NOT NULL CHECK (origin IN ('batch','supplement','single')),
    position    INTEGER NOT NULL DEFAULT 0, -- 项目内自然序
    text        TEXT NOT NULL,
    language    TEXT NOT NULL,
    gen_status  TEXT NOT NULL DEFAULT 'pending'
                CHECK (gen_status IN ('pending','running','done','failed')),
    in_final    INTEGER NOT NULL DEFAULT 0,  -- 是否选入成品（与原 selected/completed 解耦）
    output_mp3  TEXT,
    output_hash TEXT,
    duration_ms INTEGER,
    file_size   INTEGER,
    version     INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_outputs_final  ON outputs(project_id, in_final, position);
CREATE INDEX idx_outputs_status ON outputs(project_id, gen_status);
CREATE INDEX idx_outputs_voice  ON outputs(voice_id);

CREATE TABLE output_versions (
    id          TEXT PRIMARY KEY,
    output_id   TEXT NOT NULL REFERENCES outputs(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    output_mp3  TEXT NOT NULL,
    output_hash TEXT,
    text        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (output_id, version)
);

CREATE TABLE output_order (          -- 替代 output_order:<pid> JSON
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    output_id  TEXT NOT NULL REFERENCES outputs(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    PRIMARY KEY (project_id, output_id),
    UNIQUE (project_id, position)
);

-- ---------- 系统域 ----------
CREATE TABLE jobs (                  -- 后台任务历史（可选，建议做）
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('candidate','batch','regen','supplement')),
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    status     TEXT NOT NULL CHECK (status IN ('running','done','failed','cancelled')),
    total      INTEGER NOT NULL DEFAULT 0,
    succeeded  INTEGER NOT NULL DEFAULT 0,
    failed     INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_jobs_project ON jobs(project_id, started_at DESC);

CREATE TABLE activity_log (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL CHECK (entity_type IN
                ('project','voice','voice_request','candidate','batch','supplement','output','job')),
    entity_id   TEXT,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_activity_project ON activity_log(project_id, created_at DESC);

CREATE TABLE app_settings (          -- 瘦身：只留真全局 KV
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 4.4 关键设计决策说明

| 决策                             | 理由                                                                                                                                                                                                                  |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `outputs` 合并三源，`origin` 区分     | 导出、排序、版本、元数据对三类产物是同构需求；合并后成品页一个查询替代三路 UNION                                                                                                                                                                         |
| `gen_status` 与 `in_final` 拆开   | 原来 `batch_items.status='selected'` 一个字段兼两职（生成完了 + 选入成品），`selected → completed` 的退出口本质是展示标记。拆开后 single/supplement 天然统一                                                                                               |
| `voice_name` 快照列               | 导出文件名必须稳定（"finished folder 和 zip 字节级一致"是现有承诺）；显示名会改，快照防漂移                                                                                                                                                           |
| `source_candidate_id`          | 补血缘：固化声线 ↔ 来源候选，回答"这把声音哪个候选来的"                                                                                                                                                                                      |
| `x_vector_only` 落库             | `generate_with_voice` 不再依赖磁盘 metadata.json；DB 成为唯一真相源，metadata.json 降级为导出用的人读镜像                                                                                                                                     |
| `reference_hash` 部分唯一索引        | 现有"相同参考音频去重"逻辑（`archive_uploaded_voice`）从应用层查询升级为库级约束                                                                                                                                                               |
| `output_order` 表替代 JSON        | 可外键、可 JOIN、可并发安全；删产物行级联清理，不再有悬空 key                                                                                                                                                                                 |
| `jobs` 表                       | 任务历史落库，重启后可追溯；与进程内 BackgroundJob 并存（运行时态仍在内存，落库只记开始/结束/计数）。**任务级事件（批量开始/完成、成功 N 条）归 `jobs`，不再写 `activity_log`**                                                                                                                           |
| 时间戳保持 ISO 文本                   | 兼容现有 500+ 行存量数据；数据量小，日期函数需求少，不值得为此迁移                                                                                                                                                                                |
| 路径建议存相对 `DATA_DIR` 的形态         | **待确认项**。存量 233 条全绝对路径，换机/换目录即全失效。倾向改成 `voices/<vid>/reference.wav` 这种 POSIX 相对路径，读取时统一 `DATA_DIR / rel` 拼回；迁移脚本按"若以 DATA_DIR 开头则截断"批量改写，非前缀的保留原值并打日志。代价：所有读路径的地方要过一层 `resolve_path()`，漏一处就是线上 404                  |
| 产物记录 `model_id` + `gen_params` | **待确认项**。每个产物记下生成它的模型标识（模型目录名 + `model.safetensors` 大小或 hash）和采样参数（`temperature` / `top_p` / `top_k` / `max_new_tokens`），与 `seed` 一起构成"可复现四元组"。当前完全缺失，换模型或调参后老产物不可辨识。代价：所有生成路径要多传一层上下文；`metadata.json` 这个"人读镜像"也要同步 |
| `output_versions` 保留策略        | 沿袭现有 `VERSIONS_KEPT=3`（每行只留最近 3 版，更旧的连同文件删）。三源合并后，**补录与单句也首次获得版本历史**——此前它们"返修即覆盖"，现在都能"留一版、再生成一版对比"                                                                                                                          |
| `activity_log` 只留关键动作         | 已定。与 `jobs` 分工：任务级事件归 `jobs`，`activity_log` 只记**关键动作（固化 / 删除 / 导出）**；加清理上限（每项目最近 N 条）。定位为审计 / 排障——前端暂不为其做展示页，出问题回溯"这版声线何时从哪个候选固化"时它是唯一线索                                                     |

---

## 5. 迁移策略（v4 → v5）

**工具选型：暂不引入第三方迁移框架。** 本项目是裸 `sqlite3`（无 SQLAlchemy/ORM），迁移只有这一次大改（v4→v5），沿用现有 `initialize()` 的自愈机制 + 一个幂等的手写迁移脚本最省事；将来若迁移频繁化、复杂化，再上 Alembic（`render_as_batch=True`）或轻量的 yoyo-migrations。SQLite 重建表时注意先 `PRAGMA foreign_keys=OFF`、`BEGIN` 包裹、结束 `PRAGMA foreign_key_check` 校验。

沿用现有 `backup_snapshot("pre-migration")` 机制，迁移脚本幂等可重入：

1. **建新表**（`outputs` / `output_versions` / `output_order` / `jobs`；`voices` 等老表用 `ALTER TABLE ADD COLUMN` 补新列，SQLite 不支持改约束，CHECK 约束通过新表 + 数据搬迁实现，或接受老表无 CHECK、仅新表有）。
2. **搬数据**：
   - `batch_items` → `outputs`（`origin='batch'`，`in_final = (status='selected')`，`gen_status` 由 status 映射：selected/completed→done，failed→failed，其余→pending；`voice_name` 取当时 JOIN 结果显示名）
   - `supplement_lines` → `outputs`（`origin='supplement'`，`in_final = (status='done')`）
   - `voice_requests` 有 `direct_output_mp3` 的行 → `outputs`（`origin='single'`，`text=audition_text`，`voice_id` 取槽位 `voice_id`（可空），`voice_name` 取槽位 `name`）
   - `batch_item_versions` → `output_versions`（按 `item_id` 找到对应 output 行）
   - `output_order:<pid>` JSON → `output_order` 表（解析 `batch:`/`single:`/`supplement:` 前缀回查 id；解析失败的 key 记录到日志并跳过，不阻断迁移）
   - `batch_export_dir:<pid>` → `projects.export_dir`，搬完删除该键
   - `last_step:<pid>` **不迁移，直接删除**（步次改由 `infer_last_step` 推断，见 1.1）
   - 各 `voices` 行读旁边 `metadata.json` 回填 `x_vector_only`
   - `voices.audio_hash` → `reference_hash`，`project_id` → `source_project_id`（保持数据不动，仅列语义更名）
3. **兼容期**：迁移后保留 `batch_items` / `supplement_lines` 为**视图**（`CREATE VIEW batch_items AS SELECT ... FROM outputs WHERE origin='batch'`），旧 API / Gradio 页面渐变迁移；前端改完再删视图。
4. **元数据补录**：启动时对账任务扫描 `output_mp3` 指向的文件，回填 `duration_ms` / `file_size` / `output_hash` 的 NULL 值；同时输出"DB 有记录但文件丢失"和"文件在但 DB 无记录"两份差异清单（后者主要是 `temp/` 与 `outputs/quick/`，属预期）。
5. **验证**（项目已有办法）：副本库上比对迁移前后各表行数、抽样 diff 成品清单与排序、二次执行迁移脚本验证幂等。


## 7. 分阶段落地建议

| 阶段    | 内容                                                                                                                                 | 风险           |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| 一     | `projects` 加 `export_dir` 并回搬 KV 键；删除 `last_step:<pid>` 键与对应接口；删 `idx_settings_prefix`；`batch_items` 删 `output_wav` 死列             | 极低，纯增量       |
| 二     | `voices` 加 `x_vector_only` / `source_candidate_id`，从 metadata.json 回填；`voice_requests.voice_id` 补 FK；`project_voices` 加 `position` | 低            |
| 三     | 建 `outputs` / `output_versions` / `output_order` 三张表 + 数据搬迁 + 视图兼容；`exports.py` 改读新表；前端成品页渐变切换                                     | 中，需充分测试导出等价性 |
| 四     | 元数据补录对账任务（hash/size/duration）、孤儿文件清单                                                                                               | 低            |
| 五（可选） | `jobs` 表接线；`activity_log` 保留策略；删旧表与视图                                                                                              | 低            |

---

## 附：字段级对照表（旧 → 新）

| 旧                                                          | 新                                               | 说明                              |
| ---------------------------------------------------------- | ----------------------------------------------- | ------------------------------- |
| `app_settings: output_order:<pid>`（JSON）                   | `output_order` 表                                | 领域数据                            |
| `app_settings: last_step:<pid>`                            | 直接删除                                            | UI 瞬时状态，改由 `infer_last_step` 推断 |
| `app_settings: batch_export_dir:<pid>`                     | `projects.export_dir`                           | 项目状态                            |
| `batch_items`                                              | `outputs`（origin='batch'）                       | 三源合一                            |
| `supplement_lines`                                         | `outputs`（origin='supplement'）                  | 三源合一                            |
| `voice_requests.direct_output_mp3` + `direct_candidate_id` | `outputs`（origin='single'）                      | 摘掉寄生字段                          |
| `batch_item_versions`                                      | `output_versions`                               | 泛化到所有来源                         |
| `voices.audio_hash`                                        | `voices.reference_hash`                         | 更名 + 部分唯一索引                     |
| `voices.project_id`                                        | `voices.source_project_id`                      | 明确"来源项目"语义                      |
| `metadata.json: x_vector_only`                             | `voices.x_vector_only`                          | 落库，DB 成唯一真相源                    |
| （无）                                                        | `voices.source_candidate_id`                    | 新增血缘                            |
| （无）                                                        | `outputs.output_hash / duration_ms / file_size` | 新增元数据                           |
| （无）                                                        | `jobs`                                          | 新增任务历史                          |
| `batch_items.output_wav`                                   | 删除                                              | 死列                              |
