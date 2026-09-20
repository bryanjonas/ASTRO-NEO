import type {
  HardwareStatus,
  LatestCaptureInfo,
  SessionReady,
  SessionStatus,
  StartSessionResponse,
  WeatherStatus,
  WhatsUpTarget,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? body.error ?? detail
    } catch {
      // ignore, use statusText
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  getSessionStatus: () => request<SessionStatus>('/session/status'),
  getSessionReady: () => request<SessionReady>('/session/ready'),
  startSession: (manualTarget?: string) =>
    request<StartSessionResponse>('/session/start', {
      method: 'POST',
      body: JSON.stringify({ manual_target_override: manualTarget ?? null }),
    }),
  stopSession: () => request<{ success: boolean; message?: string }>('/session/stop', { method: 'POST' }),
  getWhatsUpTargets: () => request<WhatsUpTarget[]>('/whatsup/targets'),
  refreshWhatsUpTargets: () => request<WhatsUpTarget[]>('/whatsup/refresh', { method: 'POST' }),
  getWeather: () => request<WeatherStatus>('/site/weather'),
  getHardwareStatus: () => request<HardwareStatus>('/hardware/status'),
  getLatestCapture: () => request<LatestCaptureInfo>('/hardware/camera/latest'),
}
