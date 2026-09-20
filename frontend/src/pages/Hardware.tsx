import { api } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import type { HardwareSubsystemStatus } from '../api/types'
import { Card, StatPill } from '../components/ui'
import CameraPreview from '../components/CameraPreview'

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function SubsystemCard({ title, status }: { title: string; status: HardwareSubsystemStatus | undefined }) {
  const fields = status
    ? Object.entries(status).filter(([key]) => !['reachable', 'backend', 'error'].includes(key))
    : []

  return (
    <Card title={title}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StatPill
          label="Reachable"
          value={status?.reachable ? 'Yes' : 'No'}
          tone={status?.reachable ? 'good' : 'bad'}
        />
        <StatPill label="Backend" value={status?.backend ?? '—'} />
      </div>
      {status?.error && <p className="mb-2 text-sm text-rose-300">{status.error}</p>}
      {fields.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {fields.map(([key, value]) => (
            <StatPill key={key} label={key} value={formatValue(value)} />
          ))}
        </div>
      )}
    </Card>
  )
}

export default function Hardware() {
  const hardware = usePolling(api.getHardwareStatus, 4000)

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-100">Hardware</h1>
      {hardware.error && <p className="text-sm text-rose-300">{hardware.error}</p>}
      <CameraPreview />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <SubsystemCard title="Mount" status={hardware.data?.mount} />
        <SubsystemCard title="Camera" status={hardware.data?.camera} />
        <SubsystemCard title="Guiding" status={hardware.data?.guiding} />
        <SubsystemCard title="Focuser" status={hardware.data?.focuser} />
      </div>
    </div>
  )
}
