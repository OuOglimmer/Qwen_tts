import { AudioPlayer } from './AudioPlayer'
import { Button } from './Button'
import { Label } from './Field'

/** 右侧输出面板：试听播放器 + 下载链接；无结果时显示占位文案。
 *  占位文案别用「♧」这类纯符号——它没有任何信息量，用户看不出这里该出现什么。 */
export function OutputBox({ label, url, placeholder = '生成结果会显示在这里', downloadText = '下载' }: {
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
        : <div className="output-empty">{placeholder}</div>}
    </div>
  )
}
