'use client'

import { useEffect } from 'react'
import type { ReactNode } from 'react'

/** 轻量受控弹窗：承载需要输入/选择的对话（ConfirmDialog 只管纯确认）。
 *  Esc 与点击遮罩关闭；actions 放按钮区。
 *  fixed：钉死外框尺寸，框内内容增删都不再改变弹窗大小（超出部分在框内滚动）。
 *  需要用户连续录入的表单应当开启——否则"标签多到换行 / 建议行出现 / 备注框被拖高"
 *  都会把卡片一路撑大，弹窗在交互过程中不停跳尺寸。 */
export function Modal({ title, children, actions, onClose, wide = false, fixed = false }: {
  title: string
  children: ReactNode
  actions?: ReactNode
  onClose: () => void
  wide?: boolean
  fixed?: boolean
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const cardClass = ['dialog-card', wide && 'dialog-wide', fixed && 'dialog-fixed'].filter(Boolean).join(' ')

  return <div className="dialog-overlay" role="presentation" onClick={onClose}>
    <div className={cardClass} role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
      <h3>{title}</h3>
      <div className="dialog-message">{children}</div>
      {actions && <div className="dialog-actions">{actions}</div>}
    </div>
  </div>
}
