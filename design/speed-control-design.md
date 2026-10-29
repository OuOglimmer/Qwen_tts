# 成品导出 · 语速控制方案

> 需求形态：**在成品导出面板加一个语速选项，导出的音频按该倍速加速；源文件一个字节都不改。**
> 等价于以前用剪映「变速」再导出的做法，但内建在导出流程里，一步完成。

结论：可以实现，**不需要重跑 TTS、不需要改数据库、不需要新增依赖**。

## 1. 为什么这个形态最省

上一版考虑过「生成时变速」和「成品重处理」，都要写 `outputs.speed`、做母版追溯、
重压后还要维护版本快照。导出时变速把这些全绕开了：

| | 生成时变速 | 导出时变速（本方案） |
|---|---|---|
| 改数据库 | 要（3 列 + v7 迁移） | 不要 |
| 重跑 GPU | 存量要 | 不要 |
| 源文件 | 被替换 | 只读，永不修改 |
| 想换倍率 | 再走一遍重压/重录 | 改个数字再导一次 |
| 回滚 | 靠版本快照 | 原文件本来就在，天然可回滚 |

代价是：语速只体现在**导出副本**上，成品库内试听仍是原速（用播放器倍速预览解决，见 §6）。

## 2. 技术验证（已实测）

环境自带 ffmpeg 7.1（`imageio-ffmpeg`），`atempo` 滤镜可用，无需新依赖。
`atempo` 是 WSOLA 时间伸缩，**保持音高**，用真实候选音频实测：

| 倍率 | 时长 | 基频中位数 |
|---|---|---|
| 1.00 | 5.036 s | 338.0 Hz |
| 1.15 | 4.389 s | 338.0 Hz |
| 1.25 | 4.048 s | 338.0 Hz |
| 1.40 | 3.609 s | 338.0 Hz |

流式输出（`-f mp3 pipe:1`）实测：单条 **66 ms / 65 KB**，MP3 头正常、时长正确。
100 条串行导出 ≈ 7 秒，浏览器逐条写盘的速度与它同量级，不会成为瓶颈。

## 3. 三条导出路径各自怎么变速

前端有三条导出通道，必须**全部**支持，否则用户换条路就得到原速文件。

```
① 浏览器直写本机文件夹（主力）
   export-plan 返回的 url 挂 ?speed=1.25 → 浏览器 fetch 拿到变速 mp3 → 写入
        └─ 由 /api/files 流式现算，不落临时文件

② 全部成品 ZIP（降级路径，浏览器不支持写盘时用）
   archive.zip?speed=1.25 → 后端逐条变速到临时目录 → 打包 → 临时目录自动清理

③ 后端目录导出（单机自用）
   POST /outputs/export {speed} → ffmpeg 直接写目标文件
```

### 关键实现选择：复用 `/api/files` 通道

不新建接口，给现有文件通道加一个可选 `speed` 参数：

```python
@app.get("/api/files/{file_path:path}")
def download_data_file(file_path: str, speed: float = Query(default=1.0, ge=0.5, le=2.0)):
    target = (_DATA_DIR_RESOLVED / file_path).resolve()
    # ……现有安全校验原样保留（数据目录内 / 非 backups / 仅音频）……
    if abs(speed - 1.0) < 1e-6:
        return _audio_response(target)          # 原路径，字节级不变
    return StreamingResponse(
        tempo_stream(target, speed),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
```

好处：
- 路径安全校验（`relative_to` + backups 排除 + MEDIA_TYPES 白名单）一行都不用重写；
- `file_url()` 生成逻辑复用，前端只在末尾追 `&speed=`；
- 不带 `speed` 的**所有现有调用完全不受影响**。

## 4. 后端改动

### 4.1 `studio/audio.py`（新增，不动现有函数）

```python
def check_speed(speed: float) -> float:
    """收敛到 [0.5, 1.5]，非数字/越界抛 ValueError。"""

def tempo_filter(speed: float) -> str:
    """返回 atempo 表达式。>2 倍按倍数串联（本功能用不到，兜底）。"""

def tempo_stream(source: Path, speed: float) -> Iterator[bytes]:
    """ffmpeg 边转边吐。客户端断开时 kill 子进程，别留僵尸。"""

def tempo_to_file(source: Path, destination: Path, speed: float) -> Path:
    """直接落盘。调用方必须保证 destination != source。"""
```

`tempo_stream` 的进程管理是本方案唯一有"资源泄漏"风险的地方：

```python
proc = subprocess.Popen([...], stdout=PIPE, stderr=PIPE, creationflags=CREATE_NO_WINDOW)
try:
    for chunk in iter(lambda: proc.stdout.read(65536), b""):
        yield chunk
finally:
    if proc.poll() is None:
        proc.kill()          # 浏览器中途取消导出
    proc.wait()
```

### 4.2 `studio/exports.py`

```python
def _build_manifest(self, rows, excerpt_length, speed=1.0):
    # 每条多一个 "speed": round(speed, 3)
    # speed=1.0 也照写：manifest 自解释，下游不必猜这批是原速还是变过

def export_plan(self, project_id, excerpt_length=8, speed=1.0):
    # 返回值多一列：files[i] 里带 speed，由 api 层拼进 url

def export_completed_batch(self, project_id, destination, excerpt_length=8, speed=1.0):
    # speed==1.0 → shutil.copy2（原逻辑）
    # speed!=1.0 → tempo_to_file(source, target, speed)
    #   前置断言：target.resolve() != source.resolve()，否则拒绝 —— 见 §7 头号风险

def iter_archive(self, entries, paths, speed=1.0):
    # speed==1.0 → 原逻辑（bundle.write 源文件）
    # speed!=1.0 → TemporaryDirectory 里逐条 tempo_to_file → 写进 zip → 立即删
    #   TemporaryDirectory 用 with 包住整个 yield 过程，流式期间有效，结束自动清理
```

**效率细节**：不要"先全部压缩完再打包"，而是压一条 → 写进 zip → 删临时文件，
峰值磁盘只占一条的大小。

### 4.3 `studio/api.py`

```python
class ExportPayload(BaseModel):
    destination: str = Field(min_length=1)
    excerpt_length: int = Field(default=8, ge=5, le=10)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)   # 新增

POST /api/projects/{id}/outputs/export          # 读 payload.speed
GET  /api/projects/{id}/outputs/archive.zip     # 加 ?speed=
GET  /api/projects/{id}/outputs/export-plan     # 加 ?speed=，返回的 url 已挂 &speed=
GET  /api/files/{path}                          # 加 ?speed=，见 §3
```

`export-plan` 拼 url 时注意 `file_url()` 在开启鉴权时已经带 `?token=`，
追加参数必须用 `&` 而不是 `?`。

## 5. 前端改动

| 文件 | 改动 |
|---|---|
| `features/products/ProductsPage.tsx` | 导出设置面板加「导出语速」；`exportAll` 传 speed；ZIP 链接带 `&speed=` |
| `lib/localExport.ts` | `exportToDirectory(projectId, excerptLength, speed, onProgress)`；manifest 同步写 `speed` |
| `components/ui/AudioPlayer.tsx` | 新增 `rate?: number`，赋值到 `el.playbackRate` |
| `lib/types.ts` | `ExportPlanFile.speed` |

### manifest 字段必须两边同步（老坑）

`_build_manifest` 和 `lib/localExport.ts` 的 `written.map(...)` 是**两份手抄的同一张表**。
加 `speed` 时两处都要加，漏一处就会让「浏览器导出」与「ZIP 导出」的清单不一致。
（代码注释里已经写过这条约定，照做即可。）

## 6. UI：加在哪、长什么样

位置：第 4 步「成品导出」的 `⚙ 导出设置` 面板，紧挨现有「文件名取前 N 字」。

```
⚙ 导出设置：成品写入你自己电脑的文件夹
  文件名取前 [8] 字
  导出语速  [0.9×] [1.0×] [1.15×] [1.25×] [1.4×] [1.5×]      ← 新增
  ⓘ 只影响本次导出的副本，成品库里的原文件不会改动。
     加速时音调不变；1.4 倍以上短句可能显得赶，建议先试听。
  [导出到本机文件夹…]  [下载全部成品 ZIP]
```

**试听预览（这版的核心体验）**：把当前倍率传给成品列表的 `AudioPlayer`，
列表里点播放听到的就是导出效果。浏览器的 `playbackRate` 同样保持音高，
听感与 `atempo` 足够接近，可以先听再决定，避免"导完才发现太赶"。

档位选择：`0.9 / 1.0 / 1.15 / 1.25 / 1.4 / 1.5`。上限 1.5 —— 再快短句会有吞字的金属感。
用分段按钮而非滑杆，符合「播放倍速」的直觉且不会误拖。

倍率选择存 `localStorage`，下次进页面还是上次的值（这是导出参数，不该每次重设）。

## 7. 边界与风险

| 风险 | 处理 |
|---|---|
| **导出目录 = 成品源目录** | speed≠1 时 ffmpeg 会直接覆盖源 mp3，母本当场没了。**必须断言 `target.resolve() != source.resolve()`，命中就报错**「导出目录不能是成品存放目录」 |
| 倍率作用在整条时间轴 | 句间停顿一起被压缩，长旁白在 1.4 倍会显紧。属于预期，靠试听预览让用户自己判断 |
| 同目录反复导出 | 换倍率重导会覆盖上一轮副本（文件名规则不变）。manifest 里的 `speed` 说明这批是什么倍率；要并存两版就导到不同目录 |
| 文件名不带倍率 | 有意为之：`序号_角色_文本.mp3` 是下游剪辑的匹配契约，加 `_1.25x` 会破坏排序与命名规则 |
| 浏览器写盘顺序 | 逐条串行 fetch，单条 66ms，100 条约 7s，与现有逐条写盘节奏一致，不额外做并发 |
| 客户端中断导出 | `tempo_stream` 的 `finally` 里 `kill()` 子进程，避免 ffmpeg 残留 |
| 单句成品 / 补录 | 它们本就在 `_collect_export_rows` 的导出集合里，变速一视同仁，无需特判 |
| 大小与码率 | 变速后仍按 `-b:a 128k` 重编码。1.5 倍时长变短，文件反而更小 |

## 8. 验收清单

- [ ] `speed=1.0` 导出：文件与源**逐字节一致**，耗时与改造前相同
- [ ] `speed=1.25` 导出：时长 ≈ 源 / 1.25，音调不变，可正常播放
- [ ] 三条通道（浏览器写盘 / ZIP / 后端目录）产物听感一致、时长一致
- [ ] `manifest.json` 带 `speed`，浏览器通道与 ZIP 通道字段完全对齐
- [ ] 导出后核对源文件 md5：**必须与导出前相同**
- [ ] 导出目录误选成品源目录时被拒绝，源文件未受损
- [ ] 导出中途关掉浏览器页面，进程列表里无残留 ffmpeg
- [ ] 列表试听按当前倍率播放，切回 1.0× 恢复正常
- [ ] 刷新页面后倍率选项仍是上次的值

## 9. 实施顺序

1. `audio.py`：`check_speed` / `tempo_filter` / `tempo_stream` / `tempo_to_file`
2. `api.py`：`/api/files` 加 `speed`（此时已可手动 `curl` 验证产物）
3. `exports.py`：manifest 加 `speed`、`export_plan` / `iter_archive` / `export_completed_batch` 透传
4. `api.py`：三个导出入口的参数
5. 前端：`types` → `localExport` → `AudioPlayer(rate)` → `ProductsPage` 面板
6. 端到端：3 条台词 → 1.25× 导出 → 核对时长/md5/manifest → 试听

工作量参考：后端 4 处、前端 4 处，均为加法式改动，无迁移、无重构。

---

## 10. 实装结果（已完成）

### 10.1 改动清单

| 文件 | 内容 |
|---|---|
| `studio/audio.py` | 新增 `SPEED_MIN/MAX`、`check_speed`、`tempo_filter`、`tempo_stream`、`tempo_to_file`；现有函数一行未动 |
| `studio/api.py` | `/api/files` 加 `speed` query；`ExportPayload.speed`；`archive.zip`、`export-plan` 加 `speed`；plan 的 url 追加 `&speed=` |
| `studio/exports.py` | `_build_manifest(…, speed)` 写入 `speed` 字段；`export_plan` / `iter_archive` / `export_completed_batch` 透传；目录导出前置拦截覆盖源文件 |
| `studio/service.py` | `export_completed_batch` 透传 `speed` |
| `frontend/lib/types.ts` | `ExportPlanFile.speed` |
| `frontend/lib/localExport.ts` | `exportToDirectory(…, speed, onProgress)`；manifest 同步 `speed` |
| `frontend/components/ui/AudioPlayer.tsx` | 新增 `rate` prop，写 `playbackRate` + 显式 `preservesPitch` |
| `frontend/features/products/ProductsPage.tsx` | 语速档位控件、`localStorage` 记忆、导出/ZIP 传参、列表试听按倍率 |
| `frontend/app/theme.css` | `.speed-options` 分段胶囊组 |

### 10.2 实测数据（真实项目「蜡笔小新」15 条成品）

| 项 | 结果 |
|---|---|
| `speed=1.0` 目录导出 | 15 条副本的 md5 集合与源文件**完全一致**（逐字节） |
| `speed=1.25` 逐条倍率 | 源时长/副本时长 = **1.239 ~ 1.250**，15 条全部在 5% 内 |
| `speed=1.4` ZIP | 16 条目（15 音频 + manifest），manifest `speed=1.4` |
| 流式单条耗时 | 66 ms（5 秒音频，65 KB） |
| 源文件 | 全程 md5 未变，`outputs` 目录文件数不变 |
| 越界倍率 0.3 / 2.5 | 422 拦截 |
| 越界倍率（服务层） | `check_speed` 抛 `ValueError`，提示"语速需要在 0.5~2.0 倍之间" |

### 10.3 两处需要知道的事实

**① 倍率不会精确等于设定值。** mp3 编码每条都有固定的帧对齐与编码器延迟开销，
在解码时长上表现为几十毫秒的常数项。5~8 秒的台词几乎看不出来（1.239~1.250），
但 0.7 秒的短句在 1.4 倍下实测比值 1.396 —— 常数项占比被放大了。
这只影响"毫秒级对齐"的场景，不影响听感。

**② 目录导出还有个隐藏开关。** `POST /outputs/export` 走 `require_host_fs()`，
需要 `QWEN_TTS_ALLOW_HOST_FS=1` 才开放（默认关闭，避免把"往服务器任意路径写并删文件"
的能力暴露给局域网）。界面上没有这个按钮，用户走的是浏览器写盘与 ZIP，都正常。

### 10.4 未做（有意）

- 单条「下载」按钮不变速：那是"从成品库取原件"，与"导出一批交付副本"是两回事。
- 文件名不带倍率后缀：`序号_角色_文本.mp3` 是下游剪辑的匹配契约。
- 语速不落库：只记在 `localStorage` 与 manifest 的 `speed` 字段里。


## 11. v2：改成逐条倍率（2026-10-28 当天迭代）

第 10 节做完的是"整批一个倍率"。当天用户提了两点，v2 按此调整：

1. 只有**部分**台词需要加速，不是全部；
2. 每条台词旁边要有一个**自己的**倍速按钮，默认 1.0×，可以调。

### 11.1 数据流：倍率跟着行走，不跟着项目走

| 环节 | 键 | 说明 |
|---|---|---|
| 前端状态 | `order_key` | `batch:<id>` / `single:<id>` / `supplement:<id>`，与成品列表的排序键同一个 |
| localStorage | `qwen-tts.row-speed` | `{ order_key: 倍率 }`，等于 1.0 的条目不写，避免存档里堆一堆默认值 |
| 查询串 | `speeds=batch:xxx\|1.25,single:yyy\|1.4` | 分隔符用 `\|` 不用 `:` —— `order_key` 自己带冒号 |
| manifest | 每条的 `speed` 字段 | 一份交付里可以混着原速和加速，清单逐条解释 |

之所以用 `order_key` 而不是序号：序号会随拖动排序变，倍率就会错位到下一条台词上。
`order_key` 绑的是那条成品本身，换顺序、改文件名都不会串。

### 11.2 后端

- `_build_manifest(rows, excerpt_length, speed=1.0, speeds=None)`：
  `speeds` 按 `order_key` 覆盖 `speed`，**整批先过一遍 `check_speed`**，非法值在碰磁盘前就 400。
- `export_plan` / `export_completed_batch` 收 `speeds`；`iter_archive` 直接用清单条目上的
  `speed` 逐条判断（有任一条非 1.0 才进临时目录，原速条目仍走 `bundle.write` 原文件）。
- `archive.zip` / `export-plan` 加查询参数 `speeds`（GET 没有请求体，浏览器要能直接开链接）；
  `POST /outputs/export` 走 JSON body 的 `speeds` 字典。
- `_parse_speeds()` 容错：解析不动的片段跳过，按原速走 —— 链接被手工改坏时整批失败更糟。
- **覆盖母本的拦截条件改了**：原来判断"整批倍率非 1"，现在判断"清单里存在任一变速条目"，
  否则混着来的时候会漏掉。

### 11.3 前端

- 每行右侧多一颗 `.speed-chip`：原速是安静灰胶囊，非原速染成强调色（一眼看出哪几条被改过）。
- 点开是 `.row-speed-pop`（`position:fixed`，与 `.row-menu` 同一套锚定手法，绝对定位会被列表裁掉）：
  5 个快捷档（1.0/1.15/1.25/1.4/1.5）+ 滑块（0.5~2.0，步进 0.05）+ 还原原速。
- 导出面板不再有一个全局倍率，改成「全部台词」批量档：一键把所有行设成同一倍率（含全部还原）。
  全部同值时该档位点亮；混着时都不点亮。
- 试听仍按该行自己的倍率播（`AudioPlayer rate={speedOf(item)}`）。
- 导出按钮文案变成「（N 条加速）」，ZIP 链接带 `speeds` 参数。

### 11.4 实测（蜡笔小新 15 条，真实服务 7871）

| 项 | 结果 |
|---|---|
| 只给第 1、3 条设倍率 | manifest `[1.25, 1.0, 1.4, 1.0...]`，url 只有第 1、3 条带 `speed=` |
| 第 1 条 1.25× | 7.680s → 6.219s（比值 1.235） |
| 第 3 条 1.4× | 8.400s → 6.075s（比值 1.383） |
| 第 2 条未设 | 6.240s → 6.240s，与源文件逐字节一致 |
| ZIP | 1021 KB，manifest 逐条倍率与计划一致，音频时长对得上 |
| 批量设 1.4× | 15 条全部高亮，按钮「（15 条加速）」，ZIP 参数 799 字符 |
| 全部还原 | 高亮归零，localStorage 存 `{}` |
| 越界 3.5 | 400「语速需要在 0.5~2.0 倍之间」 |
| 源文件 | 全程 md5 未变 |

### 11.5 这条路的上限

倍率走 URL 查询串，15 条全加速时约 800 字符；100 条约 5 KB，仍在 uvicorn 的请求行上限内。
真要上千条，应该改成 POST 拿清单（那时 ZIP 也得改成前端 fetch 后存盘）。

## 12. 交付里去掉 manifest.json（2026-10-28 收尾）

用户原话：「为什么导出的文件里面有一个叫 manifest.json 的文件，导出的时候不要这个文件。」

前面各节提到的 manifest 都是当时的设计；**现在三条通道都不再生成它**，导出目录与
ZIP 里只有音频文件。

### 12.1 改了什么

| 位置 | 改动 |
|---|---|
| `exports._build_manifest` | 改名 `_build_entries`，去掉 `app` 字段。它现在只负责"这条叫什么文件名、用哪个倍率"，不再产出给磁盘的清单 |
| `exports.export_completed_batch` | 不写 `manifest.json`；若目录里有旧版本自己写的清单，先按它清掉过时 mp3，再把清单本身删掉 |
| `exports.iter_archive` | ZIP 里不再 `writestr("manifest.json")`（两个分支都去掉了）|
| `frontend/lib/localExport.ts` | 不再写清单；改为"读旧清单 → 清过时 mp3 → 删旧清单"，且只处理带 `app` 标记的那份 |
| `scripts/api_smoke.py` | 断言改读 `export_plan`；新增"目录/ZIP 里没有 manifest.json"与"旧文件不再被清理"两条 |

### 12.2 代价（必须记住）

清单是唯一能证明"这些 mp3 是我上次导出的"的凭据。**不再写它之后，重复导出到同一个
文件夹时，上一轮遗留、这轮不再产出的 mp3 不会被清理。** 同名文件仍会覆盖。

清理能力只对"改造前导出过的目录"保留一次：那里还躺着旧清单，下次导出会顺手清掉过时
文件并把清单删掉。之后的目录就没有凭据了。

要恢复自动清理，有一个不产生额外文件的方案：把"上次导出到这个目录写了哪些文件"存进
浏览器 IndexedDB（FileSystemHandle 可持久化）。但目录可能被改名/移动，凭据会失效，
复杂度也上一个量级 —— 现在不值得做。

### 12.3 顺带受影响的地方

- `ExportPlanFile.app` 字段删除（前端类型与后端条目同步）。`MANIFEST_APP` 常量保留：
  它只剩一个用途 —— 认出历史遗留的旧清单是不是自己写的。
- 混版追溯（`voice_id` / `voice_version` / `tag_prefix` / `speed`）仍在 `export-plan`
  响应里，但不再落到交付目录。要追溯只能回应用里看。
- 用户若同时要"原速版 + 加速版"，仍然靠导到两个不同目录区分。
