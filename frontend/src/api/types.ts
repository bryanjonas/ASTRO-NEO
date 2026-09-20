export interface SessionStatus {
  active: boolean
  session_id: number | null
  target_name: string | null
  started_at: string | null
  status: string | null
  total_captures: number
  successful_captures: number
  successful_associations: number
  chain_active: boolean
  chain_auto_advance: boolean
  chain_attempted_count: number
  chain_remaining_count: number | null
}

export interface SessionReady {
  ready: boolean
  error?: string
  missing_candidates?: string[]
}

export interface WhatsUpTarget {
  id: string
  trksub: string
  vmag: number | null
  updated_at: string
}

export interface WeatherStatus {
  configured: boolean
  safe?: boolean
  reasons?: string[]
  fetched_at?: string
  temperature_c?: number
  wind_speed_mps?: number
  relative_humidity_pct?: number
  precipitation_probability_pct?: number
  precipitation_mm?: number
  cloud_cover_pct?: number
}

export interface StartSessionResponse {
  success: boolean
  target_name?: string
  auto_advance?: boolean
  error?: string
  session_id?: number | null
}

export interface HardwareSubsystemStatus {
  backend: string
  reachable: boolean
  error?: string
  [key: string]: unknown
}

export interface HardwareStatus {
  mount: HardwareSubsystemStatus
  camera: HardwareSubsystemStatus
  guiding: HardwareSubsystemStatus
  focuser: HardwareSubsystemStatus
}

export interface LatestCaptureInfo {
  available: boolean
  capture_id?: number
  target?: string
  started_at?: string
  exposure_seconds?: number
  filter_name?: string | null
  has_wcs?: boolean
  error_message?: string | null
}

export interface AltAz {
  alt_deg: number
  az_deg: number
}

export interface SkyView {
  mount: AltAz | null
  horizon: AltAz[]
  sun: AltAz | null
  moon: AltAz | null
  target: (AltAz & { name: string }) | null
}
