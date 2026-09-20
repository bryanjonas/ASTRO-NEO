import { Card } from '../components/ui'

export default function Hardware() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-100">Hardware</h1>
      <Card>
        <p className="text-sm text-slate-500">
          Mount, camera, guiding, and focuser status/control -- coming in the next build phase.
        </p>
      </Card>
    </div>
  )
}
