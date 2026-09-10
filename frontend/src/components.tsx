import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { BarChart3, BriefcaseBusiness, FileText, LayoutDashboard, Settings, UserRound, Workflow } from 'lucide-react'

export function Layout({ children }: { children: ReactNode }) {
  const links = [
    ['/', 'Dashboard', LayoutDashboard], ['/profile', 'Profile', UserRound], ['/resumes', 'Resumes', FileText],
    ['/jobs', 'Jobs', BriefcaseBusiness], ['/applications', 'Applications', Workflow], ['/settings', 'Settings', Settings],
  ] as const
  return <div className="app-shell">
    <aside className="sidebar">
      <NavLink to="/" className="brand"><span className="brand-mark">J</span><span>Jobflow<small>Application copilot</small></span></NavLink>
      <nav>{links.map(([to, label, Icon]) => <NavLink key={to} to={to} end={to === '/'}><Icon size={19}/><span>{label}</span></NavLink>)}</nav>
      <div className="privacy-note"><BarChart3 size={18}/><div><strong>Local-first</strong><span>Application records are stored locally. AI uses redacted excerpts only when enabled.</span></div></div>
    </aside>
    <main className="main-content">{children}</main>
  </div>
}

export function PageHeader({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</header>
}

export function StatusBadge({ status }: { status: string }) {
  const tone = status.includes('SUBMITTED') || status === 'OFFER' ? 'positive' : status.includes('FAILED') || status === 'REJECTED' ? 'negative' : status.includes('REVIEW') || status === 'INTERVIEW' ? 'accent' : status.includes('INPUT') || status.includes('WAITING') ? 'warning' : 'neutral'
  return <span className={`badge ${tone}`}>{status.replaceAll('_', ' ')}</span>
}

export function Score({ value, size = 'small' }: { value?: number; size?: 'small' | 'large' }) {
  if (value === undefined || value === null) return <span className="score muted">—</span>
  const tone = value >= 80 ? 'high' : value >= 65 ? 'medium' : 'low'
  return <span className={`score ${tone} ${size}`}>{value}<small>/100</small></span>
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">↗</div><h3>{title}</h3><p>{children}</p>{action}</div>
}

export function Loading() { return <div className="loading"><span/><span/><span/></div> }

export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null
  return <div className="notice error">{error instanceof Error ? error.message : 'Something went wrong.'}</div>
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="form-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>
}
