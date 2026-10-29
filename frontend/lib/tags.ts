// 标签读取：后端 /api/tags 一次给「组 + 词条 + 使用计数」。
// 页面上多处要用同一份（上传弹窗、项目标签、槽位标签），所以在传输层之上再加一层
// 短 TTL 缓存，避免每开一次弹窗就打一遍后端；写操作后调 invalidateTagCache() 失效。
import { apiGet } from './api'
import type { Tag, TagGroup, TagScope } from './types'

const TTL = 30_000
const CACHE = new Map<string, { at: number; data: TagGroup[] }>()

export async function loadTagGroups(scope?: TagScope, force = false): Promise<TagGroup[]> {
  const key = scope ?? 'all'
  const hit = CACHE.get(key)
  if (!force && hit && Date.now() - hit.at < TTL) return hit.data
  const data = await apiGet<TagGroup[]>(`/api/tags${scope ? `?scope=${scope}` : ''}`, { force })
  CACHE.set(key, { at: Date.now(), data })
  return data
}

export function invalidateTagCache(): void {
  CACHE.clear()
}

/** tag_id → tag（带上所属组名，表格/胶囊需要显示来源维度）。 */
export function tagIndex(groups: TagGroup[]): Map<string, Tag> {
  const index = new Map<string, Tag>()
  for (const group of groups) {
    for (const tag of group.tags) index.set(tag.id, { ...tag, group_name: group.name })
  }
  return index
}

/** 组内单选时的互斥集合：同一组里除自己以外的 id。 */
export function siblingsOf(groups: TagGroup[], tagId: string): string[] {
  for (const group of groups) {
    if (group.tags.some((tag) => tag.id === tagId)) {
      return group.tags.filter((tag) => tag.id !== tagId).map((tag) => tag.id)
    }
  }
  return []
}

/** 标签名映射回组：TagInput 里点一个建议时，要知道它是不是单选组。 */
export function groupOfName(groups: TagGroup[], name: string): TagGroup | undefined {
  return groups.find((group) => group.tags.some((tag) => tag.name === name))
}

/** 逗号串（旧契约）→ 标签名数组。 */
export function splitTags(value: string | undefined): string[] {
  return (value || '').split(',').map((tag) => tag.trim()).filter(Boolean)
}

/** 声线胶囊上要显示的名字：优先关系表，退化为缓存列。 */
export function voiceTagNames(voice: { tag_list?: Tag[]; tags?: string }): string[] {
  if (voice.tag_list && voice.tag_list.length > 0) return voice.tag_list.map((tag) => tag.name)
  return splitTags(voice.tags)
}
