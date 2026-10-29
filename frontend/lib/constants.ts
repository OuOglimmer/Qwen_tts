export const LANGUAGES = ['Chinese', 'English', 'Japanese', 'Korean', 'German', 'French', 'Russian', 'Portuguese', 'Spanish', 'Italian', 'Auto'] as const

export type Language = (typeof LANGUAGES)[number]

/** 语言的界面显示名。下拉框与提示文案用中文，写库/传后端的仍是上面的英文枚举值。 */
export const LANGUAGE_LABELS: Record<string, string> = {
  Chinese: '中文',
  English: '英语',
  Japanese: '日语',
  Korean: '韩语',
  German: '德语',
  French: '法语',
  Russian: '俄语',
  Portuguese: '葡萄牙语',
  Spanish: '西班牙语',
  Italian: '意大利语',
  Auto: '自动识别',
}

/** 参考音频/转换工具统一接受的音频扩展名（与后端校验保持一致）。 */
export const AUDIO_ACCEPT = '.wav,.mp3,.flac,.ogg,.m4a,.aac,.wma'
