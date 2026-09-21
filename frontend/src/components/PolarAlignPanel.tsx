import { useState } from 'react'
import { api } from '../api/client'
import { Button, Card, ErrorBanner, StatPill } from './ui'
import type { PolarAlignResult } from '../api/types'

export default function PolarAlignPanel() {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PolarAlignResult | null>(null)

  async function handleRun() {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const res = await api.runPolarAlignment()
      setResult(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Card title="All-Sky Polar Alignment">
      <p className="mb-3 text-sm text-slate-500">
        Measures the mount's polar axis error: captures and solves at the current pointing, re-slews a known
        amount in RA only, captures and solves again. Takes roughly a minute. This is a real mount/camera action --
        only runs when you click below.
      </p>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {result && (
        <div className="mb-3 flex flex-col gap-2">
          <p className="text-sm text-slate-200">{result.description}</p>
          <div className="flex flex-wrap gap-3">
            <StatPill label="Az error" value={`${result.az_error_arcmin.toFixed(1)}'`} />
            <StatPill label="Alt error" value={`${result.alt_error_arcmin.toFixed(1)}'`} />
          </div>
        </div>
      )}
      <Button onClick={handleRun} disabled={running}>
        {running ? 'Measuring… (~1 min)' : 'Run Measurement'}
      </Button>
    </Card>
  )
}
