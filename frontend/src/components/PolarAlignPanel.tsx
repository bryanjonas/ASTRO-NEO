import { useState } from 'react'
import { api } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import { Button, Card, ErrorBanner, StatPill } from './ui'

export default function PolarAlignPanel() {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const state = usePolling(api.getPolarAlignState, 3000)

  async function handleStart() {
    setBusy(true)
    setError(null)
    try {
      await api.startContinuousPolarAlignment()
      await state.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleStop() {
    setBusy(true)
    try {
      await api.stopContinuousPolarAlignment()
      await state.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const data = state.data
  const running = data?.running ?? false
  const phase = data?.phase ?? 'idle'
  const calibration = data?.calibration

  return (
    <Card title="All-Sky Polar Alignment">
      <p className="mb-3 text-sm text-slate-500">
        Calibrates once (three shots 20&deg; apart in RA, starting wherever you've already pointed the mount
        &mdash; keep it well off the celestial pole), reports exactly which way and how far to adjust, then
        stops moving entirely and just keeps re-imaging that same fixed pointing while you turn the
        azimuth/altitude bolts by hand.
      </p>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {data?.error && <p className="mb-3 text-sm text-amber-300">{data.error}</p>}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StatPill label="Running" value={running ? 'Yes' : 'No'} tone={running ? 'good' : 'default'} />
        <StatPill
          label="Phase"
          value={phase === 'calibrating' ? 'Calibrating…' : phase === 'monitoring' ? 'Monitoring (mount stationary)' : 'Idle'}
          tone={phase === 'monitoring' ? 'good' : phase === 'calibrating' ? 'warn' : 'default'}
        />
      </div>

      {data?.message && (
        <div className="mb-4 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-sm text-slate-300">
          {data.message}
        </div>
      )}

      {calibration && (
        <div className="mb-4 flex flex-col gap-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Calibration result &mdash; adjust the mount now
          </h3>
          <div className="flex flex-wrap gap-3">
            <StatPill
              label="Azimuth"
              value={`turn ${calibration.az_move_direction}, ${calibration.az_move_amount}`}
              tone="warn"
            />
            <StatPill
              label="Altitude"
              value={`turn ${calibration.alt_move_direction}, ${calibration.alt_move_amount}`}
              tone="warn"
            />
          </div>
          <p className="text-xs text-slate-500">{calibration.description}</p>
        </div>
      )}

      {phase === 'monitoring' && (
        <div className="mb-4 flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Live drift since calibration (mount not moving)
          </h3>
          <div className="flex flex-wrap gap-3">
            <StatPill label="Readings" value={data?.monitor_count ?? 0} />
            <StatPill
              label="Drift from first reading"
              value={data?.monitor_drift_arcsec != null ? `${data.monitor_drift_arcsec.toFixed(1)}"` : '—'}
              tone="warn"
            />
          </div>
          <p className="text-xs text-slate-500">
            This is the raw change in the solved star field position, not a re-derived az/alt breakdown &mdash;
            use it as a relative "am I moving the right way, and by how much" signal while adjusting, then
            re-run calibration to get a fresh precise az/alt reading once you're close.
          </p>
        </div>
      )}

      {running ? (
        <Button variant="danger" onClick={handleStop} disabled={busy}>
          {busy ? 'Stopping…' : 'Stop'}
        </Button>
      ) : (
        <Button onClick={handleStart} disabled={busy}>
          {busy ? 'Starting…' : 'Start'}
        </Button>
      )}
    </Card>
  )
}
