'use client'

import { ALL_ROLES, type RoleGroup } from '@/lib/roles'
import { Select } from './Select'

type RoleSelectProps = {
  roles: RoleGroup[]
  total: number
  /** 当前选中：ALL_ROLES 或角色名。 */
  value: string
  onChange: (value: string) => void
}

/** 角色单选下拉：第 2 步任务表、第 3 步配音结果共用。
 *  只有一个角色时整个不渲染 —— 只有一个选项的下拉是纯噪声。
 *  条数写进选项文案，切之前就能看出每个角色有多少条。 */
export function RoleSelect({ roles, total, value, onChange }: RoleSelectProps) {
  if (roles.length < 2) return null
  return <Select
    className="role-select"
    value={value}
    onChange={onChange}
    ariaLabel="按角色筛选"
    options={[
      { value: ALL_ROLES, label: `全部角色（${total} 条）` },
      ...roles.map((role) => ({
        value: role.key,
        // 「含换版」= 这个角色混着新旧两版声线，音色可能不一致（与表格里的「已换版」角标同义）。
        label: `${role.name}（${role.count} 条${role.stale ? ' · 含换版' : ''}）`,
      })),
    ]}
  />
}
