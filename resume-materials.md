# 简历素材 · Qwen TTS 配音平台建设

> 面向「上海蔚蓝云创数字科技有限公司 — Qwen TTS 平台建设」这一段经历的简历撰写素材。
> 所有条目均对照本仓库真实代码核对过（文件 / 行号可追溯），**不含未实现的能力**。
> 校对时间：2026-10-21

---

## 一、先说结论：这个项目该往哪个方向写

前两段实习（上海旋波科技、北重机车管理 OS）的条目已经把**「SSE 流式 + 有限状态机 + 会话持久化 + JWT + 异构产物预览」**这套叙事写透了。
本项目如果继续写「流式渲染、状态机驱动 UI」，有两个问题：

1. **重复**：同一套关键词在简历里出现两次，面试官会认为是模板复用，含金量反而下降。
2. **不成立**：本项目没有接 LLM 流式对话，是本地 GPU 同步推理 + 轮询进度。被追问会连带影响前一段的可信度。

本项目真正稀缺、且前两段没有的是另一类能力：

> **AI 生成结果的不确定性治理。**
> 模型会输出静音、截断、跑飞；长任务会跑到一半中断；同一句台词会存在多条成品；
> GPU 只有一张 8G 卡；交付文件必须唯一、可追溯、可复现。

这类「正确性 + 资源约束」的工程判断力，比功能罗列值钱得多。建议按此主线组织 3–4 条。

---

## 二、项目头（建议写法）

```text
Qwen TTS 配音平台建设 | 前端 / 全栈开发 | 上海蔚蓝云创数字科技有限公司
技术栈：Next.js 14 + React 18 + TypeScript 5.7 / FastAPI + SQLite / Qwen3-TTS 本地 GPU 推理 / FFmpeg
面向角色配音生产的 AI 音频工作台，覆盖项目 · 声线 · 候选 · 成品 · 导出五域全链路；
前端 67 个 TS(X) 文件约 6.8k 行，后端 studio/ 约 6.9k 行。
```

> 版本口径提醒：仓库真实依赖为 `next@14.2.15` + `react@18.3.1` + `typescript@5.7.2`。
> 写「React 19」会被追问 `use()` / Server Actions 而露底；写 18 不丢分。

---

## 三、主推条目（4 条，可直接粘贴）

### 1. 前端 · 轮询调度与缓存一致性

> 针对多阶段长任务进度高频轮询下的请求堆积与脏读问题，实现链式 `setTimeout` 轮询 Hook（上一次请求完成后再排下一次，替代固定 `setInterval`），避免慢响应乱序返回顶掉新数据；并在传输层加入 GET stale-while-revalidate 缓存（5s TTL + `force` 旁路）与写操作事件总线（正则识别项目域写路径、排除书签类写）自动失效，使切页 / 切项目回访零重复请求、进度数据始终新鲜。

**代码依据**：`frontend/lib/hooks.ts`（`usePolling`）、`frontend/lib/api.ts`（`GET_CACHE` / `onProjectWrite` / `BOOKMARK_WRITE`）

---

### 2. 前端 · 浏览器直写本机目录的交付链路（最亮的一条）

> 基于 File System Access API 实现「成品导出到使用者本机文件夹」：调用 `showDirectoryPicker` 前先做能力与安全上下文探测，并严格把取目录动作放在用户手势的有效期内（不能先 `await` 网络请求，否则超出 transient activation 窗口被浏览器拒绝）；以 `manifest.json` 做增量对账——清理上一轮遗留、本轮已不存在的音频，仅为写入成功的条目记账；不支持该 API 时自动回退 ZIP 下载。配合后端 `zipfile` 写入不可 `seek` 的 sink 触发 data descriptor，实现逐条目边打包边推流，服务端零临时文件落盘。

**代码依据**：`frontend/lib/localExport.ts`、`studio/exports.py`（`_ChunkSink`、`iter_archive`）、`frontend/lib/api.ts`（`apiPostBlob`）

---

### 3. 全栈 · 交付口径的唯一性

> 针对同一句台词存在多条成品（换声线后重录、单句保留、事后补录）导致的交付歧义，将「最后选中者胜」收敛到 `demote_siblings` 单一入口（5 处调用点统一，避免多处各写一遍互相打架）；导出前以 `(角色名, 归一化文本)` 归并同句、保留首次出现的位置序号使文件编号不跳号，并以只读 preflight 提示「合并了几条 / 哪个角色混了几版」而不拦截用户决策；以 manifest 作为文件名与元数据的唯一决策点，使磁盘导出、ZIP 打包、浏览器导出三条路径的产物逐字节一致。

**代码依据**：`studio/takes.py`（`demote_siblings` / `dedupe_latest` / `voice_versions`）、`studio/exports.py`（`_build_manifest` / `export_preflight` / `_completed_export_rows`）

---

### 4. 全栈 · 在线演进的数据模型 + AI 输出质量闭环

可拆成两条，也可合并：

> 主导三源成品（批量 / 单句 / 补录）合并为统一 `outputs` + `origin` 数据模型，用 6 版幂等 DDL、逐列守卫的 `ALTER` 与兼容视图接管旧读路径，实现运行中数据库在线升级，并保证「已迁移库」与「全新安装库」收敛到同一 schema 形状。

> 针对 TTS 输出的静音、早停与失控，建立程序化质检与自动重试：以峰值 / RMS 阈值判静音，按 12 Hz 码率反推 token 预算判定截断，对中文按「字数 → 合理时长窗口」校验节奏漂移，不达标重试至多 5 次；同时把固定的 1024 token 预算改为按文本长度自适应（`96 + n×6`，clamp 128–2048），解决长句被硬截断在 81.8 秒的问题。

**代码依据**：`studio/database.py`（`SCHEMA_VERSION = 6`、`SCHEMA_V5_DDL` / `SCHEMA_V6_DDL`、`_ensure_column`、兼容视图）、`studio/model_manager.py`（`_audio_is_usable` / `_clone_audio_is_usable` / `_clone_max_new_tokens`）

---

## 四、备选条目（视版面取舍，最多补 1–2 条）

### 设计系统与自研控件

> 建立 85 个语义化 CSS token 与 `data-theme` 明暗双主题体系（`<head>` 内联脚本预设主题防白闪）；自研 Select 组件替代原生 `<select>` 展开层（原生弹层由操作系统绘制、无法样式化），采用 `position:fixed` 锚定触发器规避滚动容器裁切，支持空间不足自动上翻与 ↑↓/Home/End/Enter/Esc 全键盘操作；对会随内容长高的弹窗区域统一「固定高度 + 区内滚动」，消除交互过程中弹窗高度跳变。

**代码依据**：`frontend/app/theme.css`（85 个自定义属性 / 830 行）、`frontend/components/ui/Select.tsx`、`Modal.tsx`

### GPU 资源调度

> 在单张 8G 显卡上以 `msvcrt` 字节范围跨进程文件锁 + `atexit` 释放，保证全机仅一个进程持有模型（避免两个进程各自加载撑爆显存）；进程内以可重入锁串行化全部推理调用，切换模型种类时先卸载再 `gc.collect()` + `empty_cache()` 回收显存。

**代码依据**：`studio/model_manager.py`（`CrossProcessGpuLock` / `ModelManager.load` / `unload`）

### 接口安全边界

> 统一「由前端指定后端读哪个文件」类接口的防护做法：路径段以 uuid 形态正则卡死，再用 `Path.resolve().relative_to(root)` 做二次校验；对外写入范围限制在数据目录之内，避免向局域网开放「任意路径写 + 删除」能力。

**代码依据**：`studio/api.py`（`/api/tools/conversions/archive`、`/api/voices/design-archive`）、`studio/exports.py`（导出目录校验）

### 提示词工程与产物可复现

> 为 VoiceDesign 模型设计六段式提示词规范（性别年龄 / 音色质感 / 语速节奏 / 语调起伏 / 情绪态度 / 适用场景）与维度词表、场景模板，前端组装后作为 `instruct` 入参；覆盖用户已编辑内容前统一走确认弹窗保护。生成时固定 seed 且以产物 id 作为落盘名，前端「入库」凭该 id 取回同一段音频（重新生成会换随机 seed，声音不再是同一条），保证候选可复现、声线资产可追溯；导出 manifest 携带 `voice_id` + `voice_version`，支撑混版交付的追溯。

**代码依据**：`frontend/lib/promptPresets.ts`、`frontend/lib/promptAssist.ts`、`studio/voice_assets.py`、`studio/api.py`（`/api/voices/design-archive`）

---

## 五、不能写的内容（会被问穿）

| 不能写 | 原因 |
| --- | --- |
| SSE / 流式回灌 / Agent 思考链 / 多轮工具调用 | 本项目是本地 GPU 同步推理 + HTTP 轮询进度，没有 SSE，也没有 tool-calling。硬写会连带把旋波那段「工具回灌延迟 <200ms」的真实性一起拖下水 |
| JWT 登录态 / 多端会话恢复 | 本项目是单机 / 局域网工具，没有鉴权体系。这段留给旋波 |
| React 19 | 真实为 React 18.3.1 + Next 14.2.15 |
| 「支持多人协作 / 云端部署」 | 数据落在本机 `%LOCALAPPDATA%`，SQLite 单文件，无多租户 |

### 如果一定要凑「Agent 开发」这一栏

不用编造思考链，用**任务运行时**的视角映射，语义完全站得住：

- `BackgroundJob`（锁内 `prepare` 保证校验与建行原子、`threading.Event` 协作式取消、进度字典快照供轮询读取、异常兜底必然收尾）≈ **工具执行状态机**
- 输出质检 + 最多 5 次重试 ≈ **工具返回结果校验与重试策略**
- 固定 seed + 产物 id 寻址 ≈ **生成结果幂等与产物寻址**
- 六段式 prompt 规范 + 模板组装 ≈ **prompt 编排**

写法示例：

> 将单次 TTS 生成建模为「可提交 / 可取消 / 进度可观测 / 结果可寻址」的任务单元，抽象统一后台任务组件（锁内前置校验与建行原子化、协作式取消、进度快照供轮询读取、异常兜底收尾），三类长任务共用一套；任务级 `jobs` 表记录 `total/succeeded/failed` 支持历史回溯。

---

## 六、量化口径速查（写数字前先对一遍）

| 说法 | 是否可写 | 依据 |
| --- | --- | --- |
| 前后端各约 6.8k 行 | ✅ | 前端 6776 行 / 67 个文件；`studio/*.py` 6901 行 |
| schema 迭代 6 版 | ✅ | `SCHEMA_VERSION = 6` |
| 「最后选中者胜」5 处调用点 | ✅ | `set_final`、批量生成完成、重录、补录、单句落库 |
| 85 个语义 token / 830 行主题 | ✅ | `frontend/app/theme.css` |
| 重试至多 5 次 | ✅ | `model_manager.clone()` 循环 |
| 长句 81.8 秒硬截断 | ✅ | 12 Hz 码率 × 1024 token |
| 工具回灌延迟 <200ms | ❌ | 本项目无此链路，别挪用 |
| 支撑 QPS / 并发用户数 | ❌ | 单卡单实例串行推理，没有并发吞吐指标 |

---

## 七、两版措辞（投递时按岗位二选一）

**投前端**：主推 §3.1 轮询与缓存一致性、§3.2 浏览器直写交付、§4 设计系统与自研 Select，全栈两条压缩为一条背景说明。

**投全栈 / AI 应用**：主推 §3.3 交付口径唯一性、§3.4 数据模型演进 + 输出质检、§4 GPU 资源调度，前端两条选一条作支撑。

---

## 附：素材溯源

| 主题 | 文件 |
| --- | --- |
| 统一后台任务 | `studio/jobs.py` |
| 模型调度与质检 | `studio/model_manager.py` |
| 成品口径 | `studio/takes.py` |
| 导出与流式打包 | `studio/exports.py` |
| schema 迁移 | `studio/database.py` |
| 批量生成 / 重录 | `studio/batch.py` |
| 声线工作流 / 候选 | `studio/voice_workflow.py` |
| HTTP 传输层与缓存 | `frontend/lib/api.ts` |
| 轮询 / 轻提示 | `frontend/lib/hooks.ts` |
| 本机目录导出 | `frontend/lib/localExport.ts` |
| 提示词数据层 | `frontend/lib/promptPresets.ts` |
| 设计 token 与主题 | `frontend/app/theme.css` |
| 自研下拉 | `frontend/components/ui/Select.tsx` |
