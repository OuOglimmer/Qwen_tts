export const LANGUAGES = ['Chinese', 'English', 'Japanese', 'Korean', 'German', 'French', 'Russian', 'Portuguese', 'Spanish', 'Italian', 'Auto'] as const

export type Language = (typeof LANGUAGES)[number]

/** 参考音频/转换工具统一接受的音频扩展名（与后端校验保持一致）。 */
export const AUDIO_ACCEPT = '.wav,.mp3,.flac,.ogg,.m4a,.aac,.wma'
