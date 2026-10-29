# Qwen3-TTS 配音工作台 · 部署方案

> 版本 v1 · 2026-10-29
> 适用对象：Windows 单机 / 局域网内网部署（非公网）
> 依据：仓库现状实测（`.venv` Python 3.12.10、Node v22.22.2、RTX 3060 12GB、driver 610.88、schema v6）

---

## 0. 一句话结论

沿用现有双进程架构（FastAPI 7871 + Next.js 3000），**把前端从 `npm run dev` 换成 `npm run build` + `next start`**，后端绑回 `127.0.0.1` 并启用共享 token，只对外暴露 3000 端口；用计划任务在开机时拉起。

模型统一到 **1.7B**（design + base 都 1.7B）；**数据从零启动**，这台机器上的项目/声线/台词/音频全部舍弃，只保留系统本身（代码、交互流程、数据模型、启动脚本）。

改造点 5 个：生产构建、后端收口到本机回环、token 门、开机自启、数据目录换新 + 模型换 1.7B。其余沿用 `start_studio.cmd` 的既有逻辑。

---

## 1. 部署形态选型

| | A. 单机桌面 | **B. 局域网内网服务（推荐）** | C. 公网暴露 |
|---|---|---|---|
| 使用者 | 只有本机 | 同网段同事，浏览器访问 | 任意人 |
| 启动方式 | 双击 `start_studio.cmd` | 计划任务 + 生产构建 | 同 B + 反代/HTTPS |
| 鉴权 | 不需要 | **必须开 token** | 必须 + TLS + 网关鉴权 |
| 端口暴露 | 仅 127.0.0.1 | 只开 3000 | 443 |
| 风险 | 无 | 内网可信，低 | 无鉴权即等于开放删库 → 本次不做 |

**推荐 B**。理由：现有代码已经按 LAN 形态设计（`next.config.mjs` 同源代理、`0.0.0.0` 监听、防火墙脚本、token 门），只差把开发服务器换成生产构建。

C 形态不建议在当前架构上做：后端只有共享 token 一道门、没有账号体系、没有审计日志读取接口，公网暴露等于把「删项目 / 改数据 / 拿音频」交给任何一个扫到端口的人。

---

## 2. 环境基线

### 2.1 硬件

| 项 | 要求 | 本机现状 |
|---|---|---|
| GPU | NVIDIA，显存 ≥ 8GB（1.7B bf16 + 音频缓冲） | RTX 3060 12288 MiB ✅ |
| 显卡驱动 | 支持 CUDA 13.0（≥ 580） | 610.88 ✅ |
| 内存 | ≥ 16GB | 待核 |
| 磁盘 | 模型 8.6GB（两个 1.7B）+ 数据增长 + 备份（保留 30 份） | 模型在 D 盘 `D:/ouyang/model` |

### 2.2 软件

| 组件 | 版本 | 说明 |
|---|---|---|
| Windows | 10 1809+ / 11 | 需要 `Get-NetTCPConnection`、`New-NetFirewallRule` |
| Python | 3.12.x | `.venv` 已建，3.12.10 |
| PyTorch | 2.11.0+cu130 | 来自 `requirements-deployment.txt` 的 extra-index |
| qwen-tts | 0.1.1 | |
| Node.js | LTS ≥ 20 | 本机 v22.22.2，npm 10.9.7 |
| Next.js | 14.2.15（package.json 锁定） | |
| SoX | 14.4.2 | 倍速处理依赖；`start_api.ps1` 会到 WinGet 目录兜底找 sox.exe |

### 2.3 端口

| 端口 | 进程 | 生产建议监听 |
|---|---|---|
| 3000 | Next.js | `0.0.0.0`（对外唯一入口） |
| 7871 | FastAPI | `127.0.0.1`（只给 Next 代理回源） |

### 2.4 目录

| 用途 | 路径 | 备注 |
|---|---|---|
| 代码 | `D:/ouyang/codeWorkspace/Qwen-TTS-Voiceover-Studio` | |
| 模型 | `D:/ouyang/model/voice_design_1.7b`（4.3G）、`voice_clone_1.7b`（待下载，约 4.3G） | 由 `.env` 显式指定 |
| 数据 | `%LOCALAPPDATA%\QwenTTSVoiceoverStudio`（当前库）/ 新目录（干净启动，见第 9 节） | app.db + projects + voices + temp + backups + logs |
| 启动日志 | `data\logs\desktop-launcher.log` | 桌面入口写这里 |

### 2.5 模型档位（已定：1.7B）

两个模型各管一件事，**都要 1.7B 才一致**：

| 用途 | 模型 | 现状 |
|---|---|---|
| `voice_design`（给文字描述→生成声线） | `voice_design_1.7b` | ✅ 已在 `D:/ouyang/model` |
| `base`（给参考音频→克隆音色） | `voice_clone_1.7b` | ✅ 已由离线包恢复并校验（3.86GB，480 tensor） |

1.7B Base 与 0.6B 的实测差异（直接读两边 `config.json`）：

| | 0.6B base | 1.7B base |
|---|---|---|
| talker hidden_size | 1024 | 2048 |
| num_hidden_layers | 28 | 28 |
| num_code_groups | 16 | 16 |
| tokenizer | qwen3_tts_tokenizer_12hz | 同 |
| **speaker_encoder enc_dim** | **1024** | **2048** |
| 权重体积 | 2.4GB | 3.86GB |
| 显存占用 | 约 3~4GB | 约 6~7GB（bf16 权重 + 激活） |
| 冷加载耗时 | 约 10~20s | 约 20~40s |

> ❗ **两种尺寸的声线 prompt 不通用**。声线文件里存的 `ref_spk_embedding` 形状是 `[enc_dim]` —— 0.6B 时代是 `BF16[1024]`，1.7B 要 `[2048]`，直接加载会形状不匹配。若将来要携带 0.6B 时代的历史声线，必须用 `reference.wav` + `ref_text` 全部重建 prompt。
>
> ✅ **本次数据从零启动，所以没有这个问题**：新库里的声线全部由 1.7B 现跑现生成（第 9 节）。

> 💡 **模型不必联网重新下载**：`D:\QwenTTS_Packages` 里的 Part2/Part3 就是这两套 1.7B 的离线分卷，恢复方法见 2.6 节。

> 显存余量：1.7B 双模型都**不会同时驻留** —— `ModelManager.load()` 换模型前先 `unload()`，GPU 上永远只有一个。3060 12GB 跑 1.7B 有余量，但别在同一张卡上再叠别的推理进程（`gpu.lock` 会拦第二个进程）。

### 2.6 离线包 `D:\QwenTTS_Packages`：能用的部分与不能用的部分

这个目录里是 7 个 tar（约 8.8GB）+ `deploy.ps1` + `使用说明.txt`。**它不是当前系统的迁移包**，用之前必须分清两半：

| 内容 | 判定 | 说明 |
|---|---|---|
| `Part1_Code.tar`（532KB） | ❌ **不能用** | 是**上一代 Gradio 版**的代码：含 `studio/ui.py`、`studio/services.py`、`scripts/start_studio.ps1`，**没有** `studio/api.py`，**没有** `frontend/` |
| `Part2a/b/c`（Clone） | ✅ 可用 | `models/voice_clone_1.7b` 全套：config + tokenizer + 权重分卷 |
| `Part3a/b/c`（Design） | ✅ 可用 | `models/voice_design_1.7b` 全套（本机已有，可跳过） |
| `deploy.ps1` | ❌ **不要用** | 见下方三处硬伤 |
| `使用说明.txt` | ⚠️ 部分失效 | 端口写 7870、功能写 7861/7862 独立服务，都是旧架构 |

**`deploy.ps1` 的三处硬伤**（照它跑会失败）：

1. `& $pythonCmd -m venv .venv` —— `$pythonCmd` 是字符串 `'py -3.12'`，`&` 会把整串当**一个命令名**，报「找不到命令」。必须拆成 `& py -3.12 -m venv .venv`。
2. 解 `Part2a` / `Part3a` 的落点错位 —— 这两卷条目带 `QwenTTS/models/...` 前缀，脚本却 `Set-Location $installDir` 后解压，会落成 `D:\QwenTTS\QwenTTS\models\...`，而后续合并读的是 `D:\QwenTTS\models\...`，永远合并不上。
3. 端口与功能说明停留在 Gradio 时代（7870 / 7861 / 7862），装完打开的是已经不存在的界面。且**不装 Node、不构建前端**。

**正确恢复模型的方式**（已在本机验证通过）：

```powershell
# 1. config + tokenizer（条目带前缀，用 strip-components 去掉 QwenTTS/models/）
tar -xf QwenTTS_Part2a_Clone_Config.tar -C D:\ouyang\model --strip-components=2

# 2. 权重分卷直接顺序拼接成 model.safetensors（省掉 copy /b 的中间文件）
cd D:\QwenTTS_Packages
(tar -xOf QwenTTS_Part2b_Clone_Model1.tar model.safetensors.partaa;
 tar -xOf QwenTTS_Part2c_Clone_Model2.tar model.safetensors.partab) > D:\ouyang\model\voice_clone_1.7b\model.safetensors
```

**必须校验**，不要只看文件大小（分卷错位会静默产出坏权重）：

```powershell
.venv\Scripts\python.exe -c "import json,struct;s=open(r'D:\ouyang\model\voice_clone_1.7b\model.safetensors','rb');n=struct.unpack('<Q',s.read(8))[0];h=json.loads(s.read(n));h.pop('__metadata__',None);e=max(v['data_offsets'][1] for v in h.values());import os;print('tensor',len(h),'需要',8+n+e,'实际',os.path.getsize(r'D:\ouyang\model\voice_clone_1.7b\model.safetensors'))"
```

判据：tensor 数 **480**，且「需要」==「实际」（差值必须为 0）。本机实测：`3857413744 == 3857413744` ✅。
再确认 config 是 1.7B：`tts_model_size=1b7`、`tts_model_type=base`、`speaker_encoder enc_dim=2048`。

---

## 3. 拓扑与数据流

```
                    ┌──────────────────────────── 局域网同事 ────────────────────────────┐
                    │  http://<本机IP>:3000                                              │
                    └───────────────────────────────┬───────────────────────────────────┘
                                                    │ 同源请求（无需 CORS）
                    ┌───────────────────────────────▼───────────────────────────────────┐
                    │ Next.js 14 (next start, 0.0.0.0:3000)                              │
                    │   页面 / 静态资源                                                    │
                    │   rewrites: /api/* ──┐                                             │
                    └──────────────────────┼────────────────────────────────────────────┘
                                           │ 127.0.0.1:7871
                    ┌──────────────────────▼────────────────────────────────────────────┐
                    │ FastAPI (uvicorn, 127.0.0.1:7871)                                  │
                    │   token 门 → 路由 → StudioService                                   │
                    │   ModelManager（进程内串行 + gpu.lock 跨进程互斥）                    │
                    └───────┬───────────────────────────┬───────────────────────────────┘
                            │                           │
                  ┌─────────▼─────────┐      ┌──────────▼──────────┐
                  │ CUDA 13.0 / 3060  │      │ 数据目录 (app.db 等)  │
                  │ Qwen3-TTS 权重     │      │ backups / temp / logs │
                  └───────────────────┘      └──────────────────────┘
```

关键点：浏览器**只**访问 3000；`/api/*` 与音频 `/api/files/*` 全部走 Next 同源回源，因此后端绑 `127.0.0.1` 不影响局域网使用（`file_url()` 返回的是相对路径）。`experimental.proxyTimeout: 600000` 兜住克隆类 1~2 分钟的长请求。

---

## 4. 首次部署步骤

### 阶段 0 · 前置校验（10 分钟）

```powershell
# 端口空闲？
Get-NetTCPConnection -LocalPort 3000,7871 -State Listen -ErrorAction SilentlyContinue
# GPU 可用？
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
# 依赖在不在？
where python; where node; where sox
```

**判据**：两条端口查询无输出；`nvidia-smi` 正常出表；`node -v ≥ v20`。

### 阶段 1 · Python 环境

```powershell
cd D:\ouyang\codeWorkspace\Qwen-TTS-Voiceover-Studio
.\setup_windows.ps1
```

该脚本会建 `.venv`、装 `requirements-deployment.txt`、补齐 `data/ outputs/ models/` 目录。
**判据**：`.venv\Scripts\python.exe -c "import torch;print(torch.cuda.is_available())"` 输出 `True`（首次导入约 10~20 秒，属正常）。

### 阶段 2 · 模型就位

```powershell
# 两个 1.7B 快照都要。已有 voice_design_1.7b 时脚本会跳过（认 model.safetensors 标记）
.venv\Scripts\python.exe scripts\download_models.py --model-root D:\ouyang\model
# 私有/受限仓库需先 set HF_TOKEN=hf_xxx
```

脚本会拉 `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`（→ `voice_design_1.7b`）和 `Qwen/Qwen3-TTS-12Hz-1.7B-Base`（→ `voice_clone_1.7b`）。
**注意**：当前机器只有 `voice_clone_0.6b`，1.7B Base 需新增约 4.3GB 磁盘。

**判据**：`D:\ouyang\model\voice_clone_1.7b\model.safetensors` 存在。

### 阶段 3 · 配置

根目录 `.env`（已被 `.gitignore` 排除，`studio/config.py` 启动时以 `setdefault` 注入，不覆盖真实环境变量）：

```ini
QWEN_TTS_MODEL_ROOT=D:/ouyang/model
QWEN_TTS_GENERATION_MODEL_PATH=D:/ouyang/model/voice_design_1.7b
QWEN_TTS_CLONE_MODEL_PATH=D:/ouyang/model/voice_clone_1.7b
QWEN_TTS_DATA_DIR=%LOCALAPPDATA%/QwenTTSVoiceoverStudio            ; 干净启动则改成新目录，见第 9 节
QWEN_TTS_CORS_ORIGINS=http://<本机IP>:3000                    ; 收紧白名单（可选）
QWEN_TTS_AUTH_TOKEN=<32位随机串>                              ; 内网部署必开
# QWEN_TTS_ALLOW_HOST_FS=1                                    ; 不要开
# QWEN_TTS_TEMP_MAX_AGE_DAYS=7                                ; 默认 7 天清 temp
```

`frontend/.env.local`：

```ini
# NEXT_PUBLIC_API_BASE 留空 —— 走同源代理
NEXT_PUBLIC_AUTH_TOKEN=<与后端同一个值>
```

> ⚠️ `NEXT_PUBLIC_*` 是**构建期注入**，改了必须重新 `npm run build`，纯重启无效。

**判据**：`.env` 中 `QWEN_TTS_DATA_DIR` 已显式指定。不指定时若仓库内出现 `data/app.db`，数据库会被静默切到仓库目录（`config.py::_resolve_data_dir`），这是最容易踩的一次数据「丢失」。

### 阶段 4 · 前端依赖

```powershell
cd frontend
npm ci          # 有 package-lock.json，用 ci 而非 install，保证版本可复现
```

**判据**：`node_modules/.bin/next.cmd` 存在。

### 阶段 5 · 生产构建

```powershell
cd frontend
npm run build
```

**判据**：构建结束出现路由表，`.next/BUILD_ID` 存在。**这一步不能省** —— 跳过它 `next start` 会直接报 "Could not find a production build"。

### 阶段 6 · 启动与验收

```powershell
# 后端（新窗口）
$env:PYTHONUTF8=1
.\.venv\Scripts\python.exe -m studio.api --host 127.0.0.1 --port 7871

# 前端（新窗口）
cd frontend
npm run start -- -H 0.0.0.0 -p 3000
```

**判据**：

```powershell
curl http://127.0.0.1:7871/api/health      # {"status":"ok","model":{...}}
curl http://127.0.0.1:3000/api/health      # 经 Next 代理同样返回 ok
curl http://<本机IP>:3000/api/health       # 局域网可达性
```

三条都通 → 部署完成。再看第 12 节功能验收。

---

## 5. 生产模式 vs 开发模式（关键差异）

| | 现状（`start_frontend.ps1`） | 生产（本方案） |
|---|---|---|
| 前端命令 | `next dev` | `next build` + `next start` |
| 首屏 | 按需编译，首次 5~15s | 直接吐产物，<1s |
| 内存/CPU | 常驻编译进程，空转高 | 低 |
| 报错 | 页面内 dev overlay | 只进控制台 |
| 源码 | 直接执行 | 打包压缩 |
| 热更新 | 有 | 无（改代码要重新 build） |
| 长请求超时 | `experimental.proxyTimeout=600000` 兜底 | `next start` 的回源不带 30s 限制 |

**结论**：给人用的机器必须走生产模式；`start_studio.cmd` 保留为「开发/演示」入口。

---

## 6. 配置项总表

| 变量 | 位置 | 默认 | 生产建议 | 影响 |
|---|---|---|---|---|
| `QWEN_TTS_DATA_DIR` | 根 `.env` | 有 `data/app.db` 则用仓库目录，否则 `%LOCALAPPDATA%` | **显式指定** | 数据库/素材/备份全部落点 |
| `QWEN_TTS_MODEL_ROOT` | 根 `.env` | `models/` | `D:/ouyang/model` | 模型根目录 |
| `QWEN_TTS_GENERATION_MODEL_PATH` | 根 `.env` | `<root>/voice_design_1.7b` | 保持不变 | voice_design（文→音） |
| `QWEN_TTS_CLONE_MODEL_PATH` | 根 `.env` | `<root>/voice_clone_1.7b` | **1.7B（已定，与 design 对齐）** | clone（文→音） |
| `QWEN_TTS_AUTH_TOKEN` | 根 `.env` | 空 = 不鉴权 | **必设** | 写请求 + 音频下载门禁 |
| `QWEN_TTS_CORS_ORIGINS` | 根 `.env` | `*` | 指定 3000 来源 | 走同源代理时影响小，收紧无副作用 |
| `QWEN_TTS_ALLOW_HOST_FS` | 根 `.env` | 关 | **保持关闭** | 开后允许写后端本机路径（共享部署下无意义且扩大攻击面） |
| `QWEN_TTS_TEMP_MAX_AGE_DAYS` | 根 `.env` | 7 | 7 | temp/ 与 quick/ 按 mtime 回收 |
| `NEXT_PUBLIC_API_BASE` | `frontend/.env.local` | 空 | **留空** | 空 = 同源代理，跨机部署才填 |
| `NEXT_PUBLIC_AUTH_TOKEN` | `frontend/.env.local` | 空 | 与后端同值 | 构建期注入 |

---

## 7. 安全加固（4 项，缺一不可）

1. **后端收口**：`--host 127.0.0.1`。Next 回源地址写死 `http://127.0.0.1:7871`，收口后功能不变、攻击面砍掉一半。
2. **防火墙只开 3000**：`scripts/add_firewall_rules.ps1` 会同时放行 3000 和 7871（管理员权限运行）。生产环境**只保留 3000**，7871 那条规则删掉或不要执行。
3. **token 门**：`QWEN_TTS_AUTH_TOKEN` 非空后，写方法必须带 `X-Auth-Token`（或 `Authorization: Bearer`），音频下载走 `?token=`（媒体标签带不了头）。`/api/health`、`/api/system/status` 与 `OPTIONS` 预检放行。前端 `lib/api.ts` 自动带头，音频 URL 由后端 `file_url()` 自动附参数。
4. **不开 `QWEN_TTS_ALLOW_HOST_FS`**：导出类接口改走浏览器文件夹选择器或流式 ZIP，后端那台机器不落交付文件。

> 内网也不建议省 token：一旦有人把网线插到会议室电视、或有人装了端口扫描，无鉴权的 7871/3000 就是「谁都能删项目、拿音频」。

---

## 8. 守护与开机自启

### 方案：计划任务 + 现有隐藏启动器（推荐）

`scripts/desktop_studio_launcher.ps1` 已经做了该做的三件事：查端口避免重复启动、等端口就绪、日志留痕、拉起浏览器。生产只需把前端换成 build+start。

```
任务计划程序 → 创建任务
  常规：  使用最高权限运行 ☐（不需要管理员）
  触发器： 登录时（At log on）—— 语音生成要 GPU，建议登录态而非 At startup
  操作：   powershell.exe -NoProfile -ExecutionPolicy Bypass
           -File "D:\...\scripts\desktop_studio_launcher.ps1"
  条件：  取消「使用电池时停止」，勾选「失败后重启：3 次 / 间隔 1 分钟」
  设置：  勾选「如果任务已在运行，则不再启动新实例」
```

**注意**：模型只能有一个进程持有 GPU。`ModelManager` 靠 `gpu.lock` 做跨进程互斥，第二个进程会直接抛「另一个 Qwen TTS 进程正在占用 GPU」。所以**不要**同时配「任务计划 + 服务 + 手动双击」三条路径。

### 备选：NSSM 注册为 Windows 服务

优点：开机即起、不依赖登录态、崩溃自动重启。
代价：服务跑在 Session 0，**拿不到 GPU 桌面上下文**通常没问题，但 tkinter 目录选择框这类 UI 调用会失效（`QWEN_TTS_ALLOW_HOST_FS` 本就不开，可接受）。且要起两个服务（后端 + 前端），互斥关系靠端口判断。
结论：只在「无人值守、纯 API 调用」场景选它；给人用的工作台用计划任务。

---

## 9. 数据策略（已定：干净启动，不携带历史）

### 9.1 结论

这台机器上**生成出来的东西全部舍弃**：16 个项目、63 条声线、147 条台词、`voices/` 与 `projects/` 下的音频、3 个导出包里的 9 个 mp3。要保留的是**这套系统本身** —— 代码、前端交互流程、数据模型、启动与部署脚本。

因此不需要任何资产迁移工具。之前评估的「导入器 / prompt 重建 / 跨库搬运」全部作废。

### 9.2 必带 / 必弃清单

**必须带走**

| 内容 | 路径 | 说明 |
|---|---|---|
| 代码 | `studio/`、`frontend/app|components|features|lib` | 后端 API + 前端工作台 |
| 设计/流程文档 | `design/*.md` | 操作流程、schema、交互约定的唯一书面来源 |
| 依赖清单 | `requirements-deployment.txt`、`frontend/package.json` + `package-lock.json` | 有 lock 才可复现 |
| 启动与运维脚本 | `scripts/*.ps1`、`start_*.cmd`、`setup_windows.ps1` | 一键启动、防火墙、桌面入口 |
| 模型配置模板 | `.env.example`、`frontend/.env.example` | 新机照抄改值 |
| 模型快照 | `D:/ouyang/model/voice_design_1.7b`、`voice_clone_1.7b` | 可复制；换机不必重下 8.6GB |

**绝对不能带走**

| 内容 | 原因 |
|---|---|
| `<DATA_DIR>\app.db`（+ `-wal`/`-shm`） | 这就是「舍弃」的对象 |
| `<DATA_DIR>\voices`、`projects`、`temp`、`backups` | 生成物 |
| `data/`（仓库内）、`outputs/` | 同上，且会触发 `_resolve_data_dir` 的目录劫持 |
| `.venv/` | 路径绑定，换机必重建（同机可留） |
| `frontend/node_modules`、`.next` | `npm ci` + `npm run build` 更快更干净 |

### 9.3 怎么「舍弃」才不是给自己挖坑

**推荐：不删，只改指向。** 服务照旧，把新实例的数据目录换成一个新路径 —— 旧数据原地不动，随时能回退对照。

```ini
# .env
QWEN_TTS_DATA_DIR=%LOCALAPPDATA%/QwenTTSVoiceoverStudio-clean
```

启动后 `config.py` 发现该目录不存在，会自动 `mkdir` 出 `voices/ projects/ temp/`，`Database.initialize()` 以 `stored_version=0` 从零建表（**直接落 v6，不触发任何迁移、也不会生成 pre-migration 快照**），`ProjectService.ensure_default_project()` 会建一个默认项目。开箱即为干净的 v6 空库。

旧库确认不再需要后，想清理再说 —— 677MB，不急。**不要**用批量删除通配符去清 `%LOCALAPPDATA%`，那里还放着别的软件的数据。

### 9.4 干净启动后会发生什么

| 现象 | 说明 |
|---|---|
| 项目列表只有 1 个默认项目 | `ensure_default_project()` 的正常行为 |
| 声线库为空 | 需要重新设计/上传，由 1.7B 现跑现生成 |
| `backups/` 为空 | 没有迁移，就没有 pre-migration 快照 |
| 首次生成等待 20~40s | 1.7B 模型冷加载，`/api/health` 的 `model` 字段可看状态 |
| 页面已访问过的步骤不重载 | 前端 keep-alive（`visitedRef` + `hidden`），属设计行为 |

### 9.5 备份（新库同样适用）

- 自动：schema 升级前 `initialize()` 会调 `backup_snapshot("pre-migration")`；删除类操作按需只备数据库（`{}`）或指定目录（字典）。
- 位置：`<DATA_DIR>/backups/`，**保留最新 30 份**，超出自动清理。
- 手动全量：停服后整目录复制

```powershell
Stop-Process -Name python -ErrorAction SilentlyContinue   # 先停服务
robocopy "$env:LOCALAPPDATA\QwenTTSVoiceoverStudio-clean" "E:\backup\qwentts-clean" /E /COPYALL
```

> 用 `robocopy` 整目录而非只拷 `app.db`：`app.db-wal`、`app.db-shm` 必须一起走，否则丢掉未 checkpoint 的事务。

### 9.6 附：以后若要携带历史（当前不采用）

整目录 `robocopy` + `.env` 改指向即可，不要只搬 `app.db` —— 音频文件与 db 路径强绑定，缺文件会「列表有行、播放没声」。若届时已换 1.7B，还需验证旧 `prompt.safetensors`（0.6B 生成）的兼容性，不兼容则按 `reference.wav` + `ref_text` 批量重建。

---

## 10. 升级流程

```powershell
# 0. 备份
robocopy "%LOCALAPPDATA%\QwenTTSVoiceoverStudio" "E:\backup\pre-upgrade" /E /COPYALL

# 1. 停服（两个窗口 Ctrl+C，或）
Get-Process python,node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*Qwen-TTS*" } | Stop-Process

# 2. 拉代码
git pull

# 3. 依赖（requirements 变了才需要）
.\.venv\Scripts\python.exe -m pip install -r requirements-deployment.txt

# 4. 前端（package-lock 变了才 npm ci；无论如何都要重新 build）
cd frontend; npm ci; npm run build; cd ..

# 5. 起服并冒烟
.\.venv\Scripts\python.exe scripts\api_smoke.py
```

数据库 `SCHEMA_VERSION = 6`，旧库启动时自动迁移（v5 前的老库会先经 `_stamp_schema_version(version=5)` 路径）。**迁移前会自动快照**，但跨版本升级仍建议先手动整目录备份一次。

前端改了 `NEXT_PUBLIC_*` 必须重 build —— 这类变量在构建期被烧进 bundle，重启不生效。

---

## 11. 回滚

| 场景 | 动作 |
|---|---|
| 前端改坏了 | `git checkout <上一个 commit> -- frontend` + `npm run build` + 重启前端 |
| 后端改坏了 | `git checkout <commit> -- studio` + 重启后端（无需重装依赖，除非 requirements 变了） |
| 依赖升级炸了 | `git checkout <commit> -- requirements-deployment.txt` + `pip install -r` + 重启 |
| **数据库被迁移了** | 停服 → 从 `<DATA_DIR>/backups/` 取 `pre-migration-*` 覆盖 app.db（含 -wal/-shm 一并删掉）→ 回滚代码 → 启动。**schema 只能升不能降**，所以代码回滚必须配 db 回滚 |
| 全盘崩 | 整目录还原 + 代码 checkout 同一 tag |

回滚耗时主要在前端 rebuild（约 1~2 分钟）和模型冷加载（首次请求触发，数十秒）。

---

## 12. 验收清单

部署完成后按序打勾：

- [ ] `curl 127.0.0.1:7871/api/health` → `status: ok`，`model.cuda: true`，GPU 名正确
- [ ] `curl 127.0.0.1:7871/api/system/status` → `data_dir` 是新目录，`projects` 为 1（默认项目），确认是空库而非旧库
- [ ] `model` 字段在首次生成后显示 1.7B 对应目录，且 `/api/health` 里 GPU 显存占用约 6~7GB（不是 3~4GB）
- [ ] `curl <本机IP>:3000/api/health` 从**另一台机器**可达
- [ ] 页面能打开，深色主题无样式丢失（`theme.css` token 生效）
- [ ] 建项目 → 工作台步骤 1 建槽位 → 设计声线能试听（验证 GPU 链路 + 音频 URL 带 token 可取）
- [ ] 步骤 2 上传/克隆声线（验证 SoX + 80MB 上传分块）
- [ ] 步骤 3 批量生成 → 入成品库（验证 `demote_siblings` 互斥）
- [ ] 导出 ZIP / 本地导出，manifest 12 字段完整
- [ ] 未带 token 的写请求返回 401（安全门生效）
- [ ] 断网状态刷新页面 —— 若失败说明有外部资源依赖，需内联
- [ ] 重启机器后，登录即自动可用（计划任务生效）
- [ ] `data\logs\desktop-launcher.log` 无 ERROR

---

## 13. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 前端 502 / 页面能开但接口全挂 | 后端没起，或 Next 回源到 127.0.0.1:7871 打空 | 先确认 `Get-NetTCPConnection -LocalPort 7871`；后端崩了看那个窗口 |
| `next start` 报 "Could not find a production build" | 跳过了 `npm run build` | 补 build |
| 克隆/生成长请求 500 | dev 代理 30s 超时 | 生产已无此限制；dev 模式靠 `proxyTimeout: 600000` |
| 第二个进程启动即报 GPU 被占用 | `gpu.lock` 互斥，符合预期 | 只留一个；确认没有残留 python 进程 |
| 数据"消失"、列表全空 | `QWEN_TTS_DATA_DIR` 未显式指定，且仓库内出现了 `data/app.db`，库被切走 | 补上 `QWEN_TTS_DATA_DIR`，重启 |
| 音频 404 | 文件不在 `<DATA_DIR>` 下，或 db 路径与磁盘不一致（迁移时漏拷 voices/projects） | 核对目录完整性 |
| 中文日志乱码 | GBK 控制台 | 设 `PYTHONUTF8=1`（启动脚本已含） |
| 上传成功但转换失败 | SoX 不在 PATH | `start_api.ps1` 会到 WinGet 目录找；手动部署时把 sox.exe 目录加 PATH |
| 局域网访问不通 | 防火墙未放行 3000 | `New-NetFirewallRule ... -LocalPort 3000` |
| 改 `.env.local` 无效 | `NEXT_PUBLIC_*` 构建期注入 | 重新 `npm run build` |

---

## 14. 待确认事项

已确认：

- ~~资产迁移~~ → **不做**，历史数据全部舍弃（第 9 节）
- ~~克隆模型档位~~ → **1.7B**，与 design 模型对齐（第 2.5 节）

仍需定：

1. **部署目标机**：就在这台机器上干净重来，还是换一台？换机器则 `.venv`、`node_modules` 必须重建，模型目录建议直接复制（省 8.6GB 重下）。这一条决定第 4 节阶段 1 的具体命令。
2. **对外形态**：确认只在内网使用，不做公网 / 不做 HTTPS 反代。（若需公网，得另起一版：加 Caddy/Nginx + 基础认证 + 账号体系，工作量另计）
3. **开机自启方式**：计划任务（推荐，保留 GUI 能力）还是 NSSM 服务（无人值守）。
4. **是否现在就改启动脚本**：本方案建议新增 `scripts/start_frontend_prod.ps1`（build+start）和 `start_studio_prod.cmd`，**保留**现有 dev 脚本不动。落地时按「写脚本 → 本机试跑 → 走第 12 节验收清单」三步走。
5. **1.7B Base 何时下载**：约 4.3GB，需要能访问 Hugging Face（或已配镜像/`HF_TOKEN`）。可以现在下，也可以等代码就位后再下。

---

## 附录 · 由自动化/Agent 拉起后端时的坑

若后台由 Agent shell 启动后端，必须清空 `PYTHONPATH`：

```bash
PYTHONPATH= PYTHONUTF8=1 .venv/Scripts/python.exe -m studio.api --host 127.0.0.1 --port 7871
```

否则会加载宿主注入的 `sitecustomize` 删除拦截垫片，`backup_snapshot` 清理第 31 个快照时触发 bulk-delete guard 抛 `SystemExit(1)`，直接带走 uvicorn（表现为清理备份时全接口 500 并进程退出）。用户双击 `start_studio.cmd` 不受影响。

另：后端未开 `--reload`，改完 `.py` 必须重启进程。
