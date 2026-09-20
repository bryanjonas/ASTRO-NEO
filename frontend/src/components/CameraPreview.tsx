import { useEffect, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import { Card } from './ui'

export default function CameraPreview() {
  const [tick, setTick] = useState(0)
  const latest = usePolling(api.getLatestCapture, 8000)

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 8000)
    return () => clearInterval(id)
  }, [])

  const info = latest.data

  return (
    <Card title="Latest Capture">
      {info?.available ? (
        <div className="flex flex-col gap-3">
          <div className="overflow-hidden rounded-lg border border-slate-800 bg-black">
            <img
              key={tick}
              src={`/api/hardware/camera/preview?t=${tick}`}
              alt="Latest capture preview"
              className="w-full object-contain"
              onError={(e) => {
                ;(e.currentTarget as HTMLImageElement).style.opacity = '0.3'
              }}
            />
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-slate-400">
            <span>
              Target: <span className="text-slate-200">{info.target}</span>
            </span>
            <span>
              Exposure: <span className="text-slate-200">{info.exposure_seconds}s</span>
            </span>
            <span>
              Filter: <span className="text-slate-200">{info.filter_name ?? '—'}</span>
            </span>
            <span>
              Solved: <span className="text-slate-200">{info.has_wcs ? 'Yes' : 'No'}</span>
            </span>
            <span>
              Captured: <span className="text-slate-200">{info.started_at ? new Date(info.started_at).toLocaleTimeString() : '—'}</span>
            </span>
          </div>
          {info.error_message && <p className="text-sm text-rose-300">{info.error_message}</p>}
        </div>
      ) : (
        <p className="text-sm text-slate-500">No captures available yet.</p>
      )}
    </Card>
  )
}
