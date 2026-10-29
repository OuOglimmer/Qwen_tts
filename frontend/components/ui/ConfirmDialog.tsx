'use client'

import { useCallback, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from './Button'
import { Modal } from './Modal'

export type ConfirmOptions = {
  title: string
  message: ReactNode
  confirmText?: string
  cancelText?: string
  /** 破坏性操作（删除/清空）时确认按钮显示为红色。 */
  danger?: boolean
}

/** 应用内确认弹窗：替代浏览器原生 window.confirm，风格与浅色主题一致。
 * Esc 键与点击遮罩视为取消；由 useConfirmDialog 返回的 dialogElement 负责渲染。
 */
export function ConfirmDialog({ open, title, message, confirmText = '确定', cancelText = '取消', danger = false, onConfirm, onCancel }: ConfirmOptions & {
  open: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  if (!open) return null
  return <Modal
    title={title}
    onClose={onCancel}
    actions={<>
      <Button onClick={onCancel}>{cancelText}</Button>
      {danger
        ? <Button danger onClick={onConfirm}>{confirmText}</Button>
        : <Button primary onClick={onConfirm}>{confirmText}</Button>}
    </>}
  >{message}</Modal>
}

/** window.confirm 的应用内替代：`if (!(await confirm({ title, message }))) return`，true=确认。
 * 返回的 dialogElement 必须渲染在组件树里（遮罩是 fixed 定位，位置不限）。
 */
export function useConfirmDialog() {
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const resolverRef = useRef<((confirmed: boolean) => void) | null>(null)

  const confirm = useCallback((next: ConfirmOptions) => new Promise<boolean>((resolve) => {
    // 弹窗打开期间又来一次确认时，前一个等待者按取消处理，避免悬挂。
    resolverRef.current?.(false)
    resolverRef.current = resolve
    setOptions(next)
  }), [])

  const settle = useCallback((confirmed: boolean) => {
    setOptions(null)
    resolverRef.current?.(confirmed)
    resolverRef.current = null
  }, [])

  const dialogElement = <ConfirmDialog
    open={options !== null}
    title={options?.title ?? ''}
    message={options?.message ?? ''}
    confirmText={options?.confirmText}
    cancelText={options?.cancelText}
    danger={options?.danger}
    onConfirm={() => settle(true)}
    onCancel={() => settle(false)}
  />

  return { confirm, dialogElement }
}
