import { AudioPlayer } from './AudioPlayer'
import { Button } from './Button'
import { Label } from './Field'

/** 右侧输出面板：试听播放器 + 下载链接；无结果时显示占位文案。 */
export function OutputBox({ label, url, placeholder = '尚未生成', downloadText = '下载' }: {
  label: string
  url: string | null | undefined
  placeholder?: string
  downloadText?: string
}) {
  return (
    <div className="download-box">
      <Label>{label}</Label>
      {url
        ? <>
          <AudioPlayer url={url} wide />
          <Button href={url} download>{downloadText}</Button>
        </>
        : <div>{placeholder}</div>}
    </div>
  )
}
