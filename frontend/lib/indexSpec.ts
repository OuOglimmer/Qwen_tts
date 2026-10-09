// 批量序号输入解析：配音任务表与成品列表共用。
// 支持 "3"、"1,3,5"、"2~5"/"2-5"/"2到5"（区间含两端）。序号一律从 1 开始，
// 与页面展示的序号列、导出文件名的编号保持一致。

const TOKEN = /^(\d+)\s*(?:[~～\-–—到]\s*(\d+))?$/

/**
 * 把用户输入解析成去重升序的序号数组，max 是当前最大的有效序号。
 * 配音任务表的序号在删行后会有空洞，所以这里只校验 1~max，
 * 具体序号是否存在由调用方/后端兜底报错。
 * 解析失败或越界时返回错误文案（可直接 notify），而不是抛异常。
 */
export function parseIndexSpec(input: string, max: number): number[] | string {
  const cleaned = input.trim()
  if (!cleaned) return '请输入要删除的序号，例如：1,3,5~8'
  if (max <= 0) return '当前列表为空，没有可删除的条目'

  const picked = new Set<number>()
  for (const raw of cleaned.split(/[,，、\s]+/).filter(Boolean)) {
    const match = TOKEN.exec(raw)
    if (!match) return `无法识别的序号「${raw}」，请用数字或 a~b 区间写法`
    const start = Number(match[1])
    const end = match[2] === undefined ? start : Number(match[2])
    if (start === 0 || end === 0) return `序号从 1 开始（范围 1~${max}），没有 0 号`
    if (start > end) return `区间「${raw}」写反了，应为 小~大（如 3~6）`
    if (start > max || end > max) {
      return `序号超出范围：范围为 1~${max}，收到「${raw}」`
    }
    for (let value = start; value <= end; value += 1) picked.add(value)
  }
  return Array.from(picked).sort((a, b) => a - b)
}
