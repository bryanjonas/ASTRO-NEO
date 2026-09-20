import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/app/', label: 'Overview', icon: '◉' },
  { to: '/app/hardware', label: 'Hardware', icon: '⚙' },
  { to: '/app/sky', label: 'Sky View', icon: '✡' },
  { to: '/app/psv', label: 'PSV Builder', icon: '▤' },
]

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/app/'}
          onClick={onNavigate}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              isActive
                ? 'bg-sky-500/15 text-sky-300'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-100'
            }`
          }
        >
          <span className="text-lg leading-none">{item.icon}</span>
          {item.label}
        </NavLink>
      ))}
    </>
  )
}

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="flex min-h-screen bg-slate-950">
      {/* Desktop sidebar */}
      <aside className="hidden w-56 shrink-0 border-r border-slate-800 bg-slate-900/50 p-4 md:flex md:flex-col">
        <div className="mb-6 px-2">
          <h1 className="text-lg font-semibold tracking-tight text-slate-100">ASTRO-NEO</h1>
          <p className="text-xs text-slate-500">Mission Control</p>
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          <NavItems />
        </nav>
      </aside>

      {/* Mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-30 flex items-center justify-between border-b border-slate-800 bg-slate-950/95 px-4 py-3 backdrop-blur md:hidden">
        <h1 className="text-base font-semibold text-slate-100">ASTRO-NEO</h1>
        <button
          type="button"
          aria-label="Toggle menu"
          onClick={() => setMobileOpen((v) => !v)}
          className="rounded-md p-2 text-slate-300 hover:bg-slate-800"
        >
          <span className="block text-xl leading-none">{mobileOpen ? '✕' : '☰'}</span>
        </button>
      </div>
      {mobileOpen && (
        <div className="fixed inset-x-0 top-[52px] z-20 border-b border-slate-800 bg-slate-950 p-3 md:hidden">
          <nav className="flex flex-col gap-1">
            <NavItems onNavigate={() => setMobileOpen(false)} />
          </nav>
        </div>
      )}

      <main className="min-w-0 flex-1 pt-[52px] md:pt-0">
        <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
