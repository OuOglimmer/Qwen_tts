/** 声线提示词工程数据层：六段式结构规范、场景模板库与维度词表。
 *
 * 提示词会作为 instruct 传给 VoiceDesign 模型（见 studio/model_manager.py 的
 * design()），是纯展示数据：与后端 LANGUAGES 常量同理，前后端各持一份、
 * 前端自持即可，无需暴露 API。
 */

export type PromptPreset = {
  readonly key: string
  readonly label: string
  /** 完整提示词，直接填入提示词输入框。 */
  readonly prompt: string
  /** 配套试听文本：场景化的句子让候选对比时听得出音色差异。 */
  readonly audition: string
}

export type PromptDimension = {
  readonly key: string
  readonly label: string
  readonly options: readonly string[]
}

/** 六段式结构：[性别年龄]，[音色质感]，[语速节奏]，[语调起伏]，[情绪态度]，标准普通话，[适用场景]。
 * 组装器按此数组顺序拼句，scene 段前自动补"标准普通话"。
 */
export const PROMPT_DIMENSIONS: readonly PromptDimension[] = [
  {
    key: 'voice',
    label: '性别年龄',
    options: ['女童声，约6岁', '女童声，约9岁', '男童声，约7岁', '男童声，约10岁', '青年女声，约20岁', '青年女声，约26岁', '青年女声，约35岁', '青年男声，约24岁', '青年男声，约32岁', '中年女声，约42岁', '中年男声，约45岁'],
  },
  {
    key: 'timbre',
    label: '音色质感',
    options: ['音色柔和温暖', '音色清亮甜美', '音色清脆明快', '音色低沉醇厚', '音色低沉有磁性', '音色圆润饱满', '音色清晰温和', '音色浑厚大气', '音色稚嫩清脆', '音色奶声奶气'],
  },
  {
    key: 'pace',
    label: '语速节奏',
    options: ['语速平缓', '语速适中', '语速稍快', '吐字干脆利落'],
  },
  {
    key: 'tone',
    label: '语调起伏',
    options: ['语调平稳', '语调轻柔自然', '语调自然起伏', '语调抑扬顿挫', '语调上扬有活力'],
  },
  {
    key: 'mood',
    label: '情绪态度',
    options: ['语气温柔亲切', '语气沉稳专业', '语气热情洋溢', '语气俏皮可爱', '语气冷静客观', '语气坚定有力', '语气慵懒随性'],
  },
  {
    key: 'scene',
    label: '适用场景',
    options: ['适合纪录片与人文专题旁白', '适合广告与活动宣传', '适合电话客服与语音导航', '适合有声书与小说朗读', '适合少儿故事讲述', '适合新闻资讯播报', '适合短视频口播解说', '适合企业宣传片配音'],
  },
]

/** 场景模板：prompt 均符合六段式结构，可整句填入后微调。 */
export const VOICE_PROMPT_PRESETS: readonly PromptPreset[] = [
  {
    key: 'documentary',
    label: '纪录片男声',
    prompt: '中年男声，约45岁，音色低沉醇厚，语速平缓，语调沉稳有分量，语气冷静专业，标准普通话，适合纪录片与人文专题旁白。',
    audition: '在这片土地上，时间以另一种方式流淌，山川与河流见证着岁月的变迁。',
  },
  {
    key: 'gentle',
    label: '温柔旁白女声',
    prompt: '青年女声，约26岁，音色柔和温暖，语速平缓，语调轻柔自然，语气温柔亲切，标准普通话，适合情感类旁白与睡前读物。',
    audition: '夜色渐深，城市的灯光温柔地亮起，愿此刻的你能卸下一天的疲惫。',
  },
  {
    key: 'adLively',
    label: '广告活力女声',
    prompt: '青年女声，约24岁，音色清亮甜美，语速稍快，语调上扬有活力，语气热情洋溢，标准普通话，适合广告与活动宣传。',
    audition: '全新上市，惊喜不停！这个夏天，让我们一起焕新出发！',
  },
  {
    key: 'brand',
    label: '品牌磁性男声',
    prompt: '青年男声，约32岁，音色低沉有磁性，语速适中，语调平稳有质感，语气沉稳自信，标准普通话，适合品牌广告配音。',
    audition: '每一个细节，都源于对品质的坚持。我们，与你同行。',
  },
  {
    key: 'service',
    label: '电话客服',
    prompt: '青年女声，约25岁，音色清晰温和，吐字清楚，语速适中，语调平稳友好，语气耐心亲切，标准普通话，适合电话客服与语音导航。',
    audition: '您好，欢迎致电客户服务中心，我们将竭诚为您服务。',
  },
  {
    key: 'audiobook',
    label: '有声书男声',
    prompt: '中年男声，约38岁，音色醇厚温暖，语速适中，语调抑扬顿挫，语气沉稳从容，标准普通话，适合有声书与小说朗读。',
    audition: '他缓缓推开那扇尘封已久的木门，记忆如潮水般涌了上来。',
  },
  {
    key: 'kidsStory',
    label: '少儿故事',
    prompt: '青年女声，约28岁，音色清脆甜美，语速稍慢，语调活泼富有感染力，语气俏皮可爱，标准普通话，适合少儿故事讲述。',
    audition: '小朋友们，今天我们要讲的，是一只小兔子和大灰狼之间的有趣冒险哦！',
  },
  {
    key: 'childRole',
    label: '动画童声',
    prompt: '女童声，约8岁，音色稚嫩清脆，语速稍快，语调活泼跳跃，语气天真好奇，标准普通话，适合动画与儿童角色配音。',
    audition: '哇，原来彩虹有七种颜色呀！我们明天再来看看，它会不会变魔术吧！',
  },
  {
    key: 'news',
    label: '新闻播报',
    prompt: '青年男声，约33岁，音色明亮清晰，吐字干脆利落，语速适中，语调平稳庄重，语气客观专业，标准普通话，适合新闻资讯播报。',
    audition: '下面播报一组热点资讯：今日，多项民生新政正式落地实施。',
  },
  {
    key: 'shortVideo',
    label: '短视频口播',
    prompt: '青年女声，约23岁，音色明快自然，语速稍快，语调节奏感强，语气亲切有活力，标准普通话，适合短视频口播解说。',
    audition: '三个技巧让你的播放量翻倍，最后一个最重要，记得看完！',
  },
  {
    key: 'corporate',
    label: '宣传片男声',
    prompt: '中年男声，约42岁，音色浑厚大气，语速适中，语调抑扬顿挫，语气坚定有力，标准普通话，适合企业宣传片配音。',
    audition: '三十载风雨兼程，我们始终以匠心致敬每一位客户的信任。',
  },
]

/** 按维度固定顺序拼句：已选维度依序连接，scene 段前补"标准普通话"，无任何选择时返回空串。 */
export function composePrompt(selection: Record<string, string>): string {
  const parts: string[] = []
  for (const dim of PROMPT_DIMENSIONS) {
    const value = selection[dim.key]
    if (!value) continue
    parts.push(dim.key === 'scene' ? `标准普通话，${value}` : value)
  }
  return parts.length ? `${parts.join('，')}。` : ''
}

/** 提示词输入框的占位文案，示范六段式写法。 */
export const PROMPT_PLACEHOLDER = '例：青年女声，约26岁，音色柔和温暖，语速适中，语调自然起伏，语气温柔亲切，标准普通话，适合情感类旁白'
