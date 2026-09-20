import { Card } from '../components/ui'

export default function Psv() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-100">PSV Builder</h1>
      <Card>
        <p className="text-sm text-slate-500">
          PSV bundle builder is being ported from the legacy dashboard -- for now, use{' '}
          <a href="/dashboard/psv" className="text-sky-400 underline">
            the existing PSV page
          </a>
          .
        </p>
      </Card>
    </div>
  )
}
