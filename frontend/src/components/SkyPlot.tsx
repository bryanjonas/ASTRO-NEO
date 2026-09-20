import type { AltAz, SkyView } from '../api/types'

const SIZE = 320
const R = SIZE / 2 - 24
const CX = SIZE / 2
const CY = SIZE / 2

/** Alt/Az -> SVG point. Center = zenith (alt 90), edge = horizon (alt 0).
 * Compass convention: North at top, East at right, clockwise -- this is a
 * pointing/tracking radar display for an operator, not a "looking up"
 * naked-eye star chart, so it matches the mount's own azimuth readout
 * convention rather than the mirror-flipped naked-eye-view convention. */
function toPoint(altDeg: number, azDeg: number): { x: number; y: number } {
  const clampedAlt = Math.max(altDeg, 0)
  const r = (R * (90 - clampedAlt)) / 90
  const azRad = (azDeg * Math.PI) / 180
  return { x: CX + r * Math.sin(azRad), y: CY - r * Math.cos(azRad) }
}

function Marker({ point, label, color }: { point: AltAz; label: string; color: string }) {
  const { x, y } = toPoint(point.alt_deg, point.az_deg)
  const belowHorizon = point.alt_deg < 0
  return (
    <g opacity={belowHorizon ? 0.35 : 1}>
      <circle cx={x} cy={y} r={5} fill={color} stroke="#0f172a" strokeWidth={1.5} />
      <text x={x + 8} y={y + 4} fontSize={11} fill={color} className="select-none">
        {label}
      </text>
    </g>
  )
}

export default function SkyPlot({ data }: { data: SkyView }) {
  const ringAlts = [0, 30, 60]

  const horizonPath =
    data.horizon.length > 0
      ? data.horizon
          .map((h, i) => {
            const { x, y } = toPoint(h.alt_deg, h.az_deg)
            return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
          })
          .join(' ') + ' Z'
      : null

  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="mx-auto w-full max-w-sm">
      {/* Altitude rings */}
      {ringAlts.map((alt) => (
        <circle
          key={alt}
          cx={CX}
          cy={CY}
          r={(R * (90 - alt)) / 90}
          fill="none"
          stroke="#334155"
          strokeWidth={1}
          strokeDasharray={alt === 0 ? undefined : '3 3'}
        />
      ))}

      {/* Compass labels */}
      <text x={CX} y={CY - R - 6} textAnchor="middle" fontSize={12} fill="#94a3b8">N</text>
      <text x={CX + R + 10} y={CY + 4} textAnchor="middle" fontSize={12} fill="#94a3b8">E</text>
      <text x={CX} y={CY + R + 16} textAnchor="middle" fontSize={12} fill="#94a3b8">S</text>
      <text x={CX - R - 10} y={CY + 4} textAnchor="middle" fontSize={12} fill="#94a3b8">W</text>

      {/* Horizon obstruction mask (shaded region blocked from the horizon inward) */}
      {horizonPath && <path d={horizonPath} fill="#f87171" fillOpacity={0.12} stroke="#f87171" strokeOpacity={0.4} strokeWidth={1} />}

      {data.sun && <Marker point={data.sun} label="Sun" color="#fbbf24" />}
      {data.moon && <Marker point={data.moon} label="Moon" color="#cbd5e1" />}
      {data.target && <Marker point={data.target} label={data.target.name} color="#34d399" />}
      {data.mount && <Marker point={data.mount} label="Mount" color="#38bdf8" />}
    </svg>
  )
}
