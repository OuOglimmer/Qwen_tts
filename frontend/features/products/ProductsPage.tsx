'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut, fileUrl } from '@/lib/api'
import { usePolling } from '@/lib/hooks'
import { parseIndexSpec } from '@/lib/indexSpec'
import { fetchActiveVoices } from '@/lib/voices'
import { PickCancelled, encodeSpeeds, exportToDirectory, supportsDirectoryExport, type ExportProgress } from '@/lib/localExport'
import type { ExportPreflight, Notify, OutputRow, OutputsResponse, VoiceAsset } from '@/lib/types'
import { AudioPlayer, Button, Label, LanguageSelect, SectionTitle, useConfirmDialog, VoiceSelect } from '@/components/ui'

type ProductsPageProps = {
  projectId: string
  projectName: string
  notify: Notify
  /** true 时只展示音频列表（独立「成品」页）；false 时带导出设置（流程第 4 步）。 */
  audioOnly?: boolean
  /** 页面可见时为 true；隐藏（keep-alive 切走）时暂停后台轮询。 */
  active?: boolean
}

const signatureOf = (rows: OutputRow[]) =>
  rows.map((item) => `${item.order_key}:${item.status ?? ''}:${item.url ?? ''}`).join('|')

/** 单条台词的导出倍率：默认 1.0（原速），可逐条调。上限 2.0 与后端
 *  ``check_speed`` 一致；超过 1.5 短句容易吞字，只在滑块提示里说，不拦。 */
const SPEED_MIN = 0.5
const SPEED_MAX = 2
const SPEED_STEP = 0.05
/** 浮层里的快捷档：只放"加快"的常用值 —— 0.9× 那段用滑块调更直观，
 *  六个档塞进 300px 会折成两行。 */
const SPEED_PRESETS = [1, 1.15, 1.25, 1.4, 1.5]
/** 每行倍率按 order_key 存：换排序、改文件名都不会让设置错位到下一条。 */
const ROW_SPEED_STORAGE_KEY = 'qwen-tts.row-speed'

/** 上次逐条设过的倍率。这是导出参数而非内容，刷新/切页后不该被打回原速。 */
const readStoredSpeeds = (): Record<string, number> => {
  if (typeof window === 'undefined') return {}
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(ROW_SPEED_STORAGE_KEY) ?? '{}')
    if (!parsed || typeof parsed !== 'object') return {}
    const out: Record<string, number> = {}
    for (const [key, raw] of Object.entries(parsed as Record<string, unknown>)) {
      const value = Number(raw)
      if (Number.isFinite(value) && value >= SPEED_MIN && value <= SPEED_MAX && Math.abs(value - 1) > 1e-6) {
        out[key] = value
      }
    }
    return out
  } catch {
    return {}
  }
}

const clampSpeed = (raw: number): number => {
  if (!Number.isFinite(raw)) return 1
  // 两位小数：滑块步进 0.05 之外，手输 1.333 也能落成一个可解释的值。
  const stepped = Math.round(raw * 100) / 100
  return Math.min(SPEED_MAX, Math.max(SPEED_MIN, stepped))
}

/** 倍率写法：1.25 显示成 1.25×，1.0 显示成 1.0×（至少留一位小数，
 *  否则原速写成 "1×" 会让人以为是个倍数开关而不是可调的数值）。 */
const speedLabel = (value: number): string => `${value.toFixed(2).replace(/0$/, '')}×`

/**
 * 单条台词的倍率按钮：点开小浮层，预设档 + 滑块微调 + 还原。
 *
 * 浮层同 ``RowMenu`` 一套手法——``position:fixed`` 锚定视口。绝对定位会被
 * ``.product-list`` 之类滚动容器裁掉，还会把列表撑出滚动条。
 * 锚定坐标只在打开时量一次，滚动/缩放直接关掉（与原生菜单一致）。
 */
function SpeedChip({ value, label, onChange }: { value: number; label: string; onChange: (next: number) => void }) {
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const open = () => {
    const el = triggerRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    // 与 theme.css 的 .row-speed-pop 尺寸对应（宽 300 + 上下内边距与各段行高）。
    // 略大于实际高度：向上翻转时宁可靠上一点，也不要压住触发器。
    const width = 300
    const height = 175
    const gap = 6
    const viewH = window.innerHeight
    const viewW = window.innerWidth
    const upward = viewH - rect.bottom < height + gap && rect.top > height + gap
    // 左边缘对齐触发器；靠右的行会被视口截断，往左推回来。
    const left = Math.max(12, Math.min(rect.left, viewW - width - 12))
    setAnchor({ top: upward ? rect.top - height - gap : rect.bottom + gap, left })
  }

  useEffect(() => {
    if (!anchor) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      setAnchor(null)
    }
    const close = () => setAnchor(null)
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setAnchor(null) }
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [anchor])

  const isOriginal = Math.abs(value - 1) < 1e-6
  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        className={`speed-chip${isOriginal ? '' : ' is-retimed'}${anchor ? ' is-open' : ''}`}
        title={isOriginal
          ? '这条按原速导出。点开可以单独给它加速（只影响导出的副本，原文件不动）'
          : `这条导出时按 ${speedLabel(value)}（时长约为原来的 ${Math.round(100 / value)}%，音调不变）`}
        aria-haspopup="dialog"
        aria-expanded={!!anchor}
        onClick={() => (anchor ? setAnchor(null) : open())}
      >{speedLabel(value)}</button>
      {anchor && (
        <div className="row-speed-pop" role="dialog" ref={panelRef} style={{ top: anchor.top, left: anchor.left }}>
          <div className="row-speed-head">
            <span>{label}</span>
            <strong>{speedLabel(value)}</strong>
          </div>
          <div className="row-speed-presets">
            {SPEED_PRESETS.map((preset) => <button
              type="button"
              key={preset}
              className={Math.abs(preset - value) < 1e-6 ? 'is-active' : undefined}
              onClick={() => onChange(preset)}
            >{speedLabel(preset)}</button>)}
          </div>
          <input
            className="row-speed-range"
            type="range"
            min={SPEED_MIN}
            max={SPEED_MAX}
            step={SPEED_STEP}
            value={value}
            aria-label="导出倍率"
            onChange={(event) => onChange(clampSpeed(Number(event.target.value)))}
          />
          <p className="row-speed-hint">
            只影响这条导出的副本，原文件不动 · 时长约 {Math.round(100 / value)}%
            {value > 1.5 && ' · 超过 1.5× 短句容易吞字'}
          </p>
          <div className="row-speed-foot">
            <span>可调 {SPEED_MIN}× – {SPEED_MAX}×</span>
            <button type="button" onClick={() => onChange(1)} disabled={isOriginal}>还原原速</button>
          </div>
        </div>
      )}
    </>
  )
}

export default function ProductsPage({ projectId, projectName, notify, audioOnly = false, active }: ProductsPageProps) {
  const [items, setItems] = useState<OutputRow[]>([])
  const [excerptLength, setExcerptLength] = useState(8)
  // 逐条倍率（order_key → 倍率）。没列到的行就是原速 1.0。
  // 首帧固定空对象：localStorage 只在客户端可读，直接拿它当初始值会让
  // SSR 的 HTML（全原速）与首帧 hydration 对不上。
  const [rowSpeeds, setRowSpeeds] = useState<Record<string, number>>({})
  const [busy, setBusy] = useState(false)
  // 导出两阶段文案：先等使用者在本机选文件夹，确认后才是逐条写入。
  const [pickingFolder, setPickingFolder] = useState(false)
  const [progress, setProgress] = useState<ExportProgress | null>(null)
  // 浏览器能否直写使用者本机目录；window 只能在客户端读，故首帧为 null（未知）。
  const [canWriteLocal, setCanWriteLocal] = useState<boolean | null>(null)
  const [lastExport, setLastExport] = useState('')
  // 补录单句表单
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [supplementVoiceId, setSupplementVoiceId] = useState('')
  const [supplementText, setSupplementText] = useState('')
  const [supplementLanguage, setSupplementLanguage] = useState('Chinese')
  const [insertAfter, setInsertAfter] = useState(-1)
  const [generating, setGenerating] = useState(false)
  // 批量删除输入
  const [deleteInput, setDeleteInput] = useState('')
  // 正在换用哪一版：`${output_id}:${version}`，只让被点的那一颗按钮转圈。
  const [switchingVersion, setSwitchingVersion] = useState<string | null>(null)
  const { confirm, dialogElement } = useConfirmDialog()

  /** 当前倍率表的镜像：setRowSpeed 连点几下都不会读到过期闭包里的旧值。 */
  const speedsRef = useRef<Record<string, number>>({})

  const applySpeeds = useCallback((next: Record<string, number>) => {
    speedsRef.current = next
    setRowSpeeds(next)
    try {
      window.localStorage.setItem(ROW_SPEED_STORAGE_KEY, JSON.stringify(next))
    } catch { /* 隐私模式写不进去，不影响本次导出 */ }
  }, [])

  /** 单条改倍率。改回 1.0 就把这条从表里删掉，存储里不留"等于原速的设置"。 */
  const setRowSpeed = useCallback((key: string, value: number) => {
    const next = { ...speedsRef.current }
    if (Math.abs(value - 1) < 1e-6) delete next[key]
    else next[key] = clampSpeed(value)
    applySpeeds(next)
  }, [applySpeeds])

  /** 批量把所有台词设成同一倍率（含"全部还原原速"）。 */
  const setAllSpeeds = useCallback((value: number) => {
    if (Math.abs(value - 1) < 1e-6) { applySpeeds({}); return }
    const next: Record<string, number> = {}
    for (const item of items) if (item.order_key) next[item.order_key] = clampSpeed(value)
    applySpeeds(next)
  }, [applySpeeds, items])

  const speedOf = (item: OutputRow) => (item.order_key ? rowSpeeds[item.order_key] ?? 1 : 1)
  const retimedCount = items.filter((item) => Math.abs(speedOf(item) - 1) > 1e-6).length

  const listSignature = useRef('')
  /** 排序/删除/补录进行中时暂停自动轮询，避免过期响应把界面顶回去。 */
  const mutating = useRef(0)
  // 交换后短暂高亮受影响的两行，让“顺序变了、导出编号跟着变”看得见。
  const [flashKeys, setFlashKeys] = useState<string[]>([])
  const flashTimer = useRef<number | null>(null)

  const markFlash = useCallback((keys: string[]) => {
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current)
    setFlashKeys(keys)
    flashTimer.current = window.setTimeout(() => setFlashKeys([]), 1200)
  }, [])

  useEffect(() => () => { if (flashTimer.current !== null) window.clearTimeout(flashTimer.current) }, [])

  // 拖拽排序：只允许从手柄发起（避免与台词选区/播放器冲突），悬停行显示插入位置。
  const dragFrom = useRef<number | null>(null)
  const dragArmed = useRef(false)
  const [dropHintKey, setDropHintKey] = useState<string | null>(null)

  const applyData = useCallback((data: OutputsResponse) => {
    listSignature.current = signatureOf(data.items)
    setItems(data.items)
  }, [])

  const refresh = useCallback(async () => {
    applyData(await apiGet<OutputsResponse>(`/api/projects/${projectId}/outputs`))
  }, [projectId, applyData])

  const loadVoices = useCallback(async () => {
    const active = await fetchActiveVoices(projectId)
    setVoices(active)
    setSupplementVoiceId((previous) => previous || active[0]?.id || '')
  }, [projectId])

  useEffect(() => {
    refresh().catch((error: Error) => notify(error.message))
    // 声线列表属次要数据，拉取失败不打断首屏；补录提交时仍会校验声线。
    loadVoices().catch(() => undefined)
  }, [projectId, refresh, loadVoices, notify])

  // 浏览器写盘能力要到客户端才能判断（依赖 window），首帧渲染后再探测。
  useEffect(() => { setCanWriteLocal(supportsDirectoryExport()) }, [])

  // 上次逐条设过的倍率：客户端挂载后读一次，之后每次改动写回。
  // 写入走 applySpeeds 而不是 effect 监听 rowSpeeds —— 挂载时 effect 的执行顺序
  // 会让"读回来的值"被首帧的空对象先覆盖一次，那个缝隙里刷新就丢设置。
  useEffect(() => {
    const stored = readStoredSpeeds()
    speedsRef.current = stored
    setRowSpeeds(stored)
  }, [])

  // 后台批量完成或补录落库后，成品列表自动出现，无需手动刷新。
  // 轮询带 force 保证成品实时；active=false（页面被 keep-alive 切走）时暂停轮询。
  usePolling(useCallback(async () => {
    if (mutating.current > 0) return
    const data = await apiGet<OutputsResponse>(`/api/projects/${projectId}/outputs`, { force: true })
    const signature = signatureOf(data.items)
    if (signature === listSignature.current) return
    applyData(data)
  }, [projectId, applyData]), 5000, active ?? true)

  const persistOrder = async (next: OutputRow[]) => {
    mutating.current += 1
    setItems(next)
    try {
      await apiPut(`/api/projects/${projectId}/outputs/order`, { order: next.map((item) => item.order_key ?? '') })
    } catch (error) {
      notify((error as Error).message)
      await refresh().catch(() => undefined)
    } finally {
      mutating.current -= 1
    }
  }

  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= items.length) return
    const next = [...items]
    ;[next[index], next[target]] = [next[target], next[index]]
    markFlash([next[index].order_key ?? '', next[target].order_key ?? ''])
    const low = Math.min(index, target) + 1
    const high = Math.max(index, target) + 1
    notify(`已交换第 ${low}、${high} 位，两条音频的导出编号随之互换`)
    void persistOrder(next)
  }

  /** 行标识：优先 order_key，退化用来源+位置，供交换高亮定位。 */
  const keyOf = (item: OutputRow, index: number) => item.order_key ?? `${item.source}-${item.position}-${index}`

  /** 拖拽落点：把 from 行移到 targetIndex，导出编号随新顺序整体更新。 */
  const dropAt = (targetIndex: number) => {
    const from = dragFrom.current
    dragFrom.current = null
    dragArmed.current = false
    setDropHintKey(null)
    if (from === null || from === targetIndex) return
    const moved = items[from]
    const next = [...items]
    next.splice(from, 1)
    next.splice(targetIndex, 0, moved)
    markFlash([keyOf(moved, from)])
    notify(`已把第 ${from + 1} 条移到第 ${targetIndex + 1} 位，导出编号相应更新`)
    void persistOrder(next)
  }

  const resetOrder = async () => {
    mutating.current += 1
    try {
      await apiPut(`/api/projects/${projectId}/outputs/order`, { order: [] })
      await refresh()
      notify('已恢复默认排序（批量序号 → 单句 → 补录）')
    } catch (error) { notify((error as Error).message) } finally { mutating.current -= 1 }
  }

  /** 换用历史版本：后端把那一版复制成新的一版并设为当前，旧版继续留档。 */
  const useVersion = async (item: OutputRow, version: number) => {
    if (!item.id) return
    mutating.current += 1
    setSwitchingVersion(`${item.id}:${version}`)
    try {
      const result = await apiPost<{ version: number; restored_from: number }>(
        `/api/projects/${projectId}/batch/${item.id}/use-version`, { version },
      )
      await refresh()
      notify(`已换用 v${result.restored_from}（记为新的一版 v${result.version}），导出取的就是这一版`)
    } catch (error) {
      notify((error as Error).message)
    } finally {
      setSwitchingVersion(null)
      mutating.current -= 1
    }
  }

  /** 删除一行：批量行=移出成品（任务与音频保留），单句/补录行=连文件一起删。两类都先弹确认。 */
  const removeRow = async (item: OutputRow) => {
    if (item.source === 'batch') {
      if (!(await confirm({
        title: '移出成品',
        message: `确认把「${item.text.slice(0, 12)}…」移出成品？任务与音频会保留，可在配音页重新选入。`,
        confirmText: '移出',
      }))) return
      mutating.current += 1
      try {
        await apiPost(`/api/projects/${projectId}/batch/${item.id}/select`, { selected: false })
        notify(`「${item.text.slice(0, 12)}…」已移出成品；任务表第 ${item.source_position} 条保留，可在配音页重新选入`)
        await refresh()
      } catch (error) { notify((error as Error).message) } finally { mutating.current -= 1 }
      return
    }
    const kind = item.source === 'single' ? '单句成品' : '补录单句'
    if (!(await confirm({
      title: `删除${kind}`,
      message: `确认删除${kind}「${item.name ?? ''}」？音频文件会一并删除。`,
      confirmText: '删除',
      danger: true,
    }))) return
    mutating.current += 1
    try {
      if (item.source === 'single') await apiDelete(`/api/projects/${projectId}/single-outputs/${item.id}`)
      else await apiDelete(`/api/projects/${projectId}/outputs/supplement/${item.id}`)
      notify(`${kind}已删除`)
      await refresh()
    } catch (error) { notify((error as Error).message) } finally { mutating.current -= 1 }
  }

  /** 按序号批量处理：批量行移出成品，单句/补录行彻底删除。序号从 1 开始，支持 1,3,5~8。 */
  const bulkDelete = async () => {
    const parsed = parseIndexSpec(deleteInput, items.length)
    if (typeof parsed === 'string') { notify(parsed); return }
    const targets = parsed
      .map((index) => ({ index, item: items[index - 1] }))
      .filter((entry): entry is { index: number; item: OutputRow } => entry.item !== undefined)
    if (targets.length === 0) { notify('没有匹配到可删除的条目'); return }
    const deleteCount = targets.filter((entry) => entry.item.source !== 'batch').length
    const moveOutCount = targets.length - deleteCount
    if (!(await confirm({
      title: '批量删除成品',
      message: <>
        <p>共处理 {targets.length} 条：移出成品 {moveOutCount} 条，彻底删除 {deleteCount} 条。</p>
        {moveOutCount > 0 && <p>「移出成品」的任务与音频会保留，可在配音页重新选入。</p>}
        {deleteCount > 0 && <p>「彻底删除」会连音频文件一起删掉，<strong>不可恢复</strong>。</p>}
        <div className="confirm-scroll-list">
          {targets.map((entry) => (
            <div key={entry.index} className="confirm-scroll-item" title={entry.item.text}>
              <span className="item-index">{entry.index}</span>
              <span className="item-text">{entry.item.text}</span>
            </div>
          ))}
        </div>
      </>,
      confirmText: '删除',
      danger: true,
    }))) return
    mutating.current += 1
    let movedOut = 0
    let deleted = 0
    try {
      for (const { item } of targets) {
        if (item.source === 'batch') {
          await apiPost(`/api/projects/${projectId}/batch/${item.id}/select`, { selected: false })
          movedOut += 1
        } else if (item.source === 'single') {
          await apiDelete(`/api/projects/${projectId}/single-outputs/${item.id}`)
          deleted += 1
        } else {
          await apiDelete(`/api/projects/${projectId}/outputs/supplement/${item.id}`)
          deleted += 1
        }
      }
      setDeleteInput('')
      await refresh()
      notify(`已处理 ${movedOut + deleted} 条（移出成品 ${movedOut} · 彻底删除 ${deleted}）`)
    } catch (error) {
      notify((error as Error).message)
      await refresh().catch(() => undefined)
    } finally { mutating.current -= 1 }
  }

  /** 补录单句：选声线 + 填台词即时生成，并可指定插入到某条之后。 */
  const createSupplement = async () => {
    if (!supplementVoiceId) { notify('请选择用于补录的声线'); return }
    if (!supplementText.trim()) { notify('请填写补录台词'); return }
    setGenerating(true)
    mutating.current += 1
    try {
      const row = await apiPost<OutputRow>(`/api/projects/${projectId}/outputs/supplement`, {
        voice_id: supplementVoiceId, text: supplementText.trim(), language: supplementLanguage,
      })
      if (row.status !== 'done') {
        notify(`补录生成失败：${row.error ?? '未知错误'}（失败记录已保留，可删除后重试）`)
        await refresh()
        return
      }
      // 放到对应部分：默认追加到末尾，也可插入到指定条目之后。
      const keys = items.map((item) => item.order_key ?? '').filter(Boolean)
      const at = insertAfter >= 0 ? insertAfter + 1 : keys.length
      keys.splice(at, 0, row.order_key ?? '')
      await apiPut(`/api/projects/${projectId}/outputs/order`, { order: keys })
      setSupplementText('')
      await refresh()
      notify(`补录完成，已插入到第 ${at + 1} 位`)
    } catch (error) {
      notify((error as Error).message)
    } finally {
      setGenerating(false)
      mutating.current -= 1
    }
  }

  /** 导出前检查：只报告事实，不拦导出。
   *  两件事值得在落盘前说清楚——哪几句台词有多条成品、这次取了哪一条；
   *  以及哪个角色混着几版声线（换版后不重录本来就会这样，但他得知道）。 */
  const preflightMessage = (pre: ExportPreflight): string => {
    const parts: string[] = []
    const mixed = pre.roles.filter((role) => role.mixed)
    if (mixed.length) {
      const lines = mixed.map((role) => {
        const detail = role.versions
          .map((item) => `v${item.version} ${item.count} 条${item.is_current ? '（当前主版本）' : ''}`)
          .join('、')
        return `${role.name} 用了第 ${role.versions.map((item) => item.version).join('、')} 版声线：${detail}`
      })
      parts.push(`${lines.join('；')}。同一角色不同版本的音色不一致，需要统一请先按人物重新生成。`)
    }
    if (pre.deduped > 0) {
      parts.push(`有 ${pre.deduped} 条同句成品被合并，只取最后选中的那条。`)
    }
    return parts.join('')
  }

  /** 主流程：点导出 → 浏览器在「使用者自己的电脑」上弹文件夹框 → 按当前顺序逐条写入。
   *  文件名由后端 export-plan 给出，前端只负责落盘（目录里只有音频，没有清单文件）。 */
  const exportAll = async () => {
    // 检查弹窗期间不进入 busy：否则按钮会显示「正在导出…」，其实还在等用户确认。
    try {
      const pre = await apiGet<ExportPreflight>(
        `/api/projects/${projectId}/outputs/export-preflight`, { force: true },
      )
      const detail = preflightMessage(pre)
      if (detail) {
        if (!(await confirm({
          title: '导出前检查',
          message: `本次将导出 ${pre.total} 条。${detail}`,
          confirmText: '继续导出',
        }))) return
      }
    } catch {
      // 检查本身失败不该挡住导出，继续走后面的正常流程。
    }
    setBusy(true)
    setPickingFolder(true)
    setProgress(null)
    try {
      const result = await exportToDirectory(projectId, excerptLength, speedsRef.current, (next) => {
        setPickingFolder(false)
        setProgress(next)
      })
      const speedNote = retimedCount === 0 ? '' : `（其中 ${retimedCount} 条已加速）`
      const summary = `已导出 ${result.count} 条成品到本机文件夹「${result.directory}」${speedNote}`
      if (result.failed.length === 0) {
        setLastExport(summary)
        notify(summary)
      } else {
        const names = result.failed.slice(0, 3).join('、')
        notify(`${summary}；${result.failed.length} 条写入失败：${names}${result.failed.length > 3 ? ' …' : ''}`)
      }
    } catch (error) {
      if (error instanceof PickCancelled) notify(`${error.message}，未导出`)
      else notify((error as Error).message)
    } finally {
      setBusy(false)
      setPickingFolder(false)
      setProgress(null)
    }
  }

  const sourceLabel = (item: OutputRow) => {
    if (item.source === 'batch') return `批量生成 · 任务 #${String(item.source_position ?? item.position).padStart(4, '0')}`
    if (item.source === 'single') return '单句成品'
    return '补录单句'
  }

  /** 可换回的历史版本：versions 含当前版（最新在前），这里只留旧版。 */
  const historyOf = (item: OutputRow) => (item.versions ?? []).filter((entry) => entry.version !== item.version)

  const audioList = <div className="panel products-panel">
    <div className="panel-heading products-heading">
      成品音频
      <em className="heading-hint">拖动 ⠿ 或 ↑↓ 调整顺序，导出文件名按当前顺序从 1 开始编号</em>
      <span>{items.length} 条</span>
    </div>
    <div className="products-toolbar">
      <Button small onClick={resetOrder} disabled={items.length === 0}>恢复默认排序</Button>
      <span className="toolbar-sep" />
      <input value={deleteInput} onChange={(event) => setDeleteInput(event.target.value)} placeholder="按序号删除，如：1,3,5~8" style={{ width: 200 }} />
      <Button small onClick={bulkDelete} disabled={items.length === 0}>批量删除</Button>
    </div>
    <div className="product-list">
      {items.length === 0 && <p className="empty-state">暂无成品。绑定已固化声线批量生成后，配音会自动进入这里；也可以在下方「补录单句」即时生成一句话。</p>}
      {items.map((item, index) => {
        const rowKey = keyOf(item, index)
        const history = historyOf(item)
        return <article
          className={`product-row${flashKeys.includes(rowKey) ? ' row-flash' : ''}${dropHintKey === rowKey ? ' row-drop-hint' : ''}`}
          key={rowKey}
          draggable
          onDragStart={(event) => {
            if (!dragArmed.current) { event.preventDefault(); return }
            dragFrom.current = index
            event.dataTransfer.effectAllowed = 'move'
            event.dataTransfer.setData('text/plain', String(index))
          }}
          onDragOver={(event) => {
            if (dragFrom.current === null || dragFrom.current === index) return
            event.preventDefault()
            event.dataTransfer.dropEffect = 'move'
            setDropHintKey(rowKey)
          }}
          onDragLeave={() => setDropHintKey((current) => (current === rowKey ? null : current))}
          onDrop={(event) => { event.preventDefault(); dropAt(index) }}
          onDragEnd={() => { dragFrom.current = null; dragArmed.current = false; setDropHintKey(null) }}
        >
          <span
            className="drag-handle"
            title="按住拖动调整顺序"
            onMouseDown={() => { dragArmed.current = true }}
            onMouseUp={() => { dragArmed.current = false }}
          >⠿</span>
          <div className="product-index" title="导出文件名的序号，按当前顺序从 1 编号">
            <span className="order-badge">{index + 1}</span>
            <small>导出序号</small>
          </div>
          <div className="product-info">
            <strong>{item.voice_name ?? item.name}</strong>
            {/* 已换版 = 这条成品生成时用的那一声线已经被新一版顶替（旧音频完好，只是归属成历史）。
                放中性灰角标而不是红/黄：这不是错误，是"能解释得清的历史"。 */}
            {(item.roster_state === 'replaced' || item.voice_state === 'replaced') && <span
              className="stale-badge"
              title="这条成品生成时用的声线后来换版了；音频完好保留。要换成新版的声音，请用「按人物重新生成」重录，旧版仍留在成品库里。"
            >已换版</span>}
            <p>{item.text}</p>
            <small>
              {item.language} · {sourceLabel(item)}
              {item.voice_version ? ` · 声线 v${item.voice_version}` : ''}
              {item.version ? ` · 第 ${item.version} 次录音` : ''}
              {item.status === 'failed' && <span className="error-text"> · 生成失败：{item.error}</span>}
            </small>
            {history.length > 0 && <details className="output-versions">
              <summary>{history.length} 个旧版本可换回</summary>
              {history.map((entry) => <div className="output-version-row" key={entry.version}>
                <span className="version-badge">v{entry.version}</span>
                <AudioPlayer url={fileUrl(entry.url)} rate={speedOf(item)} />
                <Button
                  small
                  disabled={switchingVersion !== null}
                  title="换回这一版：它会成为当前版本并参与导出，其它版本继续保留"
                  onClick={() => useVersion(item, entry.version)}
                >{switchingVersion === `${item.id}:${entry.version}` ? '换用中…' : '换用这一版'}</Button>
              </div>)}
            </details>}
          </div>
          <AudioPlayer url={fileUrl(item.url ?? null)} wide rate={speedOf(item)} />
          <span className="cell-actions">
            <SpeedChip
              value={speedOf(item)}
              label={`第 ${index + 1} 条${item.voice_name ?? item.name ? ` · ${item.voice_name ?? item.name}` : ''}`}
              onChange={(next) => { if (item.order_key) setRowSpeed(item.order_key, next) }}
            />
            <Button small onClick={() => move(index, -1)} disabled={index === 0} title="上移">↑</Button>
            <Button small onClick={() => move(index, 1)} disabled={index === items.length - 1} title="下移">↓</Button>
            <Button small onClick={() => removeRow(item)}>删除</Button>
            {item.url && <Button small href={fileUrl(item.url) ?? '#'} download>下载</Button>}
          </span>
        </article>
      })}
    </div>
  </div>

  const supplementPanel = <div className="panel sub-panel">
    <div className="panel-heading">✚ 补录单句：后续补充台词时，用已固化声线即时生成一句并插入到对应位置</div>
    <div className="supplement-grid">
      <div>
        <Label>使用声线</Label>
        <VoiceSelect
          voices={voices}
          value={supplementVoiceId}
          onChange={setSupplementVoiceId}
          placeholder="本项目清单暂无有效声线，请到步骤 2 固化或从声线库添加"
        />
        <Label>语言</Label>
        <LanguageSelect value={supplementLanguage} onChange={setSupplementLanguage} />
        <Label>插入位置</Label>
        <select value={insertAfter} onChange={(event) => setInsertAfter(Number(event.target.value))}>
          <option value={-1}>追加到末尾</option>
          {items.map((item, index) => (
            <option key={item.order_key ?? index} value={index}>
              「{index + 1} {item.voice_name ?? item.name}」之后
            </option>
          ))}
        </select>
      </div>
      <div>
        <Label>台词（一句一个音频）</Label>
        <textarea className="tall" value={supplementText} onChange={(event) => setSupplementText(event.target.value)} placeholder="例如：这句话是后补的，放在第 3 条之后。" />
        <div className="button-row">
          <Button primary disabled={generating} onClick={createSupplement}>{generating ? '生成中…' : '生成并加入成品'}</Button>
          <span className="hint">生成完成后自动进入上方列表，按所选位置插入。</span>
        </div>
      </div>
    </div>
  </div>

  // 全部台词是否同一个倍率：是则把对应档位点亮，否（混着原速和加速）则都不点亮。
  const uniformSpeed = (() => {
    if (items.length === 0) return 1
    const first = speedOf(items[0])
    return items.every((item) => Math.abs(speedOf(item) - first) < 1e-6) ? first : null
  })()

  // ZIP 是浏览器直接开链接下载，逐条倍率只能走查询串（后端 _parse_speeds 对应）。
  const zipHref = (() => {
    const query = new URLSearchParams({ excerpt_length: String(excerptLength) })
    const encoded = encodeSpeeds(rowSpeeds)
    if (encoded) query.set('speeds', encoded)
    return fileUrl(`/api/projects/${projectId}/outputs/archive.zip?${query.toString()}`) ?? '#'
  })()

  const exportPanel = <div className="panel sub-panel">
    <div className="panel-heading">⚙ 导出设置：成品写入你自己电脑的文件夹</div>
    <Label>文件名中的台词截取字数（5-10）</Label>
    <input type="number" min={5} max={10} value={excerptLength} onChange={(event) => setExcerptLength(Number(event.target.value))} />
    <Label>导出语速（逐条设置，默认原速）</Label>
    <div className="speed-batch">
      <span className="speed-batch-label">全部台词</span>
      <div className="speed-options" role="group" aria-label="把全部台词设为同一倍率">
        {SPEED_PRESETS.map((preset) => <button
          type="button"
          key={preset}
          className={uniformSpeed !== null && Math.abs(preset - uniformSpeed) < 1e-6 ? 'is-active' : undefined}
          aria-pressed={uniformSpeed !== null && Math.abs(preset - uniformSpeed) < 1e-6}
          title={preset === 1
            ? '全部按原速导出：副本与源文件逐字节一致'
            : `把全部台词设为 ${speedLabel(preset)}（时长约为原来的 ${Math.round(100 / preset)}%，音调不变）`}
          onClick={() => setAllSpeeds(preset)}
        >{speedLabel(preset)}</button>)}
      </div>
    </div>
    {canWriteLocal === false && <p className="hint">当前访问方式下浏览器不能直接写本机文件夹（需 Chrome/Edge，并通过 https 或 localhost 打开），请改用「下载全部成品 ZIP」下载后解压。</p>}
    <div className="button-row four">
      {canWriteLocal !== false && (
        <Button primary disabled={busy || canWriteLocal === null} onClick={exportAll}>
          {busy
            ? (pickingFolder ? '等待选择文件夹…' : progress ? `写入中 ${progress.done}/${progress.total}` : '正在导出…')
            : `导出到本机文件夹…${retimedCount === 0 ? '' : `（${retimedCount} 条加速）`}`}
        </Button>
      )}
      <Button
        primary={canWriteLocal === false}
        href={zipHref}
        download
      >下载全部成品 ZIP</Button>
    </div>
    {lastExport && <p className="hint success-text">{lastExport}</p>}
  </div>

  if (audioOnly) return <>
    <SectionTitle title="成品" description="生成的配音自动进入成品：可试听、上下移动排序、逐条下载或按序号批量删除；补录台词即时生成并插入对应位置；导出按当前顺序编号，文件写到你自己的电脑。" />
    <div className="step-stack">
      {audioList}
      {supplementPanel}
      {exportPanel}
    </div>
    {dialogElement}
  </>

  return <>
    <SectionTitle number="4" title="成品导出" description="批量生成的配音会自动选入成品，在这里和已保留的单句成品、补录单句一起试听与排序；需要时导出到你自己电脑上的文件夹，或打包 ZIP 下载。" />
    <div className="step-stack">
      {/* 三段统计原来浮在页面底色上是三张独立小卡，现在收进同一个大盒子，靠竖线分段 */}
      <div className="panel">
        <div className="summary-strip">
          <div><span>已生成成品</span><strong>{items.length}</strong></div>
          <div><span>当前项目</span><strong>{projectName}</strong></div>
          <div><span>输出格式</span><strong>MP3 / 128kbps</strong></div>
        </div>
      </div>
      {audioList}
      {supplementPanel}
      {exportPanel}
    </div>
    {dialogElement}
  </>
}
