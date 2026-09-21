import { useState } from 'react'
import { api } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import { Button, Card, ErrorBanner, StatPill } from './ui'

export default function PolarAlignPanel() {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'start' | 'stop' | 'refresh' | null>(null)
  const state = usePolling(api.getPolarAlignState, 3000)

  async function run(action: 'start' | 'stop' | 'refresh', fn: () => Promise<unknown>) {
    setBusy(action)
    setError(null)
    try {
      await fn()
      await state.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const data = state.data
  const running = data?.running ?? false
  const phase = data?.phase ?? 'idle'
  const ready = phase === 'ready'
  const calibration = data?.calibration

  return (
    <Card title="All-Sky Polar Alignment">
      <p className="mb-3 text-sm text-slate-500">
        Each Refresh runs a fresh calibration (three shots ~20&deg; apart in RA, raw axis jogs, no coordinate
        GOTO) starting wherever you've already pointed the mount &mdash; keep it well off the celestial pole.
        No exposure is ever taken without pressing Refresh, so nothing captures mid-adjustment. Adjust the
        azimuth/altitude bolts, then press Refresh for a real, freshly recalculated correction.
      </p>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {data?.error && <p className="mb-3 text-sm text-amber-300">{data.error}</p>}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StatPill label="Running" value={running ? 'Yes' : 'No'} tone={running ? 'good' : 'default'} />
        <StatPill
          label="Phase"
          value={phase === 'calibrating' ? 'Calibrating…' : phase === 'ready' ? 'Ready' : 'Idle'}
          tone={ready ? 'good' : phase === 'calibrating' ? 'warn' : 'default'}
        />
        <StatPill label="Cycles" value={data?.cycle_count ?? 0} />
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

      <div className="flex flex-wrap gap-2">
        {running ? (
          <>
            <Button onClick={() => run('refresh', api.refreshPolarAlignment)} disabled={!ready || busy !== null}>
              {busy === 'refresh' ? 'Refreshing…' : 'Refresh'}
            </Button>
            <Button variant="danger" onClick={() => run('stop', api.stopContinuousPolarAlignment)} disabled={busy !== null}>
              {busy === 'stop' ? 'Stopping…' : 'Stop'}
            </Button>
          </>
        ) : (
          <Button onClick={() => run('start', api.startContinuousPolarAlignment)} disabled={busy !== null}>
            {busy === 'start' ? 'Starting…' : 'Start'}
          </Button>
        )}
      </div>
    </Card>
  )
}
