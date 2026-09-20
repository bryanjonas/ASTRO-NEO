import type { ReactNode } from 'react'

export function Card({ title, children, className = '' }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 ${className}`}>
      {title && <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>}
      {children}
    </div>
  )
}

export function StatPill({ label, value, tone = 'default' }: { label: string; value: ReactNode; tone?: 'default' | 'good' | 'bad' | 'warn' }) {
  const toneClasses: Record<string, string> = {
    default: 'bg-slate-800 text-slate-200',
    good: 'bg-emerald-500/15 text-emerald-300',
    bad: 'bg-rose-500/15 text-rose-300',
    warn: 'bg-amber-500/15 text-amber-300',
  }
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-slate-500">{label}</span>
      <span className={`inline-flex w-fit items-center rounded-md px-2 py-0.5 text-sm font-medium ${toneClasses[tone]}`}>
        {value}
      </span>
    </div>
  )
}

export function Button({
  children,
  onClick,
  disabled,
  variant = 'primary',
  className = '',
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  variant?: 'primary' | 'danger' | 'secondary'
  className?: string
  type?: 'button' | 'submit'
}) {
  const variants: Record<string, string> = {
    primary: 'bg-sky-500 hover:bg-sky-400 text-slate-950 disabled:bg-slate-700 disabled:text-slate-400',
    danger: 'bg-rose-600 hover:bg-rose-500 text-white disabled:bg-slate-700 disabled:text-slate-400',
    secondary: 'bg-slate-800 hover:bg-slate-700 text-slate-100 disabled:text-slate-500',
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  )
}

export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-rose-800 bg-rose-950/60 px-4 py-3 text-sm text-rose-200">
      <span>{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="text-rose-400 hover:text-rose-200" aria-label="Dismiss">
          &times;
        </button>
      )}
    </div>
  )
}
