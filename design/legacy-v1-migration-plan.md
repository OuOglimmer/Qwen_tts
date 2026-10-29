# 旧版（Gradio 单体 / schema v1）→ 新版（FastAPI + Next / schema v6）数据迁移方案

> 结论先行：**不用写数据转换器**。新版代码里已经有一条 `v1 → v6` 的官方迁移链
> （`initialize()` 自愈 + `migrate_v5.py` + `migrate_v6.py`），旧库是它的合法起点。
> 本方案要做的是**把链跑对**：顺序、前置断言、文件路径重基、声线 prompt 兼容性、验收与回滚。
>
> 全部结论已在临时目录用「按旧版 DDL 造的 v1 样例库」实跑验证（见第 7 节），不是纸上推演。

## 0.1 真实旧库定位（2026-10-29 实测）

**旧数据目录就在本机 `D:\asset`**（`app.db` + `voices/` + `projects/` + `outputs/` + `models/`），
schema_version=1，是合法的 v1 库。实测资产规模：

| 表 | 行数 | 说明 |
|---|---|---|
| projects | 9 | 「默认项目 / 伟大建党精神 / 星月远航2050 / 重返地球 / 温室花园…」全为 `draft` |
| voices | 30 | 27 active + 3 replaced；source: upload 7 + voice_design 23 |
| voice_requests | 42 | draft 4 / candidate 2 / selected 16 / fixed 20；其中 29 条有单句成品 |
| candidates | 477 | 36 条 selected、41 条 shortlisted |
| batch_items | 459 | **全部 `completed`**，459 条都有 output_mp3 |
| activity_log | 738 | — |
| app_settings | 16 | — |

**三个关键实测结论（直接决定迁移方案）：**

1. **路径前缀变了**：库里路径写的是 `D:\QwenTTS\data\...`，但文件实际在 `D:\asset\...`（旧
   `D:\QwenTTS\data` 被改名/移动成 `D:\asset`）。重映射后命中率：voices 29/30、candidates
   477/477、batch_items 459/459、单句 29/29。**唯一坏数据是 1 条声线 `qa-voice`（status=replaced，
   `reference_wav='x.wav'` / `prompt_path='x.safetensors'`，占位假值）**，磁盘上无此声线目录。

2. **声线 prompt 零重建**：磁盘 31 条声线的 `ref_spk_embedding` 维度**全是 2048**，与当前
   `voice_clone_1.7b` 的 `enc_dim=2048` 完全一致。旧机用的就是 1.7B，**prompt 直接可用**。

3. **磁盘有大量未引用文件**：projects/ 下 1300 个 mp3，但库只引用 488 个唯一文件名（459 batch
   + 29 单句），**多出 812 个 mp3 是库没引用的历史/废弃版本**；另有 485 个 wav（477 候选 + 8 额外）、
   2 个磁盘 voices 目录（`2fdc1fe8-*`、`91481648-*`）库里没有。这些是「孤儿资产」，迁移时不进新库、
   但**绝不删除**，保留在旧目录或另存副本。

> 由于数据从旧库到新库需要 v1→v6 迁移链，而本方案第 7 节已用样例库验证链可用，
> 对 `D:\asset` 只需把「样例库」换成「真实库」重复一遍，加上上述路径重映射。

---

## 1. 两代系统的结构差异

### 1.1 旧版（`D:\QwenTTS_Packages\QwenTTS_Part1_Code.tar`）

Gradio 单体应用，**8 个 py 文件，4530 行**，职责全压在两个大文件里：

| 文件 | 行数 | 职责 |
|---|---|---|
| `studio/ui.py` | 2415 | 全部界面（Gradio Blocks）+ 事件绑定，端口 7870 |
| `studio/services.py` | 1415 | 全部业务逻辑（项目 / 声线 / 候选 / 批量 / 导出） |
| `studio/database.py` | 272 | **schema v1**，7 张表 + 自愈加列 |
| `studio/model_manager.py` | 230 | 双模型加载、`create_prompt`、`gpu.lock` |
| `studio/config.py` | 76 | `DATA_DIR` 解析（与本版**逐字相同**） |
| `studio/audio.py` | 78 | 音频读写 / 转码 |
| `studio/project_context.py` | 13 | 当前项目态 |
| `studio/__main__.py` | 27 | `app.launch(port=7870)` |

**没有 API 层、没有前端工程、没有 Node**。所以迁移只涉及「磁盘数据目录」，
不涉及代码搬迁 —— 新版已经把这套能力重写成 FastAPI(7871) + Next(3000)。

### 1.2 新版（当前仓库）

| 层 | 位置 | 说明 |
|---|---|---|
| API | `studio/api.py` | 75 个端点，FastAPI |
| 领域 | `studio/service.py` / `voice_workflow.py` / `takes.py` / `batch.py` / `exports.py` | 按域拆分 |
| 存储 | `studio/database.py` | **schema v6**，`SCHEMA_V5_DDL` + `SCHEMA_V6_DDL` + `FRESH_V6_DDL` |
| 迁移 | `scripts/migrate_v5.py` / `migrate_v6.py` | 幂等、单事务、`foreign_key_check` 收尾 |
| 前端 | `frontend/` | Next 14 + React 18 |

### 1.3 表清单对照

| schema v1（旧） | schema v6（新） | 关系 |
|---|---|---|
| `projects` | `projects` | 加 `notes` / `export_dir` / `status` CHECK |
| `voice_requests` | `voice_requests` | 加 `candidate_round`、`voice_id` 外键；摘掉 `direct_output_mp3` / `direct_candidate_id` |
| `candidates` | `candidates` | 加 `round` / `params` |
| `voices` | `voices` | `project_id`→`source_project_id`；`audio_hash`→`reference_hash`；加 `x_vector_only` / `reference_ms` / `source_candidate_id` |
| `batch_items` | **`outputs`**(origin=batch) | 三源合一后表被 DROP，同名**视图**接管读取 |
| （`direct_output_mp3` 字段） | **`outputs`**(origin=single) | 单句成品从"字段"升级成"行" |
| `batch_item_versions` | `output_versions` | 直搬 |
| `activity_log` | `activity_log` | 原样 |
| `app_settings` | `app_settings` | `batch_export_dir:*`→`projects.export_dir`；`output_order:*`→`output_order` 表；`last_step:*` 删除 |
| — | **`project_voices`** | 新增：项目清单（声线多对多 + 版本状态机） |
| — | **`output_order`** | 新增：人工排序 |
| — | **`jobs`** | 新增：任务级流水 |
| — | **`tag_groups` / `tags` / `voice_tags` / `project_voice_tags` / `request_tags`** | 新增：标签四表，取代 `voices.tags` 逗号串 |

---

## 2. 数据模型映射（逐列）

### 2.1 `projects`
`id / name / status / created_at / updated_at` → 同名同义直搬。
`notes` 默认 `''`；`export_dir` 由 `app_settings['batch_export_dir:<pid>']` 回填。
`status` 旧值只有 `draft`，落在新 CHECK `(draft,active,archived,deleted)` 内。

### 2.2 `voice_requests`（声线槽位）
`id / project_id / position / name / prompt / audition_text / language / created_at` 直搬。

`status` 值域**已核对**：旧代码实际写入 `draft / ready / candidate / single / selected / fixed`
（`services.py` 的 6 处 `SET status=`），与新 CHECK **完全一致**，不需要映射。

`voice_id` 保留，v6 补 `ON DELETE SET NULL` 外键（悬空 id 会被置 NULL 并计数）。
`candidate_round` 新增默认 0。
`direct_output_mp3` → `outputs(origin='single', in_final=1)`，随后该列被 DROP。
`direct_candidate_id` 直接丢弃（信息损失见 §6.2）。

### 2.3 `candidates`
`id / request_id / seed / wav_path / preview_path / selected / shortlisted / created_at` 直搬；
`round` 默认 0，`params` 为 NULL。

### 2.4 `voices`（声线资产）
| 旧列 | 新列 | 处理 |
|---|---|---|
| `project_id` | `source_project_id` | 改名（语义：来源项目，不是归属） |
| `audio_hash` | `reference_hash` | 改名；旧库无此列时，自愈阶段按 `reference.wav` 现算 md5 |
| `status` | `status` | `active` / `replaced` 直搬，值域一致 |
| `tags` | `voice_tags` 关系行 | 逗号串（含中文逗号）拆词，全部落「未分类」组；原串保留为派生缓存 |
| `notes` / `prompt` / `language` / `ref_text` / `reference_wav` / `preview_mp3` / `prompt_path` | 同名 | 直搬 |
| — | `ref_language` | 默认 `''`（旧库无此概念，**不回填**：拿 `language` 冒充等于造伪数据） |
| — | `x_vector_only` | 读 `voices/<id>/metadata.json` 回填 |
| — | `reference_ms` / `source_candidate_id` | NULL（旧库无对应） |

### 2.5 `batch_items` → `outputs`
| 旧 | 新 | 规则 |
|---|---|---|
| `status='selected'` | `gen_status='done'`, `in_final=1` | 已入成品库 |
| `status='completed'` | `gen_status='done'`, `in_final=0` | 已生成未选定 |
| `status='failed'` | `gen_status='failed'` | 带 `error` |
| `status IN ('pending','running')` | `gen_status='pending'` | 中断的 `running` 复位为待生成 |
| `position` | `position` | 迁移后按 `batch → single → supplement` 重排为项目内 1..N |
| `version` | `version` | 自愈阶段旧库无此列时：`output_mp3` 非空置 1，并写一行 `batch_item_versions` |
| `error` / `output_mp3` / `text` / `language` / `project_id` / `voice_id` / 时间 | 同名 | 直搬 |
| `voice_name` | `voice_name` | 取 `project_voices.display_name`，回退 `voices.name` |
| `output_wav` | — | **丢弃**（见 §6.2 孤儿文件报告） |

### 2.6 新增的关联结构怎么来
- `project_voices`：自愈阶段由 `_backfill_voice_globalization()` 生成 —— 只给 `status='active'`
  且来源项目存在的声线建 `active` 行（演练中 3 条声线 → 2 行）；
  剩下的（`replaced` 资产被成品引用）由 v6 的 `repair_roster_links()` 补成 `state='replaced'` 行，
  并把引用它们的成品标 `roster_state='replaced'`（演练中补 1 行、标 1 条）。
- `output_order`：从 `app_settings['output_order:<pid>']` 的 `["batch:<id>","single:<id>"]` 还原，
  解析不出来的进日志跳过（不猜）。
- 标签四表：`seed_builtin_tags()` 建 7 组 27 词条（确定性 uuid5，幂等）+ `backfill_voice_tag_rows()` 搬旧串。

---

## 3. 关键语义差异（迁移时必须做对的 6 件事）

1. **三个"版本"不是一回事**：`candidates`（候选）/ `project_voices.state`（清单版本 active|replaced）/
   `outputs.version`（同句重录次数）。旧库只有前两个的雏形，**不要**把 `voices.status='replaced'`
   误当成品版本。
2. **`in_final` 才是成品库**。旧库靠 `batch_items.status='selected'` 表达，映射错一位就会出现
   「导出的成品少一半」或「草稿混进交付」。
3. **单句成品从字段变行**：`origin='single'` 的 outputs 与槽位状态 `voice_requests.status='single'`
   是**两回事**，新库允许同一个槽位同时是 `single` 与 `fixed`（非独占）。旧库语义相同（`next_status`
   只在不涉及 fixed 时才改），直搬即可。
4. **标签不猜语义**：旧串是自由文本，全部进「voice/未分类」组；不做"低沉→音色特征"这类智能归类。
5. **`replaced_by` 只在同名 active 行存在时推断**。旧库换版若改过名，血缘会留 NULL，
   UI 显示「已换版」但点不到新版 —— 这是旧库本身信息缺失，**方案选择报告而不编造**。
6. **路径全部是绝对路径**，跨机器必须重基（§4），这一步漏了表现是"列表有行、播放没声"。

---

## 4. 文件资产：路径重基（跨主机唯一的硬风险）

旧库把绝对路径写进库，共 **8 列**：

| 表 | 列 | 旧库存在性 |
|---|---|---|
| `voices` | `reference_wav`, `preview_mp3`, `prompt_path` | 有 |
| `candidates` | `wav_path`, `preview_path` | 有 |
| `batch_items` | `output_mp3`（迁移后是 `outputs.output_mp3`） | 有 |
| `batch_item_versions` | `output_mp3`（→`output_versions`） | 有 |
| `projects` | `export_dir`（目录，不是文件） | 由 settings 回填 |

### 4.1 磁盘布局两代一致
```
<DATA_DIR>/voices/<voice_id>/reference.wav | preview.mp3 | prompt.safetensors | metadata.json
<DATA_DIR>/projects/<project_id>/candidates/<request_id>/...
<DATA_DIR>/projects/<project_id>/outputs/...
<DATA_DIR>/projects/<project_id>/outputs/single_line/...
```
所以**整目录拷贝 + 前缀重写**即可，不需要重排文件名。

### 4.2 重基算法（不靠字符串盲替换）
对每个路径值 `p`：
1. `p` 在当前机器上存在 → 不动。
2. 否则取尾段：在 `p` 中找最后一个 `/voices/` 或 `/projects/` 分隔点，
   取其后半段 `tail`，拼成 `<NEW_DATA_DIR>/voices/<tail>` 或 `<NEW_DATA_DIR>/projects/<tail>`。
3. 拼出来的文件存在 → 写回该路径。
4. 仍不存在 → **不改**，记入「缺失资产」报告（文件名 + 原路径 + 所属记录）。

这条规则对 `D:\QwenTTS\data\...`、`C:\Users\olduser\AppData\Local\...`、
盘符变化、用户名变化都成立，且永远不会把路径改到工作区之外。

### 4.3 必须在 `migrate_v5` 之前做
因为 `migrate_v5` 有两处要**读文件**：
- `metadata.json` 回填 `x_vector_only`；
- `outputs` 补录 `file_size` / `output_hash`。

路径没重基时这两步会静默跳过（留 NULL），不报错 —— 属于"看着成功其实没做到"的哑火。

### 4.4 拷贝纪律
- 旧服务**必须先停**（`gpu.lock` + WAL）。
- `app.db` 必须连同 `app.db-wal`、`app.db-shm` 一起拷；只拷 `app.db` 会丢最后一段未 checkpoint 的事务。
- 用 `robocopy /E /COPYALL`（保留时间戳，`created_at` 排序依赖它）或 `sqlite3 .backup`。

---

## 5. 声线 prompt 兼容性（模型维度，与库结构无关）

`prompt.safetensors` 里是 `ref_spk_embedding`（说话人向量）+ `ref_code`（12Hz codec token）。
`ref_code` 跨尺寸通用，但 `ref_spk_embedding` 的维度由 **speaker encoder** 决定：

| 模型 | `speaker_encoder_config.enc_dim` | `talker.hidden_size` |
|---|---|---|
| `voice_clone_0.6b` | **1024** | 1024 |
| `voice_clone_1.7b` | **2048** | 2048 |

判断规则：
- 旧机用 1.7B 固化 → prompt 直接可用（0 工作量）。
- 旧机用 0.6B 固化 → **prompt 在 1.7B 上必然形状不匹配**，必须重建。

重建路径：`ModelManager.create_prompt(reference_wav, ref_text, x_vector_only)`
→ `save_prompt()` 覆盖 `voices/<id>/prompt.safetensors`。
前提：`ref_text` 非空（`x_vector_only=1` 的声线不需要），`reference.wav` 存在。
需要 GPU（`gpu.lock` 互斥，**必须在停服状态下做**），单条 3~5 秒。

探测方式（纯 CPU，读 safetensors header 8 字节长度 + JSON，不解码张量）：
对每条声线读 `ref_spk_embedding` 的 shape[0]，与目标模型的 `enc_dim` 比对，输出三色清单：
`可直接用` / `需重建且素材齐备` / `需重建但缺 ref_text 或 wav`。

---

## 6. 信息保全边界（"不破坏信息"的准确含义）

### 6.1 完整保留
项目、声线槽位（含 prompt / 试听文本 / 语种 / 位置 / 状态）、候选（含 seed、选中与入围标记）、
批量台词（文本 / 语种 / 状态 / 错误 / 重录版本）、单句成品、音视频文件、标签串、活动日志、导出目录设置。

### 6.2 已知的、需你确认的三处损失
1. **`batch_items.output_wav`**：新模型只有 mp3，"中间 wav"不再被引用。文件仍在磁盘上，
   会变成孤儿。方案会输出孤儿清单，可选择保留或清理。
2. **`voice_requests.direct_candidate_id`**：单句成品→候选的血缘。新模型单句成品是独立行，
   不再指向候选（`source_candidate_id` 只用于"固化来源"）。不影响播放与交付。
3. **`replaced_by` 血缘**：旧库若不记换版关系，只能靠"同名 active 行"推断，推不出就留空。

### 6.3 明确不做
- 不修改旧库（全程在副本上做，旧目录原样保留）。
- 不删旧目录、不删 `backups/`。
- 不迁移 `temp/`（临时产物，`cleanup.py` 按 mtime 回收）。
- 不猜状态值域：遇到 CHECK 之外的值，中止并列清单，由人决定映射。

---

## 7. 迁移流水线（9 步，可回滚）

> 演练实测：按旧版 DDL 造的 v1 样例库（2 项目 / 3 声线 / 5 槽位 / 3 候选 / 6 条台词 /
> 1 条单句成品 / 5 个 settings 键），跑完 3→4→5 后：`outputs=7`（6 batch + 1 single）、
> `output_versions=3`、`project_voices=3`（补 1 行 replaced）、`voice_tags=4`、
> `foreign_key_check` 空、文件缺失 0，且新后端 `/api/projects`、`/api/projects/{id}/*`
> 全部 200。**结论：链可用，v1 是合法起点。**

| 步 | 动作 | 判据 / 产物 |
|---|---|---|
| 0 | 停旧服务；`robocopy` 快照整个旧 `DATA_DIR` | 快照目录 + 记录 `app.db` 大小与 sha256 |
| 1 | 定位旧 `DATA_DIR`（找 `app.db` + `voices/` + `projects/` 同级），整目录拷到新机 | 新旧文件计数一致（`voices/` 子目录数、`projects/` 子目录数） |
| 2 | **前置断言**（只读）：状态值域、悬空外键、重复名称、缺失文件 | 全部通过才继续；不通过输出清单并中止 |
| 3 | **路径重基**（`--dry-run` 先出报告） | 文件存在率 100%，或缺失清单经确认 |
| 4 | 新代码**首次启动一次**（只自愈，不跑迁移） | `schema_version` 变 5；`project_voices` 出现；`outputs` 表已建但仍为 0 行 |
| 5 | `python scripts/migrate_v5.py --db <副本>/app.db` | 日志出现 `batch_items -> outputs: N 行`，`foreign_key_check 通过` |
| 6 | `python scripts/migrate_v6.py --db <副本>/app.db` | `剩余断链 0 条`，`foreign_key_check 通过` |
| 7 | **资产体检**：行数守恒表 + 文件存在率 + prompt 兼容探测 | 见 §8 验收清单 |
| 8 | API 冒烟（临时端口 7872 指向副本），核对 `slot_count / voice_count / task_count` | 与旧库计数一致 → 改 `.env` 指向并重启正式服 |

**顺序不可颠倒**：`migrate_v5.py` 第 2 步会 `ALTER TABLE project_voices ADD COLUMN position`，
而 `project_voices` 是**第 4 步首次启动时**才被创建的。先跑 v5 会直接报 `no such table`。
反过来，第 4 步的启动只会把版本号记成 5，不搬 `batch_items` 的数据（代码里明确注释了不做越级自愈），
所以不会"跳过" v5。

---

## 8. 验收清单（可断言的 9 条）

1. `projects` 行数：迁移后 == 迁移前。
2. `voices` 行数：== 迁移前（含 `replaced`）。
3. `voice_requests` 行数：== 迁移前。
4. `candidates` 行数：== 迁移前。
5. `outputs` 行数 == 旧 `batch_items` 行数 + 旧 `direct_output_mp3` 非空行数。
6. `outputs` 中 `in_final=1` 行数 == 旧 `status='selected'` 行数 + 单句成品数。
7. `output_versions` 行数 == 旧 `batch_items` 中 `output_mp3` 非空的行数。
8. 8 个路径列指向的文件存在率 100%（`projects.export_dir` 除外，允许不存在）。
9. `PRAGMA foreign_key_check` 为空；`/api/projects` 的 `task_count` 与旧库逐项目对齐。

异常态判据（看到就该停）：
- migrate_v5 日志里 `文件缺失 N 行` 的 N > 0 → 路径重基没做对。
- migrate_v6 抛 `仍有 N 条成品找不到清单归属` → 有 `outputs.voice_id` 指向不存在的声线，需先清悬空。
- `project_voices` 出现同名两条 `active` → 部分唯一索引会挡，说明旧库同项目重名，需先改名。

---

## 9. 风险与对策

| 风险 | 后果 | 对策 |
|---|---|---|
| 顺序颠倒（先 v5 后启动） | `no such table: project_voices`，事务回滚，不损坏 | 固化流水线；每步前 `backup_snapshot` |
| 只拷 `app.db` 不拷 `-wal` | 丢最后一批事务 | 停服后整目录 `robocopy`，或 `VACUUM INTO` |
| 路径未重基 | 音频全哑、"文件缺失 N 行" | 步骤 3 的 dry-run 报告，存在率必须是 100% |
| 0.6B 时代 prompt 上 1.7B | 生成时报张量形状不匹配 | 步骤 7 探测 + 重建（需 GPU，3~5s/条） |
| 旧库状态值越界 | v6 重建时 CHECK 失败 | 步骤 2 前置断言，列清单人工定映射 |
| 同项目重名声线 | 部分唯一索引建不上 | 步骤 2 检出，重命名规则 `名字_2` |
| 旧库被 `-wal` 锁着拷 | 拷出半截库 | 拷贝前确认无进程占用；拷后 `PRAGMA integrity_check` |

回滚：`.env` 的 `QWEN_TTS_DATA_DIR` 指回旧目录 + 用旧包（`D:\QwenTTS_Packages`）起旧版本即可，
旧目录从头到尾未被写入。

---

## 10. 需要你确认的 4 件事

1. **旧数据目录在本机哪个位置？** 判据：一个目录，里面有 `app.db` + `voices/` + `projects/`。
   旧版 `config.py` 的查找顺序是 ①`QWEN_TTS_DATA_DIR` ②`<安装目录>/data/app.db` 存在则用它
   ③`%LOCALAPPDATA%\QwenTTSVoiceoverStudio`。**如果你手上只有 `D:\QwenTTS_Packages`，那里面没有数据**
   —— 我核对过 `Part1_Code.tar` 的 52 个条目，只有代码，没有 `data/`、没有 `app.db`、没有 mp3。
2. **旧机用的是 0.6B 还是 1.7B？** 决定 prompt 要不要重建（决定是否要占 GPU 跑一遍）。
3. **迁到哪儿？** 独立新 `DATA_DIR`（推荐，与当前空库隔离）还是并入当前
   `%LOCALAPPDATA%\QwenTTSVoiceoverStudio`（同名项目会撞 `projects.name UNIQUE`）。
4. **孤儿 wav 与 `direct_candidate_id` 血缘**：接受丢弃（默认）还是保留一份清单/副本。

确认后我按流水线落地 `scripts/migrate_legacy_data.py`（含 `--dry-run`、前置断言、路径重基、
行数守恒报告、prompt 探测），先在副本上给你出报告，你点头再动真实目录。
