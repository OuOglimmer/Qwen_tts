import type { ReactNode } from 'react'

export function Button({ children, primary = false, small = false, disabled = false, selected = false, danger = false, title, href, download, onClick }: {
  children: ReactNode
  primary?: boolean
  small?: boolean
  disabled?: boolean
  selected?: boolean
  /** 破坏性操作（删除/彻底删除）的红色按钮。 */
  danger?: boolean
  /** 悬停提示，用于 ↑↓ 等纯符号按钮。 */
  title?: string
  /** 传入时渲染为 <a>（下载/外链），与按钮共用同一套 .btn 外观。 */
  href?: string
  /** 配合 href 触发浏览器下载。 */
  download?: boolean
  onClick?: () => void
}) {
  const className = `${small ? 'btn mini-btn' : 'btn'}${primary ? ' primary' : ''}${selected ? ' selected' : ''}${danger ? ' danger' : ''}`
  if (href !== undefined) return <a className={className} href={href} download={download} title={title}>{children}</a>
  return <button className={className} disabled={disabled} title={title} onClick={onClick}>{children}</button>
}
