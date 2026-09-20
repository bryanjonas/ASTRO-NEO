import { Card } from '../components/ui'

export default function SkyView() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-100">Sky View</h1>
      <Card>
        <p className="text-sm text-slate-500">
          Live pointing/horizon visualization -- coming in a later build phase.
        </p>
      </Card>
    </div>
  )
}
