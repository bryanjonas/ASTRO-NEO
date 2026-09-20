import { useState } from 'react'
import { api } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import { Button, Card, ErrorBanner, StatPill } from '../components/ui'

export default function Overview() {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'start' | 'stop' | 'refresh' | null>(null)

  const session = usePolling(api.getSessionStatus, 3000)
  const ready = usePolling(api.getSessionReady, 5000)
  const targets = usePolling(api.getWhatsUpTargets, 15000)
  const weather = usePolling(api.getWeather, 30000)

  async function handleStart() {
    setBusy('start')
    setError(null)
    try {
      const res = await api.startSession()
      if (!res.success) setError(res.error ?? 'Failed to start session')
      await session.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function handleStop() {
    setBusy('stop')
    setError(null)
    try {
      await api.stopSession()
      await session.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function handleRefreshTargets() {
    setBusy('refresh')
    setError(null)
    try {
      await api.refreshWhatsUpTargets()
      await targets.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const s = session.data
  const w = weather.data

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-100">Overview</h1>
        {!s?.active ? (
          <Button onClick={handleStart} disabled={busy !== null || !ready.data?.ready}>
            {busy === 'start' ? 'Starting…' : 'Start Session'}
          </Button>
        ) : (
          <Button variant="danger" onClick={handleStop} disabled={busy !== null}>
            {busy === 'stop' ? 'Stopping…' : 'End Session'}
          </Button>
        )}
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {!ready.data?.ready && ready.data?.error && !s?.active && (
        <p className="-mt-3 text-sm text-slate-500">{ready.data.error}</p>
      )}

      <Card title="Session">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatPill label="Active" value={s?.active ? 'Yes' : 'No'} tone={s?.active ? 'good' : 'default'} />
          <StatPill label="Target" value={s?.target_name ?? '—'} />
          <StatPill label="Status" value={s?.status ?? '—'} />
          <StatPill
            label="Chain"
            value={s?.chain_active ? `Active (${s.chain_attempted_count} attempted)` : 'Idle'}
            tone={s?.chain_active ? 'good' : 'default'}
          />
          <StatPill label="Captures" value={s?.total_captures ?? 0} />
          <StatPill label="Successful captures" value={s?.successful_captures ?? 0} tone="good" />
          <StatPill label="Associations" value={s?.successful_associations ?? 0} tone="good" />
          <StatPill label="Started" value={s?.started_at ? new Date(s.started_at).toLocaleTimeString() : '—'} />
        </div>
      </Card>

      <Card title="Weather">
        {!w?.configured ? (
          <p className="text-sm text-slate-500">No weather sensor configured — safety gate fails open.</p>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatPill label="Safe to observe" value={w.safe ? 'Yes' : 'No'} tone={w.safe ? 'good' : 'bad'} />
            <StatPill label="Temp" value={w.temperature_c != null ? `${w.temperature_c.toFixed(1)}°C` : '—'} />
            <StatPill label="Wind" value={w.wind_speed_mps != null ? `${w.wind_speed_mps.toFixed(1)} m/s` : '—'} />
            <StatPill label="Cloud cover" value={w.cloud_cover_pct != null ? `${w.cloud_cover_pct.toFixed(0)}%` : '—'} />
            <StatPill label="Humidity" value={w.relative_humidity_pct != null ? `${w.relative_humidity_pct.toFixed(0)}%` : '—'} />
            <StatPill
              label="Precip chance"
              value={w.precipitation_probability_pct != null ? `${w.precipitation_probability_pct.toFixed(0)}%` : '—'}
            />
          </div>
        )}
        {w?.reasons && w.reasons.length > 0 && (
          <p className="mt-3 text-sm text-rose-300">{w.reasons.join('; ')}</p>
        )}
      </Card>

      <Card title="WhatsUp Targets">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-sm text-slate-500">{targets.data?.length ?? 0} candidate(s) currently tracked</p>
          <Button variant="secondary" onClick={handleRefreshTargets} disabled={busy !== null}>
            {busy === 'refresh' ? 'Refreshing…' : 'Refresh Targets'}
          </Button>
        </div>
        {targets.data && targets.data.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500">
                  <th className="py-2 pr-4 font-medium">Designation</th>
                  <th className="py-2 pr-4 font-medium">Vmag</th>
                  <th className="py-2 pr-4 font-medium">Updated</th>
                </tr>
              </thead>
              <tbody>
                {targets.data.map((t) => (
                  <tr key={t.id} className="border-b border-slate-900">
                    <td className="py-2 pr-4 font-medium text-slate-200">{t.trksub}</td>
                    <td className="py-2 pr-4 text-slate-400">{t.vmag != null ? t.vmag.toFixed(1) : '—'}</td>
                    <td className="py-2 pr-4 text-slate-500">{new Date(t.updated_at).toLocaleTimeString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-slate-500">No targets available right now.</p>
        )}
      </Card>
    </div>
  )
}
