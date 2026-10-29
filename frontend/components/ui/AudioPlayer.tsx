'use client'

import { useEffect, useRef, useState, type CSSProperties } from 'react'

/** 播放器时间文本，mm:ss。 */
function clock(value: number) {
  const total = Math.max(0, Math.floor(value || 0))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

// 浮层半宽：把浮层夹在视口内，贴边的播放器也能看全滑条。
const POP_HALF_WIDTH = 32

const PlayIcon = () => <svg className="audio-ico" viewBox="0 0 16 16"><path d="M4.5 2.6 13 8l-8.5 5.4z" /></svg>
const PauseIcon = () => <svg className="audio-ico" viewBox="0 0 16 16"><path d="M4 2.8h2.6v10.4H4zM9.4 2.8H12v10.4H9.4z" /></svg>

const VolumeIcon = ({ level }: { level: 'off' | 'low' | 'high' }) => (
  <svg className="audio-ico" viewBox="0 0 16 16">
    <path d="M2.6 6.2h2.2L8.6 3.3v9.4L4.8 9.8H2.6z" />
    {level === 'off' && <path d="M10.8 6.2 14.2 9.8M14.2 6.2 10.8 9.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />}
    {level !== 'off' && <path d="M10.6 6.1a3 3 0 0 1 0 3.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />}
    {level === 'high' && <path d="M12.7 4.2a6 6 0 0 1 0 7.6" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />}
  </svg>
)

/**
 * 自绘音频播放器：播放/进度/时长/音量。
 * 音量滑条不常驻，鼠标经过音量图标时以图标为锚点向上浮出，移开即收起。
 * 播放互斥：开始播放时暂停页面上其他音频，保证同一时间只有一个音频在响。
 *
 * ``rate`` 是「按即将导出的倍率试听」用的：只改播放速度，源文件与导出文件都不动。
 * 浏览器播放器默认保音高（preservesPitch），听感与后端 atempo 足够接近，
 * 可以先听再决定要不要按这个倍率导出。
 */
export function AudioPlayer({
  url, wide = false, rate = 1,
}: { url: string | null | undefined; wide?: boolean; rate?: number }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const volumeBtnRef = useRef<HTMLButtonElement>(null)
  const seekedRef = useRef(false)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [pop, setPop] = useState<{ left: number; bottom: number } | null>(null)

  useEffect(() => {
    setTime(0)
    setDuration(0)
    setPlaying(false)
    setPop(null)
  }, [url])

  useEffect(() => {
    const el = audioRef.current
    if (!el) return
    el.volume = volume
    el.muted = muted
    el.playbackRate = rate
    // 显式声明保音高：变速预览的目的是听"语速"，不能让音调跟着飘，
    // 否则预览听感和后端 atempo 的输出不是一回事。
    ;(el as HTMLAudioElement & { preservesPitch?: boolean }).preservesPitch = true
  }, [volume, muted, url, rate])

  // 页面被 keep-alive 切走（display:none）或滑出视口时暂停播放，避免后台继续出声。
  useEffect(() => {
    const el = audioRef.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[0]
      if (!entry.isIntersecting || el.offsetParent === null) {
        if (!el.paused) el.pause()
      }
    }, { threshold: 0 })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  if (!url) return <span className="hint">暂无音频</span>

  const togglePlay = () => {
    const el = audioRef.current
    if (!el) return
    if (el.paused) {
      // 暂停后再播默认从头完整重播；刚拖过进度条则以拖到的位置起播。
      if (!seekedRef.current && el.currentTime > 0 && !el.ended) el.currentTime = 0
      document.querySelectorAll('audio').forEach((other) => {
        if (other !== el) other.pause()
      })
      void el.play()
    } else {
      el.pause()
    }
    seekedRef.current = false
  }

  const openVolumePop = () => {
    const rect = volumeBtnRef.current?.getBoundingClientRect()
    if (!rect) return
    const left = Math.min(Math.max(rect.left + rect.width / 2, POP_HALF_WIDTH + 6), window.innerWidth - POP_HALF_WIDTH - 6)
    setPop({ left, bottom: window.innerHeight - rect.top + 8 })
  }

  const setLevel = (value: number) => {
    setVolume(value)
    setMuted(value === 0)
  }

  const level = muted || volume === 0 ? 'off' : volume < 0.5 ? 'low' : 'high'

  // 进度百分比交给 CSS：轨道用线性渐变按这个值画出"已播段"，替代原生滑条那条粗蓝杠。
  const seekPercent = duration > 0 ? Math.min(100, (time / duration) * 100) : 0

  return (
    <div className={wide ? 'audio-player wide' : 'audio-player'}>
      <audio
        ref={audioRef}
        className="audio-src"
        // preload="metadata"：只取时长元数据，不预载音频体，保证时长显示准确。
        preload="metadata"
        src={url}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
      />
      <button type="button" className="audio-icon-btn" onClick={togglePlay} aria-label={playing ? '暂停' : '播放'} title={playing ? '暂停' : '播放'}>
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      <input
        type="range"
        className="audio-seek"
        min={0}
        max={duration || 0}
        step={0.01}
        value={Math.min(time, duration || 0)}
        aria-label="播放进度"
        style={{ '--seek-pct': `${seekPercent}%` } as CSSProperties}
        onPointerDown={() => { seekedRef.current = true }}
        onChange={(event) => {
          const el = audioRef.current
          if (!el) return
          el.currentTime = Number(event.target.value)
          setTime(Number(event.target.value))
        }}
      />
      <span className="audio-time">{clock(time)}/{clock(duration)}</span>
      <div className="audio-volume" onPointerEnter={openVolumePop} onPointerLeave={() => setPop(null)}>
        <button
          type="button"
          ref={volumeBtnRef}
          className="audio-icon-btn"
          onClick={() => setMuted(!muted)}
          aria-label={muted ? '取消静音' : '静音'}
          title={muted ? '取消静音' : '静音'}
        >
          <VolumeIcon level={level} />
        </button>
        {pop && (
          <div className="audio-volume-pop" style={{ left: pop.left, bottom: pop.bottom }}>
            <span className="audio-volume-value">{Math.round((muted ? 0 : volume) * 100)}</span>
            <input
              type="range"
              className="audio-volume-slider"
              min={0}
              max={1}
              step={0.01}
              value={muted ? 0 : volume}
              aria-label="音量"
              onChange={(event) => setLevel(Number(event.target.value))}
            />
          </div>
        )}
      </div>
    </div>
  )
}
