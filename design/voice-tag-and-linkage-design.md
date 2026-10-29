# 项目 / 声线 / 成品：关联闭环与标签体系设计（v6 草案）

> 依据：真实库副本实测（`schema_version=5`，11 表 / 3 兼容视图 / 10 索引），
> 逐行核对了 `voices 45 / project_voices 14 / voice_requests 29 / candidates 78 / outputs 101 / output_versions 110`。
> 本文只给方案，**不动代码**。§5 是分阶段落地顺序，待你确认后按阶段执行。

---

## 1. 体检结论

### 1.1 结构现状与设计稿的偏差

| 项 | 设计稿（`database-design-final.md`） | 实际库 | 影响 |
|---|---|---|---|
| `projects.notes` | 有 | **缺列** | 项目备注无处落 |
| `projects.status` | CHECK ∈ (draft/active/archived/deleted) | **无 CHECK**，实测全为 `draft` | 枚举形同虚设；`archived` 从未使用 |
| `voice_requests.voice_id` | 外键 `ON DELETE SET NULL` | **无外键** | 声线被物理删除后槽位留悬空 id |
| `project_voices.position` | `NOT NULL` | **可空** | 唯一索引 `(project_id, position)` 允许多个 NULL，新插入的行排序漂移 |
| `voices.source_candidate_id` | 血缘字段 | 有列，**45/45 全 NULL** | "这条声线从哪个候选来的"无法回答 |
| `outputs` ↔ `project_voices` | 成品经清单绑定 | **成品直接挂 `voices`** | 项目与成品的声线归属无一致性约束 |

已正确落地、无需再动的部分：三源产物合一 `outputs`（`origin` 区分）、`gen_status` 与 `in_final` 解耦、
`output_versions` 三源共享、`output_order` 表替代 JSON 键、`app_settings` 已瘦身到 2 个键、
`idx_voices_hash`（active 部分唯一索引）、`foreign_key_check` 干净（0 违规）。

### 1.2 四类关联缺口（全部实测复现）

**缺口 A：成品 ↔ 项目清单断链（25 条）**

`猪猪侠 11 条 + 西游记 14 条` 的 `outputs.voice_id` 指向的声线**不在本项目清单里**。
根因在 `voice_workflow._fix_selected_voices_locked()`：

```python
self.db.execute("UPDATE voices SET status='replaced' WHERE id=?", (row["voice_id"],))
self.db.execute("DELETE FROM project_voices WHERE project_id=? AND voice_id=?", (project_id, row["voice_id"]))
```

重新固化时把旧资产的清单行**删掉**了，但历史成品的 `voice_id` 仍指向那批 `replaced` 资产。
后果连锁：

1. `_ITEM_SELECT` 的 `COALESCE(r.display_name, v.name)` 退化成 `v.name`，旧成品显示原名（与新资产同名，界面上无法区分先后）；
2. 「按人物重新生成」走 `assets.project_voice(voice_id, project_id)` 校验（要求 `v.status='active'` 且在清单），
   这 25 条**永远无法重生成**，报"请选择当前项目中的有效声线"；
3. 这批 `replaced` 资产在「全部声线」页不可见（`library_voices` 只查 `status='active'`），
   却因 `outputs.voice_id` 是 `ON DELETE RESTRICT` 而删不掉——成了既看不见也清不掉的僵尸资产。

**缺口 B：血缘链路断开（45/45）**

`candidates` 有 `seed / round / params`，`voices` 有 `source_candidate_id`，但**固化时一个都没写**：

- `_fix_selected_voices_locked()` 的 INSERT 不含 `source_candidate_id`；
- `_generate_one_candidate()` 不写 `candidates.params`；
- `archive_designed_voice()` / `archive_uploaded_voice()` 也不写。

于是"项目 → 槽位 → 候选 → 声线"四段只能靠 `name` 字符串对齐，改名即失联。

**缺口 C：项目来源显示错误（展示层 bug）**

`api.enrich_voice()` 判断公共库用的是 `voice.get("project_id")`：

```python
if not voice.get("project_id"):
    enriched["source_display"] = "公共库"
```

v5 已把该列改名为 `source_project_id`，字典里根本没有 `project_id` 键 → 判断恒真 →
**9 个有来源项目的声线在「全部声线」页全部显示"公共库"**。

**缺口 D：清单排序无位置（写入路径）**

`_fix_selected_voices_locked()` 和 `attach_voice()` 的 `INSERT INTO project_voices` 都**不写 `position`**。
当前 14 行全是迁移脚本回填的，新增行会写 NULL：唯一索引不报错（NULL 互不冲突），
但 `ORDER BY position` 下 NULL 的位置由 SQLite 自行决定，清单顺序会和创建顺序脱节。

### 1.3 标签现状

`voices.tags` 是逗号分隔串，**45 条里只有 1 条有值**。前端已有 chips 输入（`TagInput.tsx`）
与胶囊展示（`.tag-chip`），`upload-voice-redesign.md` 明确记着"后续会加入数据库的设计，
等标签表落地时只需替换 chips ↔ 逗号串这一处序列化"。目前这条路只完成了一半：
UI 是 chips，存储仍是字符串，**无法改名、无法分组、无法表达"仅在本项目内成立"的语义**。

---

## 2. 关联闭环方案

### 2.1 一条原则：三层归属，各司其职

| 层 | 表 | 回答的问题 | 生命周期 |
|---|---|---|---|
| 全局资产 | `voices` | 这把声音本身是什么（音色、参考音频、prompt） | 跨项目、长期 |
| 项目清单 | `project_voices` | 本项目里它扮演谁、叫什么名、排第几、当前是否在用 | 随项目 |
| 需求槽位 | `voice_requests` | 本项目还缺什么角色、给谁的候选 | 随项目，可先于资产存在 |

成品 `outputs` **必须同时持有两把钥匙**：`voice_id`（谁配的音）+ `project_id`（哪个项目）。
现有设计已满足，缺的是"这把钥匙必须能在清单里找到门"的约束。

### 2.2 结构变更（v6 DDL）

```sql
-- ---------- 1. 清单行不再删除，改为状态迁移 ----------
ALTER TABLE project_voices ADD COLUMN state TEXT NOT NULL DEFAULT 'active';
ALTER TABLE project_voices ADD COLUMN replaced_by TEXT;      -- 换版后指向新声线 id
ALTER TABLE project_voices ADD COLUMN updated_at TEXT;
CREATE INDEX IF NOT EXISTS idx_project_voices_state ON project_voices(project_id, state);

-- 存量回填：仍有 active 资产的清单行保持 active；指向 replaced 资产的补一行记录
UPDATE project_voices SET state='active', updated_at=created_at WHERE state='active';

-- ---------- 2. 成品记录当时用的是哪条清单行（快照，不随换版漂移） ----------
ALTER TABLE outputs ADD COLUMN roster_state TEXT NOT NULL DEFAULT 'active';

-- ---------- 3. 补外键（SQLite 需 12-step 重建，脚本内完成） ----------
-- voice_requests.voice_id -> voices(id) ON DELETE SET NULL
-- projects.status CHECK + projects.notes
ALTER TABLE projects ADD COLUMN notes TEXT NOT NULL DEFAULT '';

-- ---------- 4. position 收敛为 NOT NULL（重建表后回填） ----------
-- 回填规则：同一 project_id 内按 created_at, rowid 重新编号 1..N
```

`project_voices.state` 是关键：`active` = 当前在用的版本，`replaced` = 历史版本（保留关联，不再出现在选声线下拉里）。
这比"删了再补"多出一列，换来的是**历史成品的归属永远可解释**。

### 2.3 写入路径改造（4 处）

| 文件 | 函数 | 改动 |
|---|---|---|
| `studio/voice_workflow.py` | `_fix_selected_voices_locked` | 换版时把旧清单行 `state='replaced', replaced_by=新id` **而不是 DELETE**；INSERT 补 `position`（`MAX+1`）与 `source_candidate_id`；同时写 `candidates.params`（design_id / model / seed）与 `voices.reference_ms` |
| `studio/voice_assets.py` | `attach_voice` | INSERT 补 `position`；重复挂载已 replaced 的资产时先把该行复活为 active |
| `studio/voice_assets.py` | `list_voices` | 过滤条件从 `v.status='active'` 改为 `pv.state='active'`（资产 `replaced` 但清单仍是 active 的边界情况才不会误伤） |
| `studio/api.py` | `enrich_voice` | `voice.get("project_id")` → `voice.get("source_project_id")` |

配套查询调整：

- `batch._ITEM_SELECT` 的 `LEFT JOIN project_voices` 加 `AND r.state='active'`（否则同名历史行会放大结果集）；
- `projects.resolve_last_steps` / `infer_last_step` 的 voices 计数加 `pv.state='active'`；
- `run_batch` 的 roster 查询加 `state='active'`，找不到时回退 `voices.name`（保持现有容错）；
- `delete_voice_asset` 的占用检查：`project_voices` 里 `state='replaced'` 的行不再算"其他项目在用"，
  但 `outputs` 引用仍算（有音频在，就不许删）。

### 2.4 存量数据修复（一次，可回滚）

```sql
-- 给 25 条成品的 replaced 声线补回清单行（否则这批成品永远无法重生成）
INSERT INTO project_voices(project_id, voice_id, display_name, position, created_at, updated_at, state)
SELECT o.project_id, o.voice_id, MAX(o.voice_name),
       (SELECT COALESCE(MAX(position),0) FROM project_voices pv2
         WHERE pv2.project_id=o.project_id) + ROW_NUMBER() OVER (PARTITION BY o.project_id ORDER BY o.voice_id),
       MIN(o.created_at), MIN(o.created_at), 'replaced'
FROM outputs o
WHERE o.voice_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM project_voices pv
                   WHERE pv.project_id=o.project_id AND pv.voice_id=o.voice_id)
GROUP BY o.project_id, o.voice_id;

UPDATE outputs SET roster_state='replaced'
WHERE voice_id IN (SELECT id FROM voices WHERE status='replaced');
```

修复后 `缺口 A` 的 25 条会变成"清单里处于 replaced 状态的历史声线"：
成品显示 `名称 · 已换版`，重生成时自动落到 `replaced_by` 指向的新资产（或在 UI 上要求先确认换绑）。

### 2.5 接口契约

- `GET /api/projects/{pid}/voices` 额外返回 `state` / `tags: string[]` / `tag_ids: string[]`；默认只返回 `state='active'`，
  `?include_replaced=1` 时带历史行（供"成品归属溯源"面板用）。
- `GET /api/projects/{pid}/outputs` 的每行带 `voice_state`（active/replaced），前端据此显示"已换版"角标。
- `POST /api/projects/{pid}/voices/{vid}/rebind`（新增）：把一批成品的 `voice_id` 从旧版换到新版，
  只在用户确认"用新声线重录这些台词"时调用。

---

## 3. 项目声线标签设计

### 3.1 先给判断：标签不能只挂在 `voices` 上

同一把声线，在《西游记》里是"旁白"，在《红楼梦》里可能是"王熙凤"。
如果标签写在 `voices.tags`，这两个角色身份会互相污染——全局库里出现"旁白"标签，
下个项目搜"旁白"就会捞到一个其实没用过的声线。

所以标签必须**按语义分域**：

| 域 | 表 | 标签意味着 | 例子 | 跨项目 |
|---|---|---|---|---|
| 资产固有 | `voice_tags` | 这把声音客观是什么 | 女声、童声、低沉、播音腔、沙哑 | 是 |
| 项目语义 | `project_voice_tags` | 在本项目里扮演什么 | 旁白、主角、反派、第二幕、待返修 | 否 |
| 需求阶段 | `request_tags` | 这个槽位想找什么声音 | P0、少年感、东北口音 | 否，固化后继承到项目层 |

三层的关系是**继承 + 覆盖**：槽位标签在固化时复制进 `project_voice_tags`（`origin='inherited'`），
项目里可以再加（`origin='manual'`），也可以删掉继承来的那条——删除只影响本项目，不动资产。

### 3.2 为什么要有"组"

只给一串平铺标签，用户会打出"温柔 / 温柔女声 / 温柔的"三种写法，筛选立刻失效。
所以标签必须挂在**组（维度）**下面，组带两个关键约束：

- `multi=0`（单选组）：同一实体只能选一个，如"角色定位"、"性别年龄"、"制作状态"。
  服务层强制校验，SQLite 的 CHECK 管不了跨行，所以在 `TagService.assign()` 里做。
- `multi=1`（多选组）：如"音色特征"、"出场分组"。

预设组（`builtin=1`，可在设置里停用但不能删）：

| 组 | scope | multi | 预设词条 |
|---|---|---|---|
| 性别年龄 | voice | 0 | 男声 / 女声 / 童声 / 老年 |
| 音色特征 | voice | 1 | 低沉 / 清亮 / 沙哑 / 磁性 / 播音腔 / 甜 / 沧桑 |
| 角色定位 | project | 0 | 主角 / 配角 / 旁白 / 群演 / 反派 |
| 出场分组 | project | 1 | 第一幕 / 第二幕 / 单元 A …（用户自建） |
| 制作状态 | project | 0 | 待录 / 已录 / 待返修 / 已定稿 |
| 需求优先级 | request | 0 | P0 / P1 / P2 |

### 3.3 表结构

```sql
CREATE TABLE tag_groups (
    id         TEXT PRIMARY KEY,
    scope      TEXT NOT NULL CHECK (scope IN ('voice','project','request')),
    name       TEXT NOT NULL,
    multi      INTEGER NOT NULL DEFAULT 1,
    sort       INTEGER NOT NULL DEFAULT 0,
    builtin    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (scope, name)
);

CREATE TABLE tags (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL REFERENCES tag_groups(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '',
    sort        INTEGER NOT NULL DEFAULT 0,
    merged_into TEXT REFERENCES tags(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (group_id, name)
);
CREATE INDEX idx_tags_group ON tags(group_id);

CREATE TABLE voice_tags (
    voice_id   TEXT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
    tag_id     TEXT NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (voice_id, tag_id)
);
CREATE INDEX idx_voice_tags_tag ON voice_tags(tag_id);

CREATE TABLE project_voice_tags (
    project_id TEXT NOT NULL,
    voice_id   TEXT NOT NULL,
    tag_id     TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    origin     TEXT NOT NULL DEFAULT 'manual'
               CHECK (origin IN ('manual','inherited','imported')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, voice_id, tag_id),
    FOREIGN KEY (project_id, voice_id)
        REFERENCES project_voices(project_id, voice_id) ON DELETE CASCADE
);
CREATE INDEX idx_pvt_tag ON project_voice_tags(tag_id);

CREATE TABLE request_tags (
    request_id TEXT NOT NULL REFERENCES voice_requests(id) ON DELETE CASCADE,
    tag_id     TEXT NOT NULL REFERENCES tags(id)          ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (request_id, tag_id)
);
```

三个设计取舍，说明一下为什么这么做：

1. **不给 `project_voice_tags` 建代理主键**，直接用 `(project_id, voice_id, tag_id)`。复合外键能挂到 `project_voices`
   的复合主键上，清单行删了标签自动跟着走，不需要应用层兜底清理。
2. **`merged_into` 而不是物理删**：标签改名/合并时，把旧词条指向新词条，历史关联不用重写，
   查询时 `COALESCE(merged_into, id)` 归一。这样"温柔女声 → 温柔"这种整理不会丢历史。
3. **`voices.tags` 列保留**，但降级为**派生的缓存列**：由 `TagService` 单向写入（`voice_tags` 变化时同步刷新），
   读取一律走关系表。这样现有前端（`AssetsPage` / `AddLibraryVoice` 的 `voice.tags.split(',')`）与
   上传接口的 `tags` 表单字段都能不改就继续工作，渐进迁移。

### 3.4 标签如何跟着数据流动

```
槽位打标签（request_tags）
   │  固化
   ├─→ project_voice_tags(origin='inherited')   ← 项目里可直接用，也可改
   ├─→ voice_tags(仅"资产固有"域的组)            ← 性别年龄/音色特征才上移
   └─→ outputs 反查 (project_id, voice_id) 拿全部项目标签
```

固化时的规则要写死，否则会重复上移：

- `scope='request'` 的组（优先级等）**不上移**，留在槽位；
- `scope='project'` 的组（角色定位、分组）复制到 `project_voice_tags`，`origin='inherited'`；
- `scope='voice'` 的组（音色特征、性别年龄）才写 `voice_tags`。

成品页可以据此做到："第 1 条台词是旁白、第二幕，导出名自动带前缀 `旁白_第二幕_0001_xxx.mp3`"。

### 3.5 API 与前端

新增：

```
GET    /api/tags?scope=voice|project|request     # 组 + 词条 + 使用计数
POST   /api/tags                                 # {group_id, name, color}
PATCH  /api/tags/{tag_id}                        # 改名 / 改色
POST   /api/tags/{tag_id}/merge                  # {into_tag_id}
PUT    /api/library/voices/{voice_id}/tags       # {tags:"a,b"} 或 {tag_ids:[...]}
PUT    /api/projects/{pid}/voices/{vid}/tags     # {tag_ids:[...]}（保留 inherited 语义）
PUT    /api/voice-requests/{rid}/tags
GET    /api/library/voices?tags=a,b&tag_mode=all|any
```

兼容策略：写接口同时接受 `tags` 逗号串（旧调用）与 `tag_ids`（新调用），
内部统一走 `TagService.normalize()`：全角逗号转半角、`strip`、去重、单条 ≤ 20 字、单组 ≤ 5 条。

前端改动（保持现有视觉语言，`.tag-chip` / `TagInput` 复用）：

| 文件 | 改动 |
|---|---|
| `components/ui/TagInput.tsx` | 对外值仍是逗号串（既有约定），内部增加 `suggestions` 按组分段、支持颜色点 |
| `components/ui/VoiceRegisterDialog.tsx` | 标签区按组渲染：单选组用胶囊单选，多选组用 chips |
| `features/assets/AssetsPage.tsx` | 「标签」筛选从单选下拉改为多选 + 组分区；表格标签列带颜色 |
| `features/voice/VoiceRequestForm.tsx` | 槽位表单加「角色定位 / 优先级」两组标签 |
| `features/voice/WorkbenchPage` 的选声线下拉 | 加标签筛选（第 2、3 步选人时最需要） |

### 3.6 迁移与验收

迁移脚本 `scripts/migrate_v6.py`（沿用 v5 的写法：`PRAGMA foreign_keys=OFF` + 单事务 + 逐步守卫 + `foreign_key_check`）：

1. 建 5 张表；写入预设组与词条（幂等：`INSERT OR IGNORE`）；
2. `voices.tags` 逗号串 → `voice_tags`，落入"未分类"组（`scope='voice', multi=1`），原串保留；
3. 补 `project_voices.state/position`、`outputs.roster_state`、`projects.notes`；
4. 重建 `voice_requests` 补外键（12-step），重建 `project_voices` 把 `position` 收紧为 `NOT NULL`；
5. 跑 §2.4 的存量修复；
6. `schema_version = 6`。

验收（每条都要能跑出确定结果）：

```sql
-- 成品必须都能在清单里找到归属行
SELECT COUNT(*) FROM outputs o
WHERE o.voice_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM project_voices pv WHERE pv.project_id=o.project_id AND pv.voice_id=o.voice_id);
-- 期望 0

-- 血缘：本轮之后固化的声线必须有来源候选
SELECT COUNT(*) FROM voices WHERE source='voice_design' AND source_candidate_id IS NULL
  AND created_at > :migration_time;   -- 期望 0

-- 单一维度组不得多选
SELECT project_id, voice_id, g.id, COUNT(*) c
FROM project_voice_tags pvt JOIN tags t ON t.id=pvt.tag_id JOIN tag_groups g ON g.id=t.group_id
WHERE g.multi=0 GROUP BY 1,2,3 HAVING c>1;   -- 期望空
```

---

## 4. 改动清单（按文件）

| 文件 | 内容 |
|---|---|
| `studio/database.py` | `SCHEMA_VERSION=6`、v6 DDL、启动自愈分支 |
| `scripts/migrate_v6.py` | 新建，幂等迁移 + 存量修复 + 备份快照 |
| `studio/tags.py` | 新建 `TagService`：normalize / resolve / assign / list / merge / 缓存列同步 |
| `studio/voice_workflow.py` | 固化换版改状态迁移、补 position / 血缘 / params |
| `studio/voice_assets.py` | attach 补 position、list 改按 `pv.state` 过滤、标签写入 |
| `studio/projects.py` | 步次推断与总览计数加 `pv.state='active'` |
| `studio/batch.py` / `supplement.py` | `_ITEM_SELECT` 的 roster join 加 state、输出 voice_state |
| `studio/api.py` | `enrich_voice` 修列名、标签端点、tags 过滤参数 |
| `frontend/components/ui/TagInput.tsx` 等 5 处 | 见 §3.5 |

## 5. 落地顺序建议

| 阶段 | 内容 | 风险 | 可否单独上线 |
|---|---|---|---|
| P0 | 修 `enrich_voice` 列名、`attach_voice`/固化补 `position` | 极低，纯修 bug | 是 |
| P1 | 固化换版改 `state` 迁移 + 存量 25 条修复 | 中：动清单与成品归属，需先 `backup_snapshot` | 是 |
| P2 | 血缘写入（`source_candidate_id` / `params` / `reference_ms`） | 低，纯增量 | 是 |
| P3 | 标签 5 表 + `TagService` + 迁移 | 中：新建表，不动存量列 | 是 |
| P4 | 前端标签 UI 与筛选 | 低 | 是 |
| P5 | 按标签导出/分组命名 | 低，依赖 P3 | 是 |

P1 与 P3 互不依赖，可以并行；P3 的 `voices.tags` 缓存列策略保证前端不阻塞。

## 6. 风险与待你拍板

1. **P1 换版语义**：成品遇到 `replaced` 声线时，是"自动换绑新声线"还是"保持旧音频 + 显示已换版"？
   我建议**保持旧音频**（成品是交付物，不能悄悄换声音），只在用户主动点「用新声线重录」时才换绑。
2. **标签可见范围**：项目语义标签是否要在「全部声线」页以 `旁白 · 西游记` 的形式反查？
   我建议**可见但只读**，否则全局库会变成项目数据的镜像。
3. **单选组校验位置**：放在服务层（推荐，可给友好错误）还是加触发器（数据库级强制）？
4. **`request_tags` 是否值得**：如果第 1 步的槽位表单近期不打算加标签，可以先只做 `voice_tags` + `project_voice_tags`，
   省掉一张表和一次继承逻辑。
