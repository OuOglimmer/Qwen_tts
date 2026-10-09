// 领域类型：与后端 FastAPI 的响应结构一一对应，只描述数据，不含任何请求逻辑。
// 各页面组件统一从这里取类型，避免每个文件重复声明（如曾经的 type Notify）。

/** 全局轻提示回调，由 App 组合根注入，页面只管调用。 */
export type Notify = (value: string) => void

/** 行内编辑草稿：配音任务表（BatchTable）与试听返修行（ResultRow）共用同一形状。 */
export type LineEdit = { text: string; language: string }

export type ProjectSummary = {
  id: string
  name: string
  status: string
  slot_count: number
  voice_count: number
  single_count: number
  task_count: number
  completed_count: number
  last_activity: string
}

export type VoiceRequest = {
  id: string
  project_id: string
  position: number
  name: string
  prompt: string
  audition_text: string
  language: string
  status: string
  voice_id: string | null
  direct_output_mp3: string | null
  direct_output_url: string | null
  candidate_count: number
  shortlisted_count: number
  selected_candidate_id: string | null
  updated_at?: string | null
}

export type Candidate = {
  id: string
  request_id: string
  seed: number
  preview_path: string
  preview_url: string | null
  selected: number
  shortlisted: number
  created_at: string
}

export type VoiceAsset = {
  id: string
  name: string
  source: string
  language: string
  prompt: string | null
  ref_text: string | null
  tags: string
  notes: string
  status: string
  preview_url: string | null
  reference_url: string | null
  created_at: string
}

export type BatchItemVersion = {
  version: number
  created_at: string
  url: string | null
}

export type BatchItem = {
  id: string
  position: number
  voice_id: string
  voice_name: string
  text: string
  language: string
  status: string
  output_mp3: string | null
  output_url: string | null
  error: string | null
  /** 当前音频版本号：首次生成为 1，每返修一次加 1；未生成过为 0。 */
  version: number
  /** 保留的历史版本（含当前版，最新在前；后端只留最近 3 版）。 */
  versions?: BatchItemVersion[]
}

export type BatchTask = {
  project_id: string
  total: number
  succeeded: number
  failed: number
  error: string | null
  cancelled: boolean
  done: boolean
  running: boolean
} | null

export type BatchStatus = { task: BatchTask; counts: Record<string, number>; running: boolean }

export type VoiceGenTask = {
  project_id: string
  request_id: string
  name: string
  total: number
  generated: number
  failed: number
  error: string | null
  cancelled: boolean
  finished: boolean
  running: boolean
} | null

export type RegenProgress = {
  voice_name: string
  position: number | null
  processed: number
  total: number
  succeeded: number
  failed: number
  error: string | null
  cancelled: boolean
  done: boolean
} | null

export type OutputRow = {
  id?: string
  position: number
  voice_name?: string
  name?: string
  text: string
  language: string
  status?: string
  error?: string | null
  source: string
  source_position?: number | null
  output_url?: string | null
  url?: string | null
  download_path?: string | null
  /** 手动排序键：batch:<任务id> / single:<请求id> / supplement:<补录id> */
  order_key?: string
  version?: number | null
}

export type OutputsResponse = { items: OutputRow[]; export_dir: string }

/** 单句成品行：GET /api/projects/{id}/single-outputs 的响应。 */
export type SingleOutput = {
  id: string
  position: number
  name: string
  text: string
  language: string
  direct_output_mp3: string | null
  url: string | null
}

export type ModelHealth = { model: string; cuda: boolean; gpu: string; allocated_gib: number }

export type SystemStatus = { model: ModelHealth; data_dir: string; projects: number }
