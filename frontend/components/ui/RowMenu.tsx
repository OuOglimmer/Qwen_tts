'use client'

import { useEffect, useRef, useState } from 'react'

export type RowMenuItem = {
  label: string
  onClick: () => void
  danger?: boolean
}

/** 行内「···」菜单：触发器留在行里，弹层用 position:fixed 锚定视口。
 *  绝对定位版本会被表格外层 `.table-wrap{overflow:auto}` 裁掉，并且把表格撑出
 *  横向/纵向滚动条（浮层一开整块跟着变形）。所以与 .xselect-panel、.audio-volume-pop
 *  走同一套手法：打开瞬间量一次触发器坐标，用 fixed 画在页面上层。
 *  锚定坐标只在打开时算一次，滚动 / 缩放直接关闭（与原生菜单一致）。 */
export function RowMenu({ items, title = '更多操作' }: { items: RowMenuItem[]; title?: string }) {
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // 面板高度估算：与 theme.css 里 .row-menu button 的行高（31px）+ 内边距 4px*2 + 描边 2px 对应
  const GAP = 4
  const ROW_H = 31
  const PAD = 10

  const open = () => {
    const el = triggerRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const viewH = document.documentElement.clientHeight
    const height = items.length * ROW_H + PAD
    const below = viewH - r.bottom - GAP
    const up = below < height && r.top > below
    // 右边缘对齐靠 CSS 的 translateX(-100%)（按面板自身宽度左移），不用 right：
    // fixed 的包含块宽度比 documentElement.clientWidth 少一条纵向滚动条，
    // 用 right 算会恒偏 15px（实测）。
    setAnchor({ top: up ? r.top - height - GAP : r.bottom + GAP, left: r.right })
  }

  useEffect(() => {
    if (!anchor) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      setAnchor(null)
    }
    const close = () => setAnchor(null)
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setAnchor(null) }
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [anchor])

  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        className={anchor ? 'row-menu-button is-open' : 'row-menu-button'}
        title={title}
        aria-haspopup="menu"
        aria-expanded={!!anchor}
        onClick={() => (anchor ? setAnchor(null) : open())}
      >
        ···
      </button>
      {anchor && items.length > 0 && (
        <div className="row-menu" role="menu" ref={panelRef} style={{ top: anchor.top, left: anchor.left }}>
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={item.danger ? 'danger' : undefined}
              onClick={() => { setAnchor(null); item.onClick() }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </>
  )
}
