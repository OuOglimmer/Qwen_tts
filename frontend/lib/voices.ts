// 跨页共用的声线查询：配音、成品、试听返修三处都需要"当前项目已固化声线"。
import { apiGet } from './api'
import type { VoiceAsset } from './types'

/** 拉取项目声线并只保留已固化的（后端已只返回 active，这里保留一道防御性过滤）。 */
export async function fetchActiveVoices(projectId: string): Promise<VoiceAsset[]> {
  const rows = await apiGet<VoiceAsset[]>(`/api/projects/${projectId}/voices`)
  return rows.filter((voice) => voice.status === 'active')
}
