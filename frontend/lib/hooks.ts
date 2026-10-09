'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Run an async task on a fixed cadence. The task closure is refreshed every
 * render, so callers can close over fresh state without re-creating the
 * timer. Errors inside the task are swallowed — polling must survive a
 * transient backend blip.
 */
export function usePolling(task: () => Promise<void>, intervalMs: number, enabled = true) {
  const taskRef = useRef(task)
  taskRef.current = task

  useEffect(() => {
    if (!enabled) return
    let timer: number | null = null
    let stopped = false
    // 链式 setTimeout：上一次轮询完成后再排下一次。固定 setInterval 在单次
    // 请求慢于间隔时会堆积并发请求，慢响应还可能乱序返回顶掉新数据。
    const tick = () => {
      taskRef.current()
        .catch(() => undefined)
        .finally(() => {
          if (!stopped) timer = window.setTimeout(tick, intervalMs)
        })
    }
    timer = window.setTimeout(tick, intervalMs)
    return () => {
      stopped = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [enabled, intervalMs])
}

/**
 * 全局轻提示：2.6 秒后自动消失；连续提示时从最后一次重新计时，内容不同的
 * 提示不会互相清除。返回的 notify 由组合根注入各页面，Toast 展示在 app 外壳里。
 */
export function useToast() {
  const [notice, setNotice] = useState('')
  const timerRef = useRef<number | null>(null)
  useEffect(() => () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
  }, [])
  const notify = useCallback((value: string) => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    setNotice(value)
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      setNotice((current) => (current === value ? '' : current))
    }, 2600)
  }, [])
  return { notice, notify }
}
