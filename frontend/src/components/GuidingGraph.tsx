import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import { Card } from './ui'

export default function GuidingGraph() {
  const history = usePolling(api.getGuidingHistory, 3000)
  const steps = history.data?.steps ?? []

  const chartData = steps.map((s, i) => ({
    index: i,
    ra: s.ra_distance_raw,
    dec: s.dec_distance_raw,
  }))

  return (
    <Card title="Guiding">
      {chartData.length > 0 ? (
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="index" stroke="#64748b" fontSize={11} tickLine={false} />
              <YAxis stroke="#64748b" fontSize={11} tickLine={false} label={{ value: 'px', angle: -90, position: 'insideLeft', fill: '#64748b', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="ra" name="RA error" stroke="#38bdf8" dot={false} strokeWidth={1.5} isAnimationActive={false} />
              <Line type="monotone" dataKey="dec" name="Dec error" stroke="#f472b6" dot={false} strokeWidth={1.5} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="text-sm text-slate-500">
          No guide telemetry yet -- appears once PHD2 guiding is active (guiding_backend="phd2").
        </p>
      )}
    </Card>
  )
}
