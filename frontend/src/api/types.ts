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

export interface GuideStep {
  time: number | null
  ra_distance_raw: number | null
  dec_distance_raw: number | null
  ra_duration: number | null
  dec_duration: number | null
  snr: number | null
  star_mass: number | null
}

export interface GuidingHistory {
  steps: GuideStep[]
}

export interface PsvNightSummary {
  night: string
  count: number
  mag_count: number
  good_count: number
  good_mag_count: number
}

export interface PsvTarget {
  target: string
  object_number: string
  vmag: number | null
  total_obs: number
  good_obs: number
  nights_observed: number
  qualifying_nights: number
  ready: boolean
  per_night: PsvNightSummary[]
  first_obs: string | null
  last_obs: string | null
  science_exposures: number
  solved: number
  associated: number
}

export interface PsvBundleResult {
  psv_path: string
  validation_path: string
  metadata_path: string
  valid: boolean
  errors: string[]
  targets: string[]
}

export interface PsvFile {
  name: string
  path: string
  size_bytes: number
  modified_at: string
}

export interface PolarAlignResult {
  az_error_arcmin: number
  alt_error_arcmin: number
  mount_pole_alt_deg: number
  mount_pole_az_deg: number
  description: string
  commanded_rotation_deg: number
}
