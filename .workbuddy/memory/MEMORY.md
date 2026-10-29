# Qwen-TTS-Voiceover-Studio · 长期约定

> 跨会话事实。日常流水见 `YYYY-MM-DD.md`。

## 用户偏好

- **先出方案/流程图，确认后再改代码**。编号计划（"方案一/阶段二"）先回会话核对指代。
- 视觉要成体系（统一 token），不接受左右失衡、控件忽大忽小。

## UI 约定（弹窗 / 可变内容）

- 内容会长高的区域：**钉死 `height` + 区内滚动**，绝不撑大外框。已落地 `.builder-body`(300) /
  `.builder-card .prompt-preview`(76) / `.bulk-names`(154) / `.confirm-scroll-list`(152) /
  `.tag-input-box`(66) / `.wizard-chips`(88) / `.wizard-voice-list`(330)。必须 `height` 不能
  `max-height`（条数少就矮，一切筛选就跳）；矮屏兜底 `height:min(330px,44vh)`。
- 钉死高度必须 `align-content:flex-start`：`center` 溢出时首尾各裁一半，**顶部裁掉的部分滚不回来**。
  只有"空态不溢出"的档位可用 center（`.wizard-chips.is-empty`）。
- 空态提示放容器**内部**；少用条件渲染控制块出现/消失，改"常驻 + `.is-off`"或空态占位。
- **运行前后不变尺寸：`display:grid` 钉死行高 + 常驻占位**。`.task-stats`(`grid-template-rows:20px 41px`)
  是样板：进度条常驻 + 空闲 `visibility:hidden`（别用 `display:none`），文案 2↔1 行的差被行高吃掉；
  窄屏另开档（1180→3 行、1000→4 行），否则钉死的高度会裁掉折行。
- 输入型弹窗 `<Modal fixed>`（`.dialog-fixed` 462px，`--dialog-h` 覆写）；弹窗内 `textarea` 禁
  `resize`（拖它等于拖整张卡片，实测 431→660）。
- 权重坑：`.dialog-card input`(0,1,1) 盖掉单类紧凑样式，胶囊内小 input 必须 `!important`
  （`.tag-input-field`/`.chip-name`，否则 28px 肿到 44px）；`.tag-chip` 文字要包 `.tag-chip-text`
  才有省略号（flex 容器对匿名文本无效）。
- **`.panel-heading span` 的灰底胶囊会穿进 fixed 浮层**：弹层 DOM 上仍挂在标题栏里，role-select 的选项
  文字 span 被套背景 + `padding:5px 9px` + 15px 圆角 + 继承 650 字重（实测同一面板 164/182/182 三种宽度，
  且"选中 600"比未选中更细）。已用 `.panel-heading .xselect span{…}` 还原。同类坑：
  `.candidate-panel … .panel-heading-actions` 要 0,4,1 才压得住。
- 表格内/滚动容器内浮层一律 `position:fixed` 锚定视口（绝对定位被 `overflow:auto` 裁掉并撑出滚动条）：
  `.xselect-panel`/`.audio-volume-pop`/`.row-menu`。**右对齐别用 `right`**（fixed 包含块少一条滚动条宽，
  恒偏 15px），用 `left:rect.right` + `translateX(-100%)`，动画也要带。
- 自定义滚动条：`scrollbar-width:thin` 只对 Firefox 生效，Chrome 必须 `::-webkit-scrollbar`；
  容器加 `scrollbar-gutter:stable`。
- `th` 没有 nowrap（globals 只给 `td` 加了），列挤窄时表头竖排；theme.css 已补 `th{white-space:nowrap}`。
- flex 行"输入框 + 短按钮"：按钮一律 `flex:0 0 auto; width:auto; white-space:nowrap`，否则
  `flex:1`(basis 0) 只剩 min-content，中文被拆两行。
- 与输入框并排的定高控件别写死 `height`：`.wizard-picker-bar` 是 stretch，输入框实际 39.25px，写死
  32px 就贴顶留 5px 深底。用 stretch + `inline-flex` 居中。
- 自绘下拉 `.xselect-option` 行高**钉死 32px**，必须与 `Select.tsx` 的 `ROW_H` 同步（面板高度按行数
  估算，不同步会算错翻上/翻下）；`.xselect-panel` 显式 `font-weight:400`，别继承容器加粗。

## 工作台步骤 1 结构（2026-10-26 定稿）

- 只剩两块：`panel-grid`（左表单 + 右候选面板）与「声线槽位」表；表标题栏只有「下一步：配音生成 →」。
- 建槽入口 `.slot-label-row > .slot-label-actions`：`＋ 添加多条声线`（`BulkAddVoices`，4 条起步）、
  `＋ 添加已有声线`（`AttachLibraryVoices`，挂进来即 `fixed`）。
- **固化入口唯一**：`CandidatePanel` 标题栏 `固化当前声线`。**只固化当前槽位**（2026-10-28）：前端 `fix()`
  带 `request_id` → `POST /api/projects/{pid}/voices/fix` → `fix_selected_voices(pid, request_id)` →
  `selected_requests()` 加 `r.id=?`；缺省走旧批量路径（`studio_e2e_smoke.py` 按旧签名调用）。置灰判据
  `canFix = activeRequest?.status === 'selected'`；"有无候选 selected=1"由后端 JOIN 拦。不做双入口。
- 操作列：`加载 / 改名 / 解冻(仅 fixed) / 查看历史声线(有历史才显示) / 删除`。原「已固化声线」盒子已删，
  解冻搬到行内 —— 删这盒子最易漏解冻入口。
- **重新固化要先确认**：`select_candidate` 只把 status 写回 `selected`、**不清 `voice_id`**，所以
  `status==='selected' && voice_id` = "会覆盖正在生效的声线"。命中时 `confirm({danger:true})` 点名槽位；
  解冻后再固化不弹（旧资产已 replaced）。
- `shortlist-block .panel-heading` 必须 `display:flex`；`.slot-label-row .btn` 已压成 11px。

## 槽位历史声线（2026-10-26）

- `查看历史声线` 是**只读 + 删除**，**不做恢复/复用**（用户明确不要版本标注、不要"换回旧版"）。
- 归属判定两处必须一致：`list_voice_requests.history_count` 与 `voice_request_history()`。硬证据是
  `voices.source_candidate_id → candidates.request_id`（改名不影响）；v6 前旧资产无候选血缘，用
  `v.name = 槽位名` 兜底（项目内槽位名唯一）。
- 删除复用 `DELETE /api/library/voices/{id}?project_id=`（已拦成品引用/他项目占用；
  `project_voices.voice_id` 是 `ON DELETE CASCADE`）。
- `VoiceHistoryDialog` 用 `Modal fixed`，滚动交给 `.dialog-message` 的 flex，不自己钉高度；
  `.voice-history-row` 播放器需 `flex:1 1 auto; min-width:0`，否则撑破 462px。

## 按角色筛选（第 2 / 3 步，2026-10-29）

- `lib/roles.ts`：`ALL_ROLES` + `groupRoles`/`filterByRole`/`activeVoiceIdOfRole`/`resolveRole`；
  UI `components/ui/RoleSelect.tsx`（**自绘 `Select` 下拉单选，不是胶囊条**；`roles.length < 2` 不渲染）。
  首版胶囊条被当场否掉，别改回去。
- 下拉挂**卡片标题栏右侧**。计数胶囊**常驻 + `min-width:132px`**：文案在「共 8 条」↔「筛选 2 / 共 8 条」
  变宽变窄会把左边的下拉顶得横移一格。
- **分组键是 voice_name 不是 voice_id**（换版会冒两个同名选项）；组内 >1 id 或任一条 replaced →
  后缀「· 含换版」。
- `activeVoiceIdOfRole` 取名用 `display_name || name`，与 VoiceSelect 标签同源（清单 `name` 是原始名）。
- 联动：第 2 步 → 左侧「绑定声线」；第 3 步 → 「按人物批量返修」（`VoiceRegenPanel` 的
  `preferredVoiceId` + `appliedPreferred` ref 门控，别直接 effect 赋——voices 每次刷新是新引用）。
- **序号/统计不跟筛选走**：`position` 全局编号（跳号正常），「批量删除序号」按全局解析；统计措辞必须是
  「全局统计」，否则"已选入 7 却只有 2 行"会被当 bug。
- 筛选态不持久（换步骤回「全部」）；角色删空时回落「全部」并**复位 state**。

## 架构事实

- 后端只有 Qwen3-TTS `voice_design`（文→音）与 base `clone`（文→音）。**没有**翻译/STS/VC；
  跨语种配音可以 —— `language` 是独立入参，与声线来源无关。
- `voices.language` = 试听文本语种；`voices.ref_language` = 参考素材语种。别混。
- 上传页两按钮共用表单但朗读文本字段名不同：`/api/voices/upload` 叫 `preview_text`，
  `/api/tools/clone` 叫 `text`（clone 两个都收）。新增字段别假定同名。
- **两个「从声线库添加」语义不同，别合并**：第 2 步 `batch/AddLibraryVoice.tsx` +
  `POST /projects/{pid}/voices/attach` = 只写 `project_voices`；第 1 步 `voice/AttachLibraryVoices.tsx` +
  `POST /projects/{pid}/voice-requests/attach-library` = 建 `voice_requests`(`fixed`+`voice_id`) 并补
  `project_voices`。命名同时满足 `voice_requests UNIQUE(project_id,name)` 与 `project_voices` active 行
  `display_name` 唯一索引，撞了 `_2/_3` 递增。
- **「加入已有声线」只在项目内做**（2026-10-28）：「全部声线」页只留 查看/标签/删除；`/voices/attach`
  保留给上面两个项目内入口和新建项目向导。
- 无 Gradio（2026-10-20 删 `studio/ui.py`）。`python -m studio` ≡ `-m studio.api`（只起 7871）；
  入口 `start_studio.cmd` 或 `scripts/desktop_studio_launcher.ps1`。
- 导航 5 项：项目总览/工作台/全部声线/上传声线/系统状态（「操作记录」页 2026-10-28 删；`activity_log`
  表与 `log_activity()` 保留，只是没读接口）。
- `voice_requests.status`：`draft`→`ready`（prompt+audition_text 都填了自动升级）→`candidate`→`selected`
  →`fixed`；另有 `single`（单句成品，**非独占**，可与 selected/fixed 共存，`request_progress` 数的是
  `direct_output_mp3` 而非 status）。
- **候选「选定」是开关**：`POST .../{id}/select/{cid}` 选定，`.../{id}/deselect` 取消（清 `selected=1`、
  status 退回 `candidate`/`ready`，**不动 `voice_id`**）；`status='fixed'` 时 deselect 直接 400。
- 第 3 步「单句成品」盒子已删，但第 1 步候选卡保留按钮、成品页仍可看可删，所以 `outputs.origin='single'`
  与 `GET /api/projects/{id}/single-outputs` 都别顺手删。
- 设计声线两条入库路径：第 1 步固化 `_fix_selected_voices_locked`；全部声线页 `archive_designed_voice()`
  + `POST /api/voices/design-archive`。都写 `source='voice_design'`。固化必须用**刚试听的那一段**：
  文件名即后端生成的 `design_id`，重新生成会换随机 seed。
- 临时产物由 `studio/cleanup.py` 按 mtime 回收（`QWEN_TTS_TEMP_MAX_AGE_DAYS`，默认 7）。
- Token 门 `QWEN_TTS_AUTH_TOKEN`（空=不鉴权）；前端 `NEXT_PUBLIC_AUTH_TOKEN` 透传 `X-Auth-Token`。
- `lib/api.ts` 5 秒 GET 缓存，写操作后 `noteProjectWrite` 整体 clear；新写接口走 `apiSend`/`apiUpload`
  自动生效，跨页面读最新库存才显式 `force:true`。页面 keep-alive（`visitedRef` + `hidden`），切页不卸载。

## 布局与主题

- 四步共用骨架：`.step-stack`(gap 18) 是间距唯一来源；盒子只有 `.panel`（内容型 18px 内边距）与
  `.panel-flush`（列表型 padding 0，留白交给 `.panel-heading`）。`.products-panel` 是旧名、同组规则，
  **步骤 3 必须保留该类名**（`.products-panel .result-edit-row select` 挂在上面）。
- `.content-wrap` 必须 `width:100%`：`.main-panel` 是 flex 列，globals 遗留 `margin:0 auto` 会让
  `align-items:stretch` 失效、宽度塌成 fit-content。别给 flex 子项加交叉轴 auto margin。
- 外壳不整页滚：`.app-shell` 100dvh + overflow:hidden，`.content-wrap` 是唯一滚动容器；侧栏
  `flex:0 0 236px` + 内部滚动（改宽度要连 flex-basis 一起改）。
- header 用 flex + space-between；`.header-actions` 必须是 header 最后一个 DOM 子元素。
- 暗色：颜色全走 `theme.css` `:root` token，`:root[data-theme="dark"]` 覆盖 + `color-scheme:dark`；
  新增色必须用 token，`globals.css` 是旧暗色遗留别再往里加颜色。

## 版本 / 导出 / 删除

- 导出 = 成品库 `in_final=1`，同句多条取**最后被选中**。"同一句" = `(outputs.voice_name, 去空白 text)`；
  **没有台词表**，改标点=新的一句。
- 选中互斥在 `takes.demote_siblings`（5 处调用），新增"入成品库"路径**必须**带上。
- 三套"版本"别混：`candidates`（候选）/ `project_voices`（声线版本 `active|replaced`）/
  `outputs.version`（**同句重录次数**）。声线版本号由 `takes.voice_versions` 按 `display_name` +
  `created_at` 算。任务表 `v3` 是重录次数，**不是**声线版本。
- 换绑已删除（改 `outputs.voice_id` 不动音频 = 让历史说假话，换声音只能重录）。
- 换用历史版本 `POST /projects/{pid}/batch/{item_id}/use-version` = "复制成新的一版"（version+1、
  in_final=1、demote_siblings），**不是**指针回退；`VERSIONS_KEPT`=5。
- manifest 唯一权威在 `exports._build_manifest`；`lib/localExport.ts` 要抄全 12 字段，`app` 字段是
  "这份清单由我写"的凭据，清理过时 mp3 前必须认它。
- `backup_snapshot(label, assets=…)`：`None` 整库 / `{}` 只备 app.db / 字典只复制列出的目录；留最新 30 个。
  新增删除路径先想清楚会不会真删文件，别全量 copytree。
- 启动自举 `ProjectService.ensure_default_project()`，异常别抛进 `__init__`；`sqlite3.Error` 有全局处理器
  （IntegrityError→409，其余→500 + 日志）。
- 删成品行 = 删它的音频，走 `takes.discard_output_files`（只删 `<项目>/outputs` 之内）。
- 固化一条声线必须整体成功：复制文件 → GPU 建 prompt → 一个 `db.transaction()` 写完全部行，失败 rmtree
  新目录。上传落盘一律 `api._copy_upload()`（1MB 分块、超限删半成品）。

## 安全约定

"前端指定后端读哪个文件"的接口：路径段卡死 uuid 正则 + `Path.resolve().relative_to(root)` 双校验。
已落地 `/api/tools/conversions/archive`、`/api/voices/design-archive`。

## 验证与环境（实测）

- 后端测试：桩掉 `design`/`create_prompt` 后走 `fastapi.testclient`，数据目录用 `QWEN_TTS_DATA_DIR`
  指向 `app.db` 副本。真实数据在 `%LOCALAPPDATA%\QwenTTSVoiceoverStudio`。
- 前端视觉：CDP 截图（Node 22 内置 WebSocket 直连本机 Chrome，没有 puppeteer-core）。`--screenshot` 的
  virtual-time 会把入场动画冻在首帧；旧 profile 会缓存 `public/` 预览页。**量尺寸比看截图可靠**：
  用 `getBoundingClientRect` + `getComputedStyle` 打真实数值。
- **Agent shell 起后端必须清空 `PYTHONPATH`**：
  `PYTHONPATH= PYTHONUTF8=1 .venv/Scripts/python.exe -m studio.api --host 0.0.0.0 --port 7871`。
  否则加载 WorkBuddy 注入的 `sitecustomize` 删除拦截垫片，`backup_snapshot` 清理第 31+ 个快照时触发
  bulk-delete guard 抛 `SystemExit(1)` 带走 uvicorn（2026-10-26"删除历史声线"因此全接口 500 并退出）。
  用户双击 `start_studio.cmd` 不受影响。
- 后端未开 `--reload`，改完要重启；前端 3000，后端 7871。
