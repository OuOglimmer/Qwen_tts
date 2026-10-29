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
  /** 上次离开时所在步骤（后端：显式记录优先，否则按数据推断）。 */
  last_step: number
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
  /** 这个槽位换掉过几把声线：>0 才显示「查看历史声线」入口。 */
  history_count?: number
  /** 槽位标签：需求优先级（request 域）+ 角色定位/出场分组（project 域，固化时上移）。 */
  tag_list?: Tag[]
  tag_ids?: string[]
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

/** 标签域：资产固有 / 项目语义 / 需求阶段。同一个词在不同域里是两个身份。 */
export type TagScope = 'voice' | 'project' | 'request'

export type Tag = {
  id: string
  group_id: string
  name: string
  color: string
  scope: TagScope
  /** 0 = 单选组（同一实体只能选一个），1 = 多选组。 */
  multi: number
  usage?: number
  group_name?: string
  /** 仅项目标签：manual = 项目里手动打的，inherited = 从槽位继承来的。 */
  origin?: string
}

export type TagGroup = {
  id: string
  scope: TagScope
  name: string
  multi: number
  sort: number
  builtin: number
  tags: Tag[]
}

export type VoiceAsset = {
  id: string
  name: string
  /** 项目清单内的展示名（GET /api/projects/{id}/voices 才有）。 */
  display_name?: string
  /** 来源项目名；公共库时后端给 "公共库"，来源项目已删除时给 "XX（已删除）"。 */
  source_display?: string
  /** 全局库列表（/api/library/voices）才有：被多少项目清单引用。 */
  usage_count?: number
  project_id?: string | null
  source_project_id?: string | null
  source: string
  /** 试听文本的语种：只决定「试听那一段」用什么语言朗读。 */
  language: string
  /** 参考音频本身的语种，纯资产标注；历史声线为空串，界面显示「未标注」。 */
  ref_language?: string
  prompt: string | null
  ref_text: string | null
  /** 声线域标签名的派生缓存（逗号串，兼容旧代码）；新代码读 tag_list。 */
  tags: string
  /** 声线域标签（资产固有：女声/低沉…）。 */
  tag_list?: Tag[]
  tag_ids?: string[]
  /** 项目语义标签（角色定位/出场分组），只在项目清单接口里返回，全库页只读展示。 */
  project_tags?: Tag[]
  project_tag_ids?: string[]
  /** 清单状态：active = 当前在用，replaced = 换版后的历史版本。 */
  roster_state?: 'active' | 'replaced'
  replaced_by?: string | null
  notes: string
  status: string
  preview_url: string | null
  reference_url: string | null
  created_at: string
}

/** 槽位用过的旧声线（已 replaced，全局库默认不列，只能从槽位翻出来）。
 *  bound_outputs = 还有多少条成品挂着它，>0 时不允许删。 */
export type VoiceHistoryEntry = {
  id: string
  name: string
  status: string
  source: string
  language: string
  prompt: string | null
  notes: string
  created_at: string
  preview_url: string | null
  reference_url: string | null
  bound_outputs: number
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
  /** 生成时用的清单状态与声线当前状态（replaced = 已换版）。 */
  roster_state?: string
  voice_state?: string
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
  /** 生成时声线在清单里的状态；replaced = 这条成品的声线后来换版了。 */
  roster_state?: string
  /** 声线资产/清单的当前状态（replaced = 已换版）。 */
  voice_state?: string
  /** 换版后的新声线 id（仅用于在角标里解释"被哪一版取代"）。 */
  replaced_by?: string | null
  voice_id?: string
  /** 这把声线是同类第几版（同角色固化顺序），0 = 查不到（历史脏数据）。 */
  voice_version?: number
  /** 保留的历史版本（含当前版，最新在前；后端只留最近 5 版）。仅批量行有。 */
  versions?: BatchItemVersion[]
}

export type OutputsResponse = { items: OutputRow[] }

/** 导出前检查：只报告事实，不拦导出。 */
export type ExportPreflight = {
  total: number
  deduped: number
  deduped_samples: { voice: string; text: string }[]
  mixed: boolean
  roles: {
    name: string
    total: number
    current_version: number
    mixed: boolean
    versions: { version: number; voice_id: string; count: number; is_current: boolean }[]
  }[]
}

/** 一条待写入本机文件夹的成品：file 是最终文件名，url 指向后端音频。
 *  这些字段只用于在前端决定写什么名字、按什么倍率取音频；不再落成 manifest.json。 */
export type ExportPlanFile = {
  position: number
  source: string
  source_position: number | null
  voice: string
  /** 混版交付追溯用：这条音频是哪个声线资产、第几版录的。 */
  voice_id: string | null
  voice_version: number
  text: string
  language: string
  version: number | null
  tag_prefix: string
  /** 这份导出副本的播放倍率（1.0 = 原速）。源文件不受影响。 */
  speed: number
  file: string
  url: string | null
}

export type ExportPlanResponse = { files: ExportPlanFile[]; count: number }

/** 单句成品行：GET /api/projects/{id}/single-outputs 的响应。
 *  第 3 步的「单句成品」盒子已移除，界面上不再单独拉这份列表（成品页走 outputs 汇总），
 *  类型留给脚本/调试与后端契约对照。 */
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
