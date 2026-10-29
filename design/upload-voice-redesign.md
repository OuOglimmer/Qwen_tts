# 「上传声线」页改版方案（v4 定稿 · 阶段一／阶段二均已实施）

> 2026-10-16 · v3 → v4 按你的两条回执修订（② 做闭环、入库不加归属），§七 其余三条按低风险默认采用。
> 所有现状描述均来自对现行代码的逐文件核对，关键处标注 `文件:行号`。
> **实施状态见文末 §十**（阶段一、阶段二都已完成并实测）。

## 零、你的决定（已锁定）

| # | 你的决定 | 方案落点 |
|---|---------|---------|
| 1 | **保持独立按钮**（试听 / 入库分开）；点「入库」时弹窗命名 + 打标签 | §二 |
| 2 | **做**批量转化 + 批量下载 | §三 |
| 3 | 「参考音频语种」给方案看能否实现 | §四 → **可实现，推荐方案 3A** |
| 4 | 把 VoiceDesign 合并到**「全部声线」页** | §五 |
| 5 | ② 文字描述生成**做入库闭环** | §五 5.3 |
| 6 | 入库弹窗**不加「归属项目」** → 一律进公共库 | §二 2.2 |

因此页面与导航重排为：

```
声线库
├── 全部声线      ← 声线总库 + 文字描述生成（原 VoiceDesign） + 单句试听
└── 上传声线      ← 一页两模式：① 上传克隆  ② 音频转化
（「工具」组整组消失）
```

---

## 一、现有相关模块盘点（核对结论）

| # | 模块文件 | 入口 | 实际功能 | 是否存档 |
|---|---------|------|---------|---------|
| 1 | `features/assets/UploadVoicePage.tsx` | 声线库 → 上传声线 | 上传参考音频 → 克隆试听 → **固化进全局声线库** | ✅ 落 `VOICE_DIR/{voice_id}/` |
| 2 | `features/tools/CloneTool.tsx` | 工具 → VoiceClone | 上传参考音频 → 克隆试听 | ❌ 落 `TEMP/standalone/voiceclone/` |
| 3 | `features/tools/ConvertTool.tsx` | 工具 → 音频转化 | 格式转换 WAV(24kHz 单声道) ↔ MP3(128kbps)，可多选 | ❌ 落 `TEMP/conversions/{id}/` |
| 4 | `features/assets/DesignTool.tsx` | 工具 → VoiceDesign | 文字描述生成音色 → 试听 | ❌ 落 `TEMP/standalone/voicedesign/` |
| 5 | `features/batch/AddLibraryVoice.tsx` | 工作台第 1 步 | 从全局库挑现成声线关联进项目 | 只写 `project_voices` |
| 6 | `features/voice/BulkAddVoices.tsx` | 工作台第 1 步 | 批量建空槽位草稿 | 仅草稿 |

模块 **1 与 2 的差别只有"存不存档"**（`/api/voices/upload` vs `/api/tools/clone`），却让用户在侧栏两处各选一次 —— 这就是合并的理由。

---

## 二、决策 1：模式一 · 上传克隆（两个独立按钮 + 入库登记弹窗）

### 2.1 交互结构

**表单区**（只放"怎么克隆"，不放"叫什么"）：

| 分组 | 字段 | 说明 |
|------|------|------|
| ① 参考音频 | 拖放区 | WAV/MP3/FLAC/OGG/M4A/AAC/WMA，≤80MB。保留现有 MD5 去重拦截 |
| ② 克隆素材 | 参考音频对应文字 | 高质量克隆必填；勾"仅声学模式"则免填 |
| ② | 仅声学模式 | 沿用 `x_vector_only` |
| ③ 试听 | 试听文本 | 沿用 `preview_text`（**必填**，两个按钮都要用） |

**按钮区**（并排，各自独立、互不依赖）：

| 按钮 | 动作 | 端点 | 产物 |
|------|------|------|------|
| `试听`（次） | 生成一段试听 | `POST /api/tools/clone` | 落 `TEMP/standalone/voiceclone/`，**不写库** |
| `入库`（主） | **弹出「登记声线」对话框** → 确认后入库 | `POST /api/voices/upload` | 落 `VOICE_DIR/{voice_id}/`，**持久** |

两者**都不要求先点另一个**。点「入库」不必先试听（后端 `archive_uploaded_voice` 内部本身就会生成 `preview.mp3`，所以入库一步就够）。这个"两个按钮各管一段"的写法，正是你说的**保持独立**。

### 2.2 「登记声线」弹窗字段

点「入库」后弹出，字段就是你说的**命名 + 标签**，另加两个必要的归属信息：

| 字段 | 必填 | 说明 |
|------|------|------|
| **声线名称** | ✅ | 全局库允许同名（靠来源项目区分），**项目清单内**同名会被拦（`project_voices` 有 `UNIQUE(project_id, display_name)`） |
| **标签** | ❌ | **chips 输入**：回车/逗号添加，点 × 删除。可选已有标签自动补全 |
| 备注 | ❌ | 沿用 `notes` |

**不加「归属项目」下拉**（你已定）。所以入库一律 `project_id = NULL`，即**只进公共库、不自动关联任何项目**。

连带影响，需要明确：从「上传声线」页入库的声线**不会**出现在当前项目的声线清单里。要在项目里用它，走工作台第 1 步的「复用声线库」（`AddLibraryVoice`）挂载 —— 那条路径本来就在，且符合"库与项目解耦"的设计。好处是入库时不必先想"这声线归哪个项目"，减少一次决策。

弹窗内并显示一行**只读摘要**（参考音频文件名 / 参考文字字数 / 模式），避免弹窗遮住上下文后不知道自己在给谁命名。

### 2.3 标签的数据格式（过渡方案）

现状：`voices.tags` 是 **`TEXT NOT NULL DEFAULT ''`**，存**逗号分隔字符串**（`voice_assets.py:170`；前端 `AssetsPage.tsx:12,39,43` 按逗号切分做筛选与 `.tag-chip` 展示）。

你说"**后续会加入数据库的设计**" —— 方案按这个前提准备，但**本次不建标签表**：

- **UI 按 chips 做**（等价于独立标签的体验）
- **提交仍是逗号分隔串**，`apiPost`/`apiUpload` 参数形状不变
- 等标签表落地时，**只需替换"chips ↔ 逗号串"这一处序列化**，UI 与调用点不动

这样今天就能用，且不给你未来的数据库设计挖坑。

### 2.4 合并掉的模块

| 原模块 | 去向 |
|--------|------|
| `UploadVoicePage` | → 模式一主体 |
| `CloneTool` | → 并入模式一，成为「试听」按钮（原来它只差"不存档"，现在由按钮表达） |

---

## 三、决策 2：模式二 · 音频转化（批量转化 + 批量下载）

从工具页迁入，功能不变（多文件、两种目标格式），补齐现在**真实缺失**的两样。

### 3.1 界面

| 分组 | 内容 | 对比现状 |
|------|------|---------|
| ① 待转文件 | 拖放区 + **文件清单**：文件名 / 大小 / 源格式 / **移除按钮** | 现状只显示一行逗号拼接的名字，看不出选了几个、不能单个移除 |
| ② 目标格式 | 「MP3 · 128kbps（带耳机听 / 分发用）」/「WAV · 24kHz 单声道（项目内使用）」 | 把技术参数翻译成用途 |
| — | 主按钮「开始转换」+ **逐文件状态** | 现状是同步阻塞，全程只有一个"处理中…" |
| ③ 结果 | 每文件一行：播放器 + 单个下载；顶部「**全部下载（ZIP）**」 | **现状无批量下载** |
| ③ | 提示：产物在临时目录，**刷新即失效** | 现状没讲清，用户以为存住了 |

### 3.2 逐文件进度怎么实现（关键设计）

`convert_files`（`studio/tools.py:28-49`）是**同步阻塞**，且**遇错即整单失败**（第一个文件出错就抛异常，整批 500）。所以：

**前端改为逐文件顺序请求**（每次调 `/api/tools/convert` 传 1 个文件）：

- ✅ 天然拿到**逐文件进度**（等待 / 转换中 / 完成 / 失败 四态）
- ✅ **单文件失败不影响其他文件**（顺带修掉"遇错整单失败"这个真实缺陷）
- ✅ 格式转换是纯 CPU（`normalize_to_wav` / `wav_to_mp3`，**不碰 GPU**），无并发争抢问题
- ✅ **后端 `convert_files` 零改动**

代价是 N 次请求，但每个文件都小、转换很快，感知不到。

### 3.3 批量下载（ZIP）—— 需要新增后端

现有 `/api/projects/{id}/outputs/archive.zip`（`api.py:911-932`）走的是 `exports.iter_archive`（`exports.py:265-279`），**只读库表里的成品**，不扫 `TEMP/conversions/`。所以需要新端点：

**新增 `POST /api/tools/conversions/archive`**

```
请求体: { "items": ["<conversion_id>/<文件名>", ...] }
响应:   StreamingResponse(application/zip)
```

- 打包逻辑复用 `exports.py` 的 `_ChunkSink` + `zipfile` 流式写法，**新增一个 `iter_file_archive(entries)`**（`iter_archive` 会固定塞 `manifest.json`，转换场景不需要）
- **不做落盘**：ZIP 只存在于响应字节流里，服务器不留交付副本（与成品导出同一策略）
- **安全校验（必做）**：每个 item 必须匹配 `^[0-9a-f-]{36}/[^/\\]+\.(mp3|wav)$`，并 `resolve()` 后确认仍在 `TEMP_DIR/conversions` 之下，否则 400。防止路径穿越

前端点「全部下载（ZIP）」时，把已成功转换的文件按 `conversion_id/name` 收集起来一次性提交。

### 3.4 产物归属要写清楚

转换结果落 `TEMP/conversions/{id}/`，**不进声线库、不进成品、不进导出包**，与模式一的"入库"是两个世界的产物。界面上必须讲明，否则用户会以为转完就存住了。

---

## 四、决策 3：「参考音频语种」入库 —— 方案与可行性

### 4.1 现状的语义错位（先说清问题）

| 字段 | 实际含义 | 后端用途 |
|------|---------|---------|
| `voices.language` | **试听文本的语种** | 只用于朗读试听文本：`clone(preview_text, language, ...)`（`voice_assets.py:151`）。**不参与建 prompt、不约束日后生成** |
| （不存在） | 参考音频本身的语种 | 目前**无处可存** |

而 UI 把它摆在"参考音频对应文字"上方，读起来像在填"这条音频的语种"。**语义摆错了位置**，是页面读不懂的根源之一。

### 4.2 推荐方案 3A：新增 `voices.ref_language` 列 ✅ **可实现**

**改动（全部是"加一个可选字段"，向后兼容）**：

| 文件 | 改动 |
|------|------|
| `studio/database.py` | `SCHEMA_VERSION` 3 → 4；建表语句加 `ref_language TEXT NOT NULL DEFAULT ''`；在 `initialize()` 的迁移块（`database.py:276-281` 现有 `audio_hash` 范式之后）加 `ALTER TABLE voices ADD COLUMN ref_language TEXT NOT NULL DEFAULT ''` |
| `studio/voice_assets.py:91` | `archive_uploaded_voice(...)` 增参 `ref_language: str = ""`；`INSERT`（`:170`）补列 |
| `studio/service.py:185` | 透传参数 |
| `studio/api.py:604` | `/api/voices/upload` 增 `ref_language: str = Form("")` |
| `studio/voice_workflow.py:748` | 固化 INSERT 补 `ref_language`（设计声线没有参考音频 → 留空） |
| `frontend/lib/types.ts` | `VoiceAsset.ref_language?: string` |
| `frontend/features/assets/AssetsPage.tsx` | 表格加「素材语种」列；空值显示 `—`（未标注） |
| `frontend/features/assets/UploadVoicePage.tsx` | 「参考音频语种」下拉**真正提交**，不再只作展示 |

**历史数据怎么处理**：**留空，不回填**。
不能用 `language` 回填 —— 那是试听文本语种，语义不同，回填等于**造伪数据**。旧声线统一显示「未标注」，可在详情里手动补。

**为什么值得做**：
1. 现在字段填了不落库，下次打开就没了 —— 用户会以为系统记不住
2. 素材语种可检索：能筛出"用中文素材做的声线"，排查"英文素材却选了中文试听"这类问题
3. 为将来"跨语种生成时提示素材语种不匹配"打地基

**风险**：低。纯新增列 + `DEFAULT ''`，不动任何现有读写路径。唯一注意点是 `INSERT` 要跟着补列（有默认值所以安全）。

### 4.3 备选方案

| 方案 | 评价 |
|------|------|
| **3B：新建 `voice_meta` 表** | 为单个字段拆表，过度设计。**不推荐** |
| **3C：不存库，只作 UI 展示** | 就是现状，字段填了白填。**不推荐** |

---

## 五、决策 4：VoiceDesign 并入「全部声线」页

### 5.1 先说一个关键发现：设计声线**已经能固化入库**

`voice_workflow.py:697-760` 的固化逻辑（工作台第 1 步「固化声线」）已经把设计生成的声线写进了 `voices` 表：

```
source        = 'voice_design'
prompt        = <自然语言描述>
reference_wav = 生成音频的副本
prompt_path   = prompt.safetensors
```

也就是说 **`voices` 表本来就支持"无参考音频、只有设计描述"的声线**，固化后与克隆声线**行为完全一致**（可被克隆复用、可批量配音）。

**缺的只是一个脱离项目/槽位的独立入口** —— 现在必须先在某个项目里建 `voice_requests` 槽位、生成候选、再固化。而 `DesignTool`（`TEMP/standalone/voicedesign/`）不给这条路。

### 5.2 「全部声线」页的新结构

页面变成三段（沿用现有 `SectionTitle` 分节骨架）：

```
① 声线总库     ← 现状：筛选（搜索/来源/标签）+ 表格 + 删除
② 文字描述生成  ← 原 VoiceDesign 迁入，补上「试听」/「入库」两个按钮（与模式一同款弹窗）
③ 单句快速试听  ← 现状：QuickGeneratePanel（选已固化声线 → 读一句话）
```

② 与 ③ 是互补的：**② 造声线**（文字 → 音色），**③ 用声线**（音色 + 文字 → 音频）。不重复。

② 的「入库」按钮与模式一**复用同一个「登记声线」弹窗**（名称 + 标签 + 备注，同样 `project_id = NULL`），用户不必学两套。

### 5.3 「生成 → 入库」闭环 —— **你已确认：做**

现状 `DesignTool` 只出试听，**无法入库**。要做到闭环，方案是：

**新增 `archive_designed_voice(...)`（`studio/voice_assets.py`）** —— 把上面 §5.1 那段固化逻辑抽出来，去掉对项目与 `voice_requests` 的依赖，接受 `name / tags / notes / project_id / prompt / language / text / 生成音频路径`，直接写 `voices`（+ 可选 `project_voices`），并复用 `create_prompt` + `save_prompt` 生成 `prompt.safetensors`。

**新增端点 `POST /api/voices/design-archive`**（或让 `/api/tools/design` 支持 `archive: true`）。

**顺带补一处**：固化 INSERT（`voice_workflow.py:748`）**没写 `tags`/`notes`**，所以设计声线入库会丢标签。做这个功能时要一并补上，否则「入库弹窗」的标签在②这条路径上白打。

### 5.4 导航收窄

| 文件 | 改动 |
|------|------|
| `frontend/lib/nav.ts` | `Page` 去掉 `VoiceDesign` / `VoiceClone` / `音频转换`；**`TOOL_TYPES` 整个删除**（`nav.ts:13-17`） |
| `frontend/features/shell/Sidebar.tsx:151-156` | 删掉整个「工具」组 |
| `frontend/app/page.tsx:104-106` | 删掉 3 条工具 slot + `ToolPage` 导入（`:22`） |
| `frontend/features/tools/` | `ToolPage.tsx` 删除；`DesignTool.tsx` / `CloneTool.tsx` / `ConvertTool.tsx` 迁入 `features/assets/` 后删除 |

---

## 六、实施清单（按文件汇总）

### 前端

| 文件 | 改动 | 决策 |
|------|------|------|
| `features/assets/UploadVoicePage.tsx` | 重写：表单精简 + 两个独立按钮 + 入库弹窗接入 + 参考音频语种提交 | 1, 3 |
| `components/ui/TagInput.tsx` | **新建** chips 标签输入（复用 `.tag-chip` 样式，`theme.css:395`） | 1 |
| `components/ui/VoiceRegisterDialog.tsx` | **新建** 「登记声线」弹窗（名称/标签/备注；**无归属**） | 1 |
| `features/assets/AssetsPage.tsx` | 加「素材语种」列；插入 ② 文字描述生成区块 | 3, 4 |
| `features/assets/DesignPanel.tsx` | **新建**（由 `tools/DesignTool.tsx` 迁入 + 加「试听」/「入库」按钮） | 4 |
| `features/assets/ConvertPanel.tsx` | **新建**（由 `tools/ConvertTool.tsx` 迁入：文件清单 + 逐文件状态 + 全部下载 ZIP） | 2, 4 |
| `lib/types.ts` | `VoiceAsset.ref_language?: string` | 3 |
| `lib/nav.ts` | 收窄 `Page`；删除 `TOOL_TYPES` | 4 |
| `features/shell/Sidebar.tsx` | 删除「工具」组 | 4 |
| `app/page.tsx` | 删除工具 slot 与 `ToolPage` 导入 | 4 |
| `app/theme.css` | 弹窗、chips 输入、文件清单状态样式 | 1, 2 |

### 后端

| 文件 | 改动 | 决策 |
|------|------|------|
| `studio/database.py` | `SCHEMA_VERSION` 3→4；建表 + 迁移加 `ref_language` | 3 |
| `studio/voice_assets.py` | `archive_uploaded_voice` 增参；新增 `archive_designed_voice` | 3, 4 |
| `studio/service.py` | 透传 + 暴露新方法 | 3, 4 |
| `studio/api.py` | upload 增 `ref_language`；`tools/convert` 响应加 `conversion_id`；**新增** `tools/conversions/archive`；**新增** `voices/design-archive` | 2, 3, 4 |
| `studio/exports.py` | 新增 `iter_file_archive(entries)`（复用 `_ChunkSink`） | 2 |
| `studio/voice_workflow.py` | 固化 INSERT 补 `ref_language` / `tags` / `notes` | 3, 4 |
| `studio/tools.py` | **零改动**（逐文件请求复用现有 `convert_files`） | 2 |

---

## 七、决策状态（v3 的 5 处追问 → 回执）

| # | 问题 | 结论 |
|---|------|------|
| 1 | 入库弹窗要不要「归属项目」 | **不加**（你已定）→ 一律进公共库，`project_id = NULL`。§2.2 已改 |
| 2 | ② 文字描述生成要不要做入库闭环 | **做**（你已定）→ 新增 `archive_designed_voice()` + `POST /api/voices/design-archive`。§5.3 |
| 3 | 标签过渡方式（chips UI / 逗号串落库） | **按建议默认采用** —— 今天就能用，不给你未来的标签表挖坑 |
| 4 | 「全部声线」页三段并一页会变长 | **按建议默认采用**：不做折叠、不做页内 tab，三段平铺（沿用现有 `SectionTitle` 分节，与现状风格一致）。若你之后觉得长，再加折叠成本很低 |
| 5 | ZIP 里要不要 `manifest.json` | **按建议默认采用**：不带，就是纯音频文件（成品导出的清单逻辑不套用到临时转换产物上） |

第 3/4/5 条是低风险选择，若你有别的偏好，说一声即可，不影响主体实现。

---

## 八、视觉规格（沿用现有 token，不新增设计语言）

| 项 | 取值 | 来源 |
|----|------|------|
| 主色 | `#3b82f6` | `theme.css` |
| 描边 | `#e5e5e0` / `#d9d9d3` | `--line` / `--line-strong` |
| 圆角 | 12px 卡片 / 8px 控件 | `--radius` |
| 单行控件高 | 36px | `--control-h` |
| 标签 chip | `2px 10px` / `999px` / `11px` | `.tag-chip`（`theme.css:395`） |
| 音频条 | 自绘 4px 细轨 + 10px 圆头滑块，控件总高 30px | 上一轮改版已全站生效 |
| 分组间距 | 组间 14px + 1px 分隔线；组内 12px | 取代 `.control-panel > * { margin-bottom:10px }` 的等距排布 |

---

## 九、实施顺序建议（分两阶段，可一次做完）

共 **18 个文件**（前端 11 / 后端 7），彼此耦合不深，可切两刀：

### 阶段一 · 「上传声线」页两模式 + 素材语种入库

| 决策 | 文件 |
|------|------|
| 1 | `UploadVoicePage.tsx`（重写）、`TagInput.tsx`（新）、`VoiceRegisterDialog.tsx`（新）、`theme.css` |
| 2 | `ConvertPanel.tsx`（新）、`api.py`（convert 响应加 id + 新 ZIP 端点）、`exports.py`（`iter_file_archive`） |
| 3 | `database.py`（`SCHEMA_VERSION` 4 + 迁移）、`voice_assets.py`、`service.py`、`api.py`（upload）、`types.ts`、`AssetsPage.tsx` |

**这一阶段做完，用户就能用上「一页两模式」和「素材语种」，且性能与视觉问题一并清掉。** 侧栏「工具」组此时仍在（不删），页面功能重复但可用 —— 属于安全的中间态。

### 阶段二 · 「全部声线」页合并 + 设计声线入库闭环

| 决策 | 文件 |
|------|------|
| 4 | `AssetsPage.tsx`（加 ② 区块）、`DesignPanel.tsx`（新）、`nav.ts`（收窄）、`Sidebar.tsx`（删工具组）、`page.tsx`（删 slot）、删 `features/tools/` |
| 4 | `voice_assets.py`（`archive_designed_voice`）、`service.py`、`api.py`（`voices/design-archive`）、`voice_workflow.py`（固化 INSERT 补 `tags`/`notes`/`ref_language`） |

**阶段二才有破坏性改动**（删侧栏入口、删组件）。放后面可以先把阶段一验证过，降低一次性改坏的风险。

### 两步共用的验证

- `npx tsc --noEmit`
- 后端 `/api/projects` 等接口耗时复测（上一轮已从 1.84s 降到 0.015s，确认无回归）
- 用无头 Chrome + 项目真实 CSS 渲染核对页截图验证视觉（上一轮用的同一套办法）
- 迁移验证：对**现有 app.db 的副本**跑一次 `Database()` 初始化，确认 `SCHEMA_VERSION` 3→4 后旧数据完好、`ref_language` 全为空串

---

## 十、实施记录（2026-10-16 当天完成两阶段）

### 阶段二 · 实际落点（与计划的偏差已注明）

| 文件 | 改动 |
|------|------|
| `studio/tools.py` | `design_sample(text, language, prompt, design_id=None)`：产物改为 `standalone/voicedesign/<design_id>.{wav,mp3}`，**中间 wav 保留** |
| `studio/voice_assets.py` | 新增 `archive_designed_voice()` |
| `studio/service.py` | 透传 `archive_designed_voice` / `design_sample(design_id)` |
| `studio/api.py` | `/api/tools/design` 回传 `design_id`；新增 `DESIGN_ROOT` + `_resolve_design_audio()` + `POST /api/voices/design-archive` |
| `studio/voice_workflow.py` | 固化 INSERT 显式补 `ref_language`/`tags`/`notes`（见下方说明） |
| `frontend/features/assets/DesignPanel.tsx` | **新建**：声线提示词 + 语言 + 试听文本，`试听`/`入库` 两个按钮 + 复用「登记声线」弹窗 |
| `frontend/features/assets/AssetsPage.tsx` | 表格下方插入 ② 区块，归档后刷新声线库 |
| `frontend/lib/nav.ts` / `features/shell/Sidebar.tsx` / `app/page.tsx` | 删三个工具页、`TOOL_TYPES`、「工具」导航组与对应 slot |
| `frontend/features/tools/` | **整目录删除**（已核 `ConvertPanel` 是 `ConvertTool` 的超集；旧文件备份在 `.workbuddy/backup/stage2-removed-tools-2026-10-16/`） |
| `frontend/app/theme.css` | `.quick-*` 与 `.design-*` 共用同一张卡片规则（两面板视觉语言一致，不复制样式） |

**三处与文档原稿的偏差，都是有理由的：**

1. **不加「归档后重新生成」的路径**：入库固化的是 `design_id` 指向的**那一段**，即用户刚试听的那段。原稿只写"新增端点"，没写这个约束；但重跑一次会换一个随机 seed，试听的和入库的就不是同一把声音。前端按「描述/语言/文本」签名判断是否可复用，没变就直接固化。
2. **`design_sample` 保留中间 wav**：固化要拿 wav 原样做 `reference.wav`，只剩 mp3 就得先解码回 wav（白过一次有损压缩）。
3. **`voice_workflow.py` 的 `tags`/`notes` 只能写空串**：原稿说"补上漏写的 tags/notes"，但 `voice_requests` 表**根本没有** tags/notes 列（工作台第 1 步也没有这两个输入），所以那一路**无处可取**，写空是事实而非漏写。真正带标签入库的路径是新的 `archive_designed_voice()` —— 标签从弹窗直接收上来。

### 阶段二 · 验证记录（全部实测，`/api/projects` 与迁移在数据目录副本上跑）

| 项 | 结果 |
|---|---|
| `/api/tools/design` | 200；返回 uuid 形态 `design_id`；wav+mp3 都落盘；url 可访问；**未写库** |
| `POST /api/voices/design-archive` | 200；`source=voice_design`、`project_id=NULL`、`prompt`=描述、`ref_text`=试听文本、`tags`/`notes` 落库、`ref_language=''`、`audio_hash` 非空；资产四件套齐全；metadata 与固化路径同构；列表来源显示「公共库」 |
| 路径穿越 | `../../app.db`、`abcd`、非 uuid、合法 uuid 但文件不存在 → 全 400；缺 name / 缺 text → 422 |
| 固化声线可用性 | `generate_with_voice()` 走真实 `load_prompt` + `clone` 路径成功出 mp3 |
| 工作台固化（补列后） | `fix_selected_voices()` 成功，新行 `ref_language`/`tags`/`notes` 均为 `''` |
| 迁移 / 幂等 | 副本 `schema_version=4`；七表行数增量恰为本次新增（projects+1、voices+2、project_voices+1、voice_requests+1、candidates+1）；二次初始化幂等 |
| 性能 | `/api/projects` 最快 5.7 ms（无回归） |
| 前端 | `tsc --noEmit` 通过；真实页面（无头 Chrome 走 dev server）截图核对 ②③ 两张卡片；SSR HTML 中「工具 / VoiceDesign / VoiceClone / 音频转换」出现次数均为 0 |

### 尚未处理（沿用原状，不在本次范围）

- `outputs/quick/`、`temp/standalone/voicedesign/`、`temp/conversions/` 三处**都没有清理机制**；设计产物现在多留一份 wav，占用略增。
- 快速试听与设计生成抢 GPU 锁时**没有进度与取消**。
- 同一个 `design_id` 重复入库会生成两条声线（前端已防重复提交，后端未拦）。
