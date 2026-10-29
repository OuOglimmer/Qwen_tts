// 声线提示词助手的共用落地流程：模板/维度组装结果在覆盖已编辑提示词前，
// 统一走应用内确认弹窗；配套试听文本只在"未填写"时自动填入，不覆盖已写内容。
// 两个调用方（声线工作台表单、「全部声线」页的文字描述生成）只在"什么算未编辑"上有差异。

type Confirm = (options: { title: string; message: string; confirmText: string }) => Promise<boolean>

/** 返回是否真正写入了提示词：用户在覆盖确认里取消时返回 false，调用方据此决定是否收起助手弹窗。 */
export async function applyPromptAssist(args: {
  confirm: Confirm
  currentPrompt: string
  nextPrompt: string
  nextAudition: string
  currentAudition: string
  setPrompt: (value: string) => void
  setAudition: (value: string) => void
  /** 提示词仍等于该值时视为未编辑过，覆盖前不弹确认（表单为空串，独立工具为默认提示词）。 */
  untouchedPrompt?: string
  /** 试听文本视作未填写（可被自动填入）的判断；默认仅空白串。 */
  untouchedAudition?: (value: string) => boolean
}): Promise<boolean> {
  const current = args.currentPrompt.trim()
  if (
    current
    && current !== args.untouchedPrompt
    && current !== args.nextPrompt.trim()
    && !(await args.confirm({
      title: '覆盖当前提示词？',
      message: '模板内容将替换正在编辑的提示词，原内容不会被保留。',
      confirmText: '覆盖填入',
    }))
  ) return false
  args.setPrompt(args.nextPrompt)
  const untouched = args.untouchedAudition ?? ((value: string) => !value.trim())
  if (args.nextAudition && !untouched(args.currentAudition)) args.setAudition(args.nextAudition)
  return true
}
