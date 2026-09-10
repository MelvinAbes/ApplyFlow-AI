import { FormEvent, useEffect, useState } from 'react'
import { Bot, Check, Database, ExternalLink, LogOut, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '../api'
import { EmptyState, ErrorMessage, Field, Loading, PageHeader } from '../components'
import type { AIConnectionStatus, AnswerBankEntry, CodexLoginStart, CodexLoginStatus } from '../types'

export function SettingsPage() {
  const [status, setStatus] = useState<AIConnectionStatus>()
  const [answers, setAnswers] = useState<AnswerBankEntry[]>()
  const [error, setError] = useState<unknown>()
  const [show, setShow] = useState(false)
  const [login, setLogin] = useState<CodexLoginStart>()
  const [connecting, setConnecting] = useState(false)

  const load = async () => {
    try {
      const [connection, answerBank] = await Promise.all([
        api.get<AIConnectionStatus>('/settings/status'),
        api.get<AnswerBankEntry[]>('/answer-bank'),
      ])
      setStatus(connection)
      setAnswers(answerBank)
    } catch (loadError) {
      setError(loadError)
    }
  }

  useEffect(() => { void load() }, [])

  useEffect(() => {
    if (!login) return
    const timer = window.setInterval(async () => {
      try {
        const attempt = await api.get<CodexLoginStatus>(`/settings/codex/login/${login.login_id}`)
        if (attempt.status === 'completed') {
          window.clearInterval(timer)
          setLogin(undefined)
          setConnecting(false)
          await load()
        } else if (attempt.status === 'failed' || attempt.status === 'cancelled') {
          window.clearInterval(timer)
          setConnecting(false)
          setError(new Error(attempt.error || 'ChatGPT sign-in did not complete'))
        }
      } catch (pollError) {
        window.clearInterval(timer)
        setConnecting(false)
        setError(pollError)
      }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [login])

  const connectCodex = async () => {
    setError(undefined)
    setConnecting(true)
    try {
      const attempt = await api.post<CodexLoginStart>('/settings/codex/login')
      setLogin(attempt)
      window.open(attempt.auth_url, '_blank', 'noopener,noreferrer')
    } catch (connectError) {
      setConnecting(false)
      setError(connectError)
    }
  }

  const disconnectCodex = async () => {
    setError(undefined)
    try {
      await api.delete('/settings/codex/session')
      setLogin(undefined)
      await load()
    } catch (disconnectError) {
      setError(disconnectError)
    }
  }

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    try {
      await api.post('/answer-bank', {
        canonical_key: data.get('canonical_key'),
        question: data.get('question'),
        answer: data.get('answer'),
        aliases: String(data.get('aliases') || '').split(',').map(value => value.trim()).filter(Boolean),
      })
      form.reset()
      setShow(false)
      await load()
    } catch (saveError) {
      setError(saveError)
    }
  }

  const remove = async (id: string) => {
    await api.delete(`/answer-bank/${id}`)
    await load()
  }

  if (!status && !error) return <Loading/>

  return <>
    <PageHeader
      eyebrow="Configuration"
      title="Settings & answer bank"
      description="Connect your ChatGPT account for Codex intelligence and store reusable factual answers."
    />
    <ErrorMessage error={error}/>
    {status && <section className="settings-grid">
      <article className="panel setting-card connection-card">
        <div className={`setting-icon ${status.codex_connected ? 'ok' : ''}`}><Bot size={22}/></div>
        <div>
          <h2>ChatGPT / Codex</h2>
          {status.codex_connected
            ? <p>Connected{status.codex_plan_type ? <> on the <strong>{status.codex_plan_type}</strong> plan</> : null}{status.codex_email ? <> as {status.codex_email}</> : null}.</p>
            : <p>Sign in with ChatGPT to use your Codex subscription. No developer account or API key is required.</p>}
          <small>
            {status.active_provider === 'codex'
              ? `Active provider · ${status.model}`
              : `Selected provider · ${status.selected_provider}`}
          </small>
          {login && <a className="login-link" href={login.auth_url} target="_blank" rel="noreferrer">
            Complete sign-in <ExternalLink size={12}/>
          </a>}
          {status.codex_error && <small className="connection-error">{status.codex_error}</small>}
        </div>
        {status.codex_connected
          ? <button className="icon-button" type="button" title="Disconnect Codex" onClick={disconnectCodex}><LogOut size={16}/></button>
          : <button className="button primary small" type="button" disabled={connecting || !status.codex_runtime_available} onClick={connectCodex}>
              {connecting ? 'Waiting…' : 'Connect'}
            </button>}
      </article>
      <article className="panel setting-card">
        <div className={`setting-icon ${status.openai_api_configured ? 'ok' : ''}`}><Bot size={22}/></div>
        <div>
          <h2>API fallback</h2>
          <p>{status.openai_api_configured
            ? 'An OpenAI API fallback is configured for AI_PROVIDER=openai or auto.'
            : 'Optional only. Deterministic behavior remains available when Codex is disconnected.'}</p>
          <small>{status.ai_invocation_count} successful AI generations recorded</small>
        </div>
        {status.openai_api_configured && <Check size={18}/>}
      </article>
      <article className="panel setting-card">
        <div className="setting-icon ok"><ShieldCheck size={22}/></div>
        <div><h2>Submission safety</h2><p>Automatic submission is disabled. Backend approval and a single-use token are mandatory.</p><small>AUTO_SUBMIT = false</small></div>
        <Check size={18}/>
      </article>
      <article className="panel setting-card">
        <div className="setting-icon"><Database size={22}/></div>
        <div><h2>Local data</h2><p>Resumes, browser state, Codex credentials, screenshots, and application data stay outside version control.</p><small>{status.data_directory}</small></div>
      </article>
    </section>}
    <section className="panel answer-bank">
      <div className="panel-heading">
        <div><p className="eyebrow">Deterministic reuse</p><h2>Answer bank</h2></div>
        <button className="button secondary small" onClick={() => setShow(!show)}><Plus size={15}/> Add answer</button>
      </div>
      {show && <form className="answer-form" onSubmit={add}>
        <Field label="Canonical key"><input name="canonical_key" placeholder="work_authorization" pattern="[a-z][a-z0-9_]*" required/></Field>
        <Field label="Canonical question"><input name="question" required/></Field>
        <Field label="Answer"><textarea name="answer" required/></Field>
        <Field label="Aliases" hint="Comma separated"><input name="aliases"/></Field>
        <button className="button primary">Save answer</button>
      </form>}
      {answers?.length
        ? <div className="answer-list">{answers.map(item => <article key={item.id}>
            <div><span className="code-label">{item.canonical_key}</span><h3>{item.question}</h3><p>{item.answer}</p>{item.aliases.length > 0 && <small>Also matches: {item.aliases.join(' · ')}</small>}</div>
            <button className="icon-button danger" onClick={() => remove(item.id)}><Trash2 size={16}/></button>
          </article>)}</div>
        : <EmptyState title="No saved answers">Add verified answers for work authorization, weekly hours, start date, languages, and other recurring questions.</EmptyState>}
    </section>
  </>
}
