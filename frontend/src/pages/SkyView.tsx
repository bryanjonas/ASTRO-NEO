import { api } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import { Card } from '../components/ui'
import SkyPlot from '../components/SkyPlot'
import PolarAlignPanel from '../components/PolarAlignPanel'

export default function SkyView() {
  const sky = usePolling(api.getSkyView, 5000)

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-100">Sky View</h1>
      {sky.error && <p className="text-sm text-rose-300">{sky.error}</p>}
      <Card>
        {sky.data ? (
          <>
            <SkyPlot data={sky.data} />
            <p className="mt-4 text-center text-xs text-slate-500">
              Center = zenith, edge = horizon. Rings at 30°/60° altitude. Shaded red = obstructed by horizon mask.
              {!sky.data.mount && ' Mount position unavailable.'}
            </p>
          </>
        ) : (
          <p className="text-sm text-slate-500">Loading…</p>
        )}
      </Card>
      <PolarAlignPanel />
    </div>
  )
}
