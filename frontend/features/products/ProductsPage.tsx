'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut, fileUrl } from '@/lib/api'
import { usePolling } from '@/lib/hooks'
import { parseIndexSpec } from '@/lib/indexSpec'
import { fetchActiveVoices } from '@/lib/voices'
import type { Notify, OutputRow, OutputsResponse, VoiceAsset } from '@/lib/types'
import { AudioPlayer, Button, Label, LanguageSelect, SectionTitle, useConfirmDialog, VoiceSelect } from '@/components/ui'

type ProductsPageProps = {
  projectId: string
  projectName: string
  notify: Notify
  /** true 时只展示音频列表（独立「成品」页）；false 时带导出设置（流程第 4 步）。 */
  audioOnly?: boolean
}

const signatureOf = (rows: OutputRow[]) =>
  rows.map((item) => `${item.order_key}:${item.status ?? ''}:${item.url ?? ''}`).join('|')

export default function ProductsPage({ projectId, projectName, notify, audioOnly = false }: ProductsPageProps) {
  const [items, setItems] = useState<OutputRow[]>([])
  const [exportDir, setExportDir] = useState('')
  const [excerptLength, setExcerptLength] = useState(8)
  const [zipUrl, setZipUrl] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 导出两阶段文案：先等弹窗选文件夹，确认后才是复制文件。
  const [pickingFolder, setPickingFolder] = useState(false)
  // 补录单句表单
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [supplementVoiceId, setSupplementVoiceId] = useState('')
  const [supplementText, setSupplementText] = useState('')
  const [supplementLanguage, setSupplementLanguage] = useState('Chinese')
  const [insertAfter, setInsertAfter] = useState(-1)
  const [generating, setGenerating] = useState(false)
  // 批量删除输入
  const [deleteInput, setDeleteInput] = useState('')
  const { confirm, dialogElement } = useConfirmDialog()

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
    setExportDir((previous) => previous || data.export_dir || '')
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
    setZipUrl(null)
    refresh().catch((error: Error) => notify(error.message))
    // 声线列表属次要数据，拉取失败不打断首屏；补录提交时仍会校验声线。
    loadVoices().catch(() => undefined)
  }, [projectId, refresh, loadVoices, notify])

  // 后台批量完成或补录落库后，成品列表自动出现，无需手动刷新。
  usePolling(useCallback(async () => {
    if (mutating.current > 0) return
    const data = await apiGet<OutputsResponse>(`/api/projects/${projectId}/outputs`)
    const signature = signatureOf(data.items)
    if (signature === listSignature.current) return
    applyData(data)
  }, [projectId, applyData]), 5000)

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

  /** 删除一行：批量行=移出成品（任务与音频保留），单句/补录行=连文件一起删。 */
  const removeRow = async (item: OutputRow) => {
    if (item.source === 'batch') {
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
    mutating.current += 1
    let movedOut = 0
    let deleted = 0
    try {
      for (const index of parsed) {
        const item = items[index - 1]
        if (!item) continue
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

  /** 主流程：点导出 → 弹出系统文件夹选择框（由后端所在机器弹出）→ 确认后按当前顺序导出。
   *  弹窗不可用（如后端跑在无桌面环境）时，退回输入框里手动填写的目录。 */
  const exportAll = async () => {
    setBusy(true)
    try {
      let destination = ''
      try {
        setPickingFolder(true)
        const picked = await apiPost<{ path: string | null }>('/api/dialog/pick-folder', exportDir.trim() ? { initial: exportDir.trim() } : {})
        if (!picked.path) { notify('已取消选择文件夹，未导出'); return }
        destination = picked.path
      } catch (dialogError) {
        destination = exportDir.trim()
        if (!destination) { notify(`文件夹选择框不可用：${(dialogError as Error).message}`); return }
      } finally {
        setPickingFolder(false)
      }
      setExportDir(destination)
      const result = await apiPost<{ destination: string; count: number }>(`/api/projects/${projectId}/outputs/export`, { destination, excerpt_length: excerptLength })
      notify(`已导出 ${result.count} 条成品到 ${result.destination}；可点「打开目录」查看`)
    } catch (error) {
      notify((error as Error).message)
    } finally { setBusy(false) }
  }

  /** 在后端所在机器的资源管理器中打开导出目录。 */
  const openExportDir = async () => {
    try {
      await apiPost('/api/system/open-folder', { path: exportDir.trim() })
      notify(`已打开：${exportDir.trim()}`)
    } catch (error) { notify((error as Error).message) }
  }

  const buildArchive = async () => {
    setBusy(true)
    try {
      const result = await apiPost<{ archive: string; count: number; url: string | null }>(`/api/projects/${projectId}/outputs/archive`, { excerpt_length: excerptLength })
      setZipUrl(result.url); notify(`已打包 ${result.count} 条成品，点击下方链接下载`)
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  const sourceLabel = (item: OutputRow) => {
    if (item.source === 'batch') return `批量生成 · 任务 #${String(item.source_position ?? item.position).padStart(4, '0')}`
    if (item.source === 'single') return '单句成品'
    return '补录单句'
  }

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
            <p>{item.text}</p>
            <small>
              {item.language} · {sourceLabel(item)}
              {item.version ? ` · 第 ${item.version} 版` : ''}
              {item.status === 'failed' && <span className="error-text"> · 生成失败：{item.error}</span>}
            </small>
          </div>
          <AudioPlayer url={fileUrl(item.url ?? null)} wide />
          <span className="cell-actions">
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
          placeholder="暂无已固化声线，请先到「声线」页固化"
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

  const exportPanel = <div className="panel sub-panel">
    <div className="panel-heading">⚙ 导出设置：目录、文件名截取字数</div>
    <Label>导出目录（导出时弹窗选择后自动填入；备用：可手动填写）</Label>
    <div className="export-dir-row">
      <input value={exportDir} onChange={(event) => setExportDir(event.target.value)} placeholder="点击下方「导出全部成品」弹出文件夹选择框" />
      <Button small disabled={busy || !exportDir.trim()} onClick={openExportDir} title="在本机资源管理器中打开该文件夹">打开目录</Button>
    </div>
    <Label>文件名中的台词截取字数（5-10）</Label>
    <input type="number" min={5} max={10} value={excerptLength} onChange={(event) => setExcerptLength(Number(event.target.value))} />
    <p className="hint">命名格式：序号_人物名字_朗读文本前N字.mp3（序号不补零，按上方当前顺序从 1 开始编号）</p>
    <div className="button-row four">
      <Button primary disabled={busy} onClick={exportAll}>{busy ? (pickingFolder ? '等待选择文件夹…' : '正在导出…') : '导出全部成品…'}</Button>
      <Button disabled={busy} onClick={buildArchive}>生成全部成品 ZIP</Button>
      {zipUrl && <Button primary href={fileUrl(zipUrl) ?? '#'} download>▣ 全部成品下载 ZIP</Button>}
    </div>
  </div>

  if (audioOnly) return <>
    <SectionTitle title="成品" description="生成的配音自动进入成品：可试听、上下移动排序、逐条下载或按序号批量删除；补录台词即时生成并插入对应位置；导出按当前顺序编号。" />
    {audioList}
    {supplementPanel}
    {exportPanel}
    {dialogElement}
  </>

  return <>
    <SectionTitle number="4" title="成品导出" description="批量生成的配音会自动选入成品，在这里和已保留的单句成品、补录单句一起试听与排序；需要时复制到指定本机目录或打包 ZIP。" />
    <div className="output-summary">
      <div><span>已生成成品</span><strong>{items.length}</strong></div>
      <div><span>当前项目</span><strong>{projectName}</strong></div>
      <div><span>输出格式</span><strong>MP3 / 128kbps</strong></div>
    </div>
    {audioList}
    {supplementPanel}
    {exportPanel}
    {dialogElement}
  </>
}
