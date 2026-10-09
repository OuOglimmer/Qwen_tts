export function AudioPlayer({ url, wide = false }: { url: string | null | undefined; wide?: boolean }) {
  if (!url) return <span className="hint">暂无音频</span>
  // preload="metadata"：只取时长元数据，不预载音频体，保证时长显示准确。
  // 播放互斥：开始播放时暂停页面上其他音频，保证同一时间只有一个音频在响。
  return (
    <audio
      className="cell-audio"
      controls
      preload="metadata"
      src={url}
      style={wide ? undefined : { width: 200 }}
      onPlay={(e) => {
        const el = e.currentTarget
        document.querySelectorAll('audio').forEach((a) => {
          if (a !== el) a.pause()
        })
        // 暂停会保留进度；重新点击播放时归零，从头完整重播。
        if (el.currentTime > 0) el.currentTime = 0
      }}
    />
  )
}
