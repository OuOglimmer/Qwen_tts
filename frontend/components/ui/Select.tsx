'use client'

import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

export type SelectOption = { value: string; label: string }

/** 自绘下拉单选：替代原生 <select>。
 *  原生 select 的关闭态项目已用 token 美化，但展开后的选项弹层是操作系统画的
 *  （Windows Chrome/Edge 基本不允许网页改它的圆角 / 阴影 / 高亮条），与项目风格脱节。
 *  这里把整层弹层自绘：白底 + 1px 细描边 + 10px 圆角 + 阴影 + accent-soft 高亮。
 *  交互对齐原生：↑↓/Home/End 移动、Enter/Space 选中、Esc 关闭、点击外部关闭、
 *  下方放不下自动向上翻。面板用 position:fixed 锚定触发器（同 .audio-volume-pop 的手法），
 *  不会被滚动容器的 overflow 裁掉。
 *  关闭态外观与主题里 select 的统一外观保持一致（36px 高 / 8px 圆角 / 自绘箭头）。 */
export function Select({ value, onChange, options, placeholder, className, disabled, ariaLabel }: {
  value: string
  onChange: (value: string) => void
  options: SelectOption[]
  /** value 不在 options 里时（含未选择）显示的占位文案 */
  placeholder?: string
  className?: string
  disabled?: boolean
  ariaLabel?: string
}) {
  const [open, setOpen] = useState(false)
  // 面板锚点：触发器的视口坐标 + 是否向上翻。打开瞬间算一次即可（滚动 / 缩放直接关闭，与原生行为一致）
  const [anchor, setAnchor] = useState<{ left: number; top: number; width: number } | null>(null)
  const [active, setActive] = useState(-1)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 面板尺寸估算：行高 32px + 上下内边距 8px，与 CSS 里 .xselect-option 的 height、
  // .xselect-panel 的 max-height 一一对应（那边改行高这里必须跟着改，否则翻上/翻下会算错）
  const PANEL_GAP = 4
  const PANEL_MAX_H = 272
  const ROW_H = 32

  const current = options.find((option) => option.value === value)

  const openPanel = () => {
    const el = triggerRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const h = Math.min(options.length * ROW_H + 8, PANEL_MAX_H)
    const below = window.innerHeight - r.bottom - PANEL_GAP
    const up = below < h && r.top > below
    const left = Math.max(8, Math.min(r.left, window.innerWidth - Math.max(r.width, 150) - 8))
    setAnchor({ left, top: up ? r.top - h - PANEL_GAP : r.bottom + PANEL_GAP, width: Math.max(r.width, 150) })
    setActive(options.findIndex((option) => option.value === value))
    setOpen(true)
  }

  const closePanel = (refocus = false) => {
    setOpen(false)
    if (refocus) triggerRef.current?.focus()
  }

  const pick = (option: SelectOption) => {
    if (option.value !== value) onChange(option.value)
    closePanel(true)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return
    if (!open) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        openPanel()
      }
      return
    }
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive((i) => Math.min(options.length - 1, i + 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive((i) => Math.max(0, i - 1)) }
    else if (event.key === 'Home') { event.preventDefault(); setActive(0) }
    else if (event.key === 'End') { event.preventDefault(); setActive(options.length - 1) }
    else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      const option = options[active]
      if (option) pick(option)
    }
    else if (event.key === 'Escape' || event.key === 'Tab') closePanel(event.key === 'Escape')
  }

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    // 面板是 fixed 定位，页面一滚就会和触发器脱开——滚动（含页面内滚动容器）直接关闭；
    // 排除面板自身的滚动（选项多时内部滚动列表不能把面板关掉）。
    const onScroll = (event: Event) => {
      if (!listRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onResize = () => setOpen(false)
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open])

  // 键盘移动高亮项时，保证它滚动进可视区（block:nearest 不打扰其它项）
  useEffect(() => {
    if (!open || active < 0) return
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' })
  }, [open, active])

  return (
    <div className={`xselect${className ? ` ${className}` : ''}`} ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        className={`xselect-trigger${open ? ' is-open' : ''}`}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => (open ? closePanel() : openPanel())}
        onKeyDown={onKeyDown}
      >
        <span className={`xselect-value${current ? '' : ' is-placeholder'}`}>{current ? current.label : (placeholder ?? '')}</span>
        <svg className="xselect-caret" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 4.75 6 7.75 9 4.75" />
        </svg>
      </button>
      {open && anchor && (
        <div className="xselect-panel" ref={listRef} role="listbox" style={{ left: anchor.left, top: anchor.top, width: anchor.width }}>
          {options.map((option, index) => (
            <div
              key={option.value}
              role="option"
              aria-selected={option.value === value}
              className={`xselect-option${index === active ? ' is-active' : ''}${option.value === value ? ' is-selected' : ''}`}
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(option)}
            >
              <span className="xselect-option-text">{option.label}</span>
              {option.value === value && (
                <svg className="xselect-check" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M2.5 6.4 5 8.9 9.5 3.4" />
                </svg>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
