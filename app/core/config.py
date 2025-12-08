"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ASTRO-NEO"
    app_version: str = "0.1.0"
    api_prefix: str = "/api"
    database_url: str = "postgresql+psycopg://astro:astro@db:5432/astro"
    site_name: str = "default"
    site_latitude: float = 0.0
    site_longitude: float = 0.0
    site_altitude_m: float = 0.0
    site_bortle: int | None = None
    site_config_path: str = "config/site.yml"
    neocp_html_url: str = "https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html"
    neocp_local_html: str = "/data/neocp_snapshots/toconfirm.html"
    neocp_text_url: str = "https://minorplanetcenter.net/iau/NEO/neocp.txt"
    neocp_local_text: str = "/data/neocp_snapshots/neocp.txt"
    neocp_fetch_timeout: float = 30.0
    neocp_use_local_sample: bool = False
    neocp_api_url: str = "https://data.minorplanetcenter.net/api/get-obs-neocp"
    neocp_observation_formats: tuple[str, ...] = ("ADES_DF",)
    neocp_ades_version: str = "2022"
    neocp_poll_interval_seconds: int = 15 * 60
    neocp_api_pause_seconds: float = 1.0
    neocp_api_max_retries: int = 3
    neocp_metrics_enabled: bool = True
    neocp_metrics_host: str = "0.0.0.0"
    neocp_metrics_port: int = 9500
    mpc_ephemeris_url: str = "https://data.minorplanetcenter.net/api/get-ephemeris"
    mpc_ephemeris_timeout: float = 30.0
    observability_horizon_hours: int = 12
    observability_sample_minutes: int = 5
    observability_min_altitude_deg: float = 25.0
    observability_min_window_minutes: int = 15
    observability_target_window_minutes: int = 60
    observability_max_sun_altitude_deg: float = -12.0
    observability_min_moon_separation_deg: float = 30.0
    observability_max_vmag: float = 20.0
    observability_recent_hours: int = 24
    observability_refresh_minutes: int = 15
    weather_snapshot_ttl_minutes: int = 15
    weather_api_timeout: float = 10.0
    weather_max_wind_speed_mps: float = 13.5  # ~30 mph
    weather_max_relative_humidity_pct: float = 95.0
    weather_max_precip_probability_pct: float = 40.0
    weather_precip_block_threshold_mm: float = 0.1
    weather_max_cloud_cover_pct: float = 95.0
    guiding_max_rms_arcsec: float = 2.5
    iq_max_fwhm_arcsec: float = 4.0
    station_code: str = "XXX"
    observer_initials: str = "XX"
    software_id: str = "ASTRO-NEO/0.1.0"
    default_band: str = "R"
    mag_uncert_floor: float = 0.05
    mpc_email: str = ""
    submission_channel: str = "email"  # email|api
    nina_bridge_url: str = "http://nina-bridge:8001/api"
    nina_bridge_timeout: float = 15.0
    data_root: str = "/data"
    fits_retention_days: int = 14
    astrometry_worker_url: str | None = "http://astrometry-worker:8100"
    astrometry_worker_timeout: float = 300.0
    astrometry_config_path: str = "/app/astrometry.cfg"
    astrometry_scale_low_arcsec: float | None = None
    astrometry_scale_high_arcsec: float | None = None
    astrometry_search_radius_deg: float | None = None
    astrometry_downsample: int | None = None
    calibration_dark_counts: int = 10
    calibration_flat_counts: int = 10
    calibration_bias_counts: int = 20
    synthetic_target_count: int = 3
    synthetic_target_min_altitude_deg: float = 30.0
    synthetic_target_max_altitude_deg: float = 45.0
    synthetic_target_interval_minutes: int = 10
    synthetic_target_prefix: str = "FAKE"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

__all__ = ["settings", "Settings"]
