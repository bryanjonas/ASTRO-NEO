import type {
  GuidingHistory,
  HardwareStatus,
  LatestCaptureInfo,
  PolarAlignResult,
  PsvBundleResult,
  PsvFile,
  PsvTarget,
  SessionReady,
  SessionStatus,
  SkyView,
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
  getSkyView: () => request<SkyView>('/hardware/sky'),
  getGuidingHistory: () => request<GuidingHistory>('/hardware/guiding/history'),
  getPsvTargets: () => request<{ targets: PsvTarget[] }>('/psv/targets'),
  getPsvFiles: () => request<{ files: PsvFile[] }>('/psv/files'),
  createPsvBundle: (targets: string[], bundleLabel?: string) =>
    request<PsvBundleResult>('/psv/bundle', {
      method: 'POST',
      body: JSON.stringify({ targets, bundle_label: bundleLabel || null }),
    }),
  runPolarAlignment: () => request<PolarAlignResult>('/hardware/polar-align/run', { method: 'POST' }),
}
