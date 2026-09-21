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

  const latest = state.data?.latest
  const running = state.data?.running ?? false

  return (
    <Card title="All-Sky Polar Alignment">
      <p className="mb-3 text-sm text-slate-500">
        Continuously re-measures the mount's polar axis error: each cycle slews to a reference point, captures
        and solves, re-slews a known amount in RA, captures and solves again (~1 min/cycle). Adjust the mount's
        azimuth/altitude bolts between readings and watch the error converge.
      </p>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {state.data?.error && <p className="mb-3 text-sm text-amber-300">Last cycle: {state.data.error}</p>}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StatPill label="Running" value={running ? 'Yes' : 'No'} tone={running ? 'good' : 'default'} />
        <StatPill label="Cycles completed" value={state.data?.cycle_count ?? 0} />
      </div>

      {latest && (
        <div className="mb-4 flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
          <p className="text-sm text-slate-200">{latest.description}</p>
          <div className="flex flex-wrap gap-3">
            <StatPill label="Az error" value={`${latest.az_error_arcmin.toFixed(1)}'`} />
            <StatPill label="Alt error" value={`${latest.alt_error_arcmin.toFixed(1)}'`} />
          </div>
        </div>
      )}

      {state.data && state.data.history.length > 1 && (
        <div className="mb-4 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-500">
                <th className="py-1 pr-4">#</th>
                <th className="py-1 pr-4">Az error</th>
                <th className="py-1 pr-4">Alt error</th>
              </tr>
            </thead>
            <tbody>
              {state.data.history
                .slice()
                .reverse()
                .map((h, i) => (
                  <tr key={i} className="border-b border-slate-900 text-slate-400">
                    <td className="py-1 pr-4">{state.data!.history.length - i}</td>
                    <td className="py-1 pr-4">{h.az_error_arcmin.toFixed(1)}'</td>
                    <td className="py-1 pr-4">{h.alt_error_arcmin.toFixed(1)}'</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      {running ? (
        <Button variant="danger" onClick={handleStop} disabled={busy}>
          {busy ? 'Stopping…' : 'Stop'}
        </Button>
      ) : (
        <Button onClick={handleStart} disabled={busy}>
          {busy ? 'Starting…' : 'Start Continuous'}
        </Button>
      )}
    </Card>
  )
}
