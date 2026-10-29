// 角色归类：第 2 步任务表与第 3 步配音结果共用。
// 「角色」= 槽位名（project_voices.display_name），后端写 batch / outputs 时就落成 voice_name
// （studio/batch.py: COALESCE(r.display_name, v.name) AS voice_name），所以前端不必再查一遍。

/** 「全部」的哨兵值。不用 '' 是为了和"还没选过"区分，也避免角色名恰好为空串时撞上。 */
export const ALL_ROLES = '__all__'

export type RoleGroup = {
  /** 筛选值 = voice_name */
  key: string
  name: string
  count: number
  /** 组内混着多个 voice_id，或有任意一条标着 replaced：这个角色的音色可能不一致。 */
  stale: boolean
}

type RoleItem = { voice_id: string; voice_name: string; roster_state?: string; voice_state?: string }

/**
 * 按角色归类。**键是名字不是 id**：一次换版会让同一角色出现新旧两个 voice_id，
 * 按 id 分会冒出两个同名胶囊，人却只认一个角色。
 * 顺序 = 台词首次出现的顺序（入参本就按 position 排好），不按名字重排。
 */
export function groupRoles(items: RoleItem[]): RoleGroup[] {
  const groups = new Map<string, { count: number; ids: Set<string>; stale: boolean }>()
  for (const item of items) {
    const key = item.voice_name || '未命名声线'
    const group = groups.get(key) ?? { count: 0, ids: new Set<string>(), stale: false }
    group.count += 1
    group.ids.add(item.voice_id)
    if (item.roster_state === 'replaced' || item.voice_state === 'replaced') group.stale = true
    groups.set(key, group)
  }
  return [...groups.entries()].map(([key, group]) => ({
    key,
    name: key,
    count: group.count,
    stale: group.stale || group.ids.size > 1,
  }))
}

/** 只留选中角色的条目；ALL_ROLES 原样返回。序号（position）不重编，跳号是正常的。 */
export function filterByRole<T extends RoleItem>(items: T[], role: string): T[] {
  return role === ALL_ROLES ? items : items.filter((item) => item.voice_name === role)
}

/**
 * 当前筛选角色在清单里对应的**有效**声线 id，用于联动「绑定声线」「按人物批量返修」。
 * 名字取值与 VoiceSelect 的下拉标签保持一致（display_name 优先）：清单接口的 name 是
 * 声线资产的原始名，项目里改过名的槽位只有 display_name 才对得上 voice_name。
 * 换版后同名声线只剩一条 active，取到的就是当前版本；角色已从清单移除时返回 null（不联动）。
 */
export function activeVoiceIdOfRole(
  role: string,
  voices: { id: string; name: string; display_name?: string | null }[],
): string | null {
  if (role === ALL_ROLES) return null
  return voices.find((voice) => (voice.display_name || voice.name) === role)?.id ?? null
}

/** 角色被删空（行全删完、换项目）时回落到「全部」，避免停在一张空列表上。 */
export function resolveRole(role: string, roles: RoleGroup[]): string {
  return role === ALL_ROLES || roles.some((group) => group.key === role) ? role : ALL_ROLES
}
