// 表单草稿的 localStorage 持久化：纯模块，不依赖 React。
// 按项目持久化表单草稿：切换页面/步骤、刷新浏览器后都能恢复未保存的输入。

export type VoiceDraft = {
  activeRequestId: string | null
  name: string
  prompt: string
  auditionText: string
  language: string
  count: number
}

const draftKey = (projectId: string) => `voice-draft:${projectId}`
const SEEN_TASK_KEY = 'voice-gen-last-seen-request'

export const readDraft = (projectId: string): VoiceDraft | null => {
  try {
    const raw = window.localStorage.getItem(draftKey(projectId))
    const draft = raw ? (JSON.parse(raw) as VoiceDraft) : null
    return draft && typeof draft === 'object' ? draft : null
  } catch { return null }
}

export const writeDraft = (projectId: string, draft: VoiceDraft) => {
  try { window.localStorage.setItem(draftKey(projectId), JSON.stringify(draft)) } catch { /* 隐私模式等存储不可用时静默降级 */ }
}

export const readSeenTask = (): string | null => {
  try { return window.localStorage.getItem(SEEN_TASK_KEY) } catch { return null }
}

export const writeSeenTask = (requestId: string) => {
  try { window.localStorage.setItem(SEEN_TASK_KEY, requestId) } catch { /* 存储不可用时静默降级 */ }
}
