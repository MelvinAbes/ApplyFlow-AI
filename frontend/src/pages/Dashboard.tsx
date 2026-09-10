import { useEffect, useState } from 'react'
import { ArrowRight, BriefcaseBusiness, CheckCircle2, Clock3, Sparkles, Trophy, UsersRound } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorMessage, Loading, PageHeader, StatusBadge } from '../components'
import type { DashboardStats } from '../types'

export function DashboardPage() {
  const [data, setData] = useState<DashboardStats>()
  const [error, setError] = useState<unknown>()
  useEffect(() => { api.get<DashboardStats>('/dashboard').then(setData).catch(setError) }, [])
  if (!data && !error) return <Loading/>
  const stats = data ? [
    { label: 'Jobs discovered', value: data.jobs_discovered, Icon: BriefcaseBusiness, tone: 'forest' },
    { label: 'Strong matches', value: data.strong_matches, Icon: Sparkles, tone: 'gold' },
    { label: 'Ready to review', value: data.ready_to_apply, Icon: Clock3, tone: 'blue' },
    { label: 'Submitted', value: data.applications_submitted, Icon: CheckCircle2, tone: 'forest' },
    { label: 'Interviews', value: data.interviews, Icon: UsersRound, tone: 'violet' },
    { label: 'Offers', value: data.offers, Icon: Trophy, tone: 'gold' },
  ] : []
  return <>
    <PageHeader eyebrow="Wednesday · Application workspace" title="Good afternoon" description="Prioritize the strongest roles, review prepared applications, and stay in control of every submission." action={<Link className="button primary" to="/jobs">Add a job <ArrowRight size={16}/></Link>}/>
    <ErrorMessage error={error}/>
    {data && <>
      <section className="stat-grid">
        {stats.map(({ label, value, Icon, tone }) => <article className={`stat-card ${tone}`} key={label}><div className="stat-icon"><Icon size={20}/></div><strong>{value}</strong><span>{label}</span></article>)}
      </section>
      <div className="dashboard-grid">
        <section className="panel span-2"><div className="panel-heading"><div><p className="eyebrow">Opportunity ranking</p><h2>Highest matches</h2></div><Link to="/jobs">View all <ArrowRight size={15}/></Link></div>
          {data.highest_matches.length ? <div className="rank-list">{data.highest_matches.map((job, index) => <Link to={`/jobs/${job.job_id}`} className="rank-row" key={job.job_id}><span className="rank-index">{String(index + 1).padStart(2, '0')}</span><span className="rank-score">{job.score}</span><span><strong>{job.role}</strong><small>{job.company}</small></span><ArrowRight size={17}/></Link>)}</div> : <EmptyState title="No scored jobs yet">Add a job and run an analysis to see your best opportunities here.</EmptyState>}
        </section>
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Action needed</p><h2>Application queue</h2></div></div>
          {data.application_queue.length ? <div className="compact-list">{data.application_queue.map(app => <Link to={`/applications/${app.application_id}/review`} key={app.application_id}><span><strong>{app.role}</strong><small>{app.company}</small></span><StatusBadge status={app.status}/></Link>)}</div> : <EmptyState title="Queue cleared">Prepared applications that need your input will appear here.</EmptyState>}
        </section>
        <section className="panel full"><div className="panel-heading"><div><p className="eyebrow">Tracker</p><h2>Recent applications</h2></div><Link to="/applications">Open tracker <ArrowRight size={15}/></Link></div>
          {data.recent_applications.length ? <div className="table-wrap"><table><thead><tr><th>Role</th><th>Company</th><th>Date</th><th>Status</th></tr></thead><tbody>{data.recent_applications.map(app => <tr key={app.application_id}><td><Link to={`/applications/${app.application_id}`}>{app.role}</Link></td><td>{app.company}</td><td>{new Date(app.date).toLocaleDateString()}</td><td><StatusBadge status={app.status}/></td></tr>)}</tbody></table></div> : <EmptyState title="No submissions yet">Submitted applications will show up in this activity view.</EmptyState>}
        </section>
      </div>
    </>}
  </>
}
