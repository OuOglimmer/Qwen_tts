export const STEPS = [
  { key: 1, label: '创建声线' },
  { key: 2, label: '配音生成' },
  { key: 3, label: '试听返修' },
  { key: 4, label: '成品导出' },
] as const

export type Step = (typeof STEPS)[number]['key']
