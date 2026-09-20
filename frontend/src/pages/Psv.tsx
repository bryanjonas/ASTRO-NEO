import { useState } from 'react'
import { api } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import { Button, Card, ErrorBanner, StatPill } from '../components/ui'
import type { PsvBundleResult } from '../api/types'

function filenameFromPath(path: string): string {
  const parts = path.split('/')
  return parts[parts.length - 1]
}

export default function Psv() {
  const targets = usePolling(api.getPsvTargets, 15000)
  const files = usePolling(api.getPsvFiles, 15000)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PsvBundleResult | null>(null)

  function toggle(target: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(target)) next.delete(target)
      else next.add(target)
      return next
    })
  }

  async function handleCreateBundle() {
    if (selected.size === 0) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const bundle = await api.createPsvBundle(Array.from(selected), label || undefined)
      setResult(bundle)
      await files.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const readyTargets = targets.data?.targets.filter((t) => t.ready) ?? []
  const otherTargets = targets.data?.targets.filter((t) => !t.ready) ?? []

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-100">PSV Builder</h1>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {result && (
        <Card title="Bundle Created" className={result.valid ? 'border-emerald-800' : 'border-amber-800'}>
          <div className="flex flex-wrap items-center gap-3">
            <StatPill label="Valid" value={result.valid ? 'Yes' : 'No'} tone={result.valid ? 'good' : 'warn'} />
            <StatPill label="Targets" value={result.targets.join(', ')} />
            <a
              href={`/api/psv/download/${filenameFromPath(result.psv_path)}`}
              className="rounded-lg bg-sky-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-sky-400"
            >
              Download {filenameFromPath(result.psv_path)}
            </a>
          </div>
          {result.errors.length > 0 && (
            <ul className="mt-3 list-inside list-disc text-sm text-rose-300">
              {result.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <Card title="Ready to Submit">
        {readyTargets.length === 0 ? (
          <p className="text-sm text-slate-500">No targets currently meet the quality-gated submission threshold.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500">
                  <th className="py-2 pr-4"></th>
                  <th className="py-2 pr-4 font-medium">Target</th>
                  <th className="py-2 pr-4 font-medium">Qualifying nights</th>
                  <th className="py-2 pr-4 font-medium">Good obs</th>
                  <th className="py-2 pr-4 font-medium">Last obs</th>
                </tr>
              </thead>
              <tbody>
                {readyTargets.map((t) => (
                  <tr key={t.target} className="border-b border-slate-900">
                    <td className="py-2 pr-4">
                      <input
                        type="checkbox"
                        checked={selected.has(t.target)}
                        onChange={() => toggle(t.target)}
                        className="h-4 w-4 accent-sky-500"
                      />
                    </td>
                    <td className="py-2 pr-4 font-medium text-slate-200">{t.target}</td>
                    <td className="py-2 pr-4 text-slate-400">{t.qualifying_nights}</td>
                    <td className="py-2 pr-4 text-slate-400">{t.good_obs}</td>
                    <td className="py-2 pr-4 text-slate-500">{t.last_obs ? new Date(t.last_obs).toLocaleDateString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <input
            type="text"
            placeholder="Bundle label (optional)"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600"
          />
          <Button onClick={handleCreateBundle} disabled={busy || selected.size === 0}>
            {busy ? 'Building…' : `Create Bundle (${selected.size} selected)`}
          </Button>
        </div>
      </Card>

      {otherTargets.length > 0 && (
        <Card title="Not Yet Ready">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500">
                  <th className="py-2 pr-4 font-medium">Target</th>
                  <th className="py-2 pr-4 font-medium">Nights observed</th>
                  <th className="py-2 pr-4 font-medium">Qualifying nights</th>
                  <th className="py-2 pr-4 font-medium">Good obs</th>
                </tr>
              </thead>
              <tbody>
                {otherTargets.map((t) => (
                  <tr key={t.target} className="border-b border-slate-900">
                    <td className="py-2 pr-4 font-medium text-slate-300">{t.target}</td>
                    <td className="py-2 pr-4 text-slate-500">{t.nights_observed}</td>
                    <td className="py-2 pr-4 text-slate-500">{t.qualifying_nights}</td>
                    <td className="py-2 pr-4 text-slate-500">{t.good_obs}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title="Previous Bundles">
        {files.data && files.data.files.length > 0 ? (
          <ul className="flex flex-col gap-2 text-sm">
            {files.data.files.map((f) => (
              <li key={f.name} className="flex items-center justify-between gap-3 border-b border-slate-900 py-2">
                <span className="text-slate-300">{f.name}</span>
                <div className="flex items-center gap-3 text-slate-500">
                  <span>{new Date(f.modified_at).toLocaleString()}</span>
                  <a href={`/api/psv/download/${f.name}`} className="text-sky-400 underline">
                    Download
                  </a>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">No PSV bundles created yet.</p>
        )}
      </Card>
    </div>
  )
}
