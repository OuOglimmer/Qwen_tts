export type Page =
  | '声线'
  | '配音'
  | '成品'
  | '声线资产'
  | '上传声线'
  | '项目管理'
  | 'VoiceDesign'
  | 'VoiceClone'
  | '音频转换'
  | '系统状态'

export const NAV_GROUPS: { title: string; items: Page[] }[] = [
  { title: '工作台', items: ['声线', '配音', '成品'] },
  { title: '资产', items: ['声线资产', '上传声线'] },
  { title: '项目', items: ['项目管理'] },
  { title: '工具', items: ['VoiceDesign', 'VoiceClone', '音频转换'] },
  { title: '系统', items: ['系统状态'] },
]
