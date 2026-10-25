import { useMemo, useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useDemoLogin, useJuryCompanies, useJuryLogin, useLogin, usePersonas } from '../api/hooks'
import type { JuryCompany } from '../api/types'
import { useAuth } from '../auth'
import { SelectInput } from '../components/forms'
import { IconRadar } from '../components/icons'
import { Button, Card, ErrorBlock, Spinner } from '../components/ui'
import { copy } from '../lib/copy'

function byIndustry(companies: JuryCompany[]): Array<[string, JuryCompany[]]> {
  const groups = new Map<string, JuryCompany[]>()
  for (const company of companies) {
    const key = company.industry ?? 'Другое'
    groups.set(key, [...(groups.get(key) ?? []), company])
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b, 'ru'))
}

/** Режим жюри: любая компания датасета как отдельный предприниматель. Скрыт под заголовком; нет датасета — блока нет совсем. */
function JuryPicker({ onToken }: { onToken: (token: string) => void }) {
  const companies = useJuryCompanies()
  const jury = useJuryLogin()
  const [company, setCompany] = useState('')
  const list = useMemo(() => companies.data ?? [], [companies.data])
  const grouped = useMemo(() => byIndustry(list), [list])
  if (list.length === 0) return null
  const error = jury.error instanceof ApiError ? jury.error.message : jury.isError ? copy.common.error : null

  return (
    <details className="mt-6 rounded-2xl border border-line bg-surface p-4">
      <summary className="cursor-pointer text-sm font-medium text-ink-2">{copy.login.juryTitle(list.length)}</summary>
      <p className="mt-2 text-sm text-ink-2">{copy.login.juryHint}</p>
      <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-end">
        <label className="grid flex-1 gap-1 text-sm font-medium text-ink-2">
          {copy.login.juryCompany}
          <SelectInput value={company} onChange={(e) => setCompany(e.target.value)} disabled={jury.isPending}>
            <option value="" disabled>
              {copy.login.juryPick}
            </option>
            {grouped.map(([industry, items]) => (
              <optgroup key={industry} label={industry}>
                {items.map((item) => (
                  <option key={item.company_id} value={item.company_id}>
                    {item.company_id} · {item.name}
                    {item.legal_form ? ` (${item.legal_form})` : ''}
                  </option>
                ))}
              </optgroup>
            ))}
          </SelectInput>
        </label>
        <Button variant="primary" disabled={!company || jury.isPending} onClick={() => jury.mutate(company, { onSuccess: (r) => onToken(r.access_token) })}>
          {jury.isPending ? copy.login.juryLoading : copy.login.juryOpen}
        </Button>
      </div>
      {jury.isPending && (
        <p role="status" className="mt-3 text-sm text-ink-2">
          {copy.login.juryWait}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-crit-ink">
          {error}
        </p>
      )}
    </details>
  )
}

export default function LoginPage() {
  const { token, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const target = (location.state as { from?: string } | null)?.from ?? '/'
  const personas = usePersonas()
  const demoLogin = useDemoLogin()
  const login = useLogin()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  if (token) return <Navigate to={target} replace />

  const done = (accessToken: string) => {
    signIn(accessToken)
    navigate(target, { replace: true })
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    login.mutate({ email, password }, { onSuccess: (r) => done(r.access_token) })
  }

  const loginError = login.error instanceof ApiError ? login.error : null

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 py-8 sm:py-14">
      <div className="mb-8 flex items-center gap-3">
        <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-white">
          <IconRadar size={26} />
        </span>
        <div>
          <h1 className="text-2xl font-semibold text-ink">{copy.appName}</h1>
          <p className="text-ink-2">{copy.tagline}</p>
        </div>
      </div>

      {personas.isPending ? (
        <Spinner />
      ) : personas.data && personas.data.length > 0 ? (
        <section aria-labelledby="personas-title">
          <h2 id="personas-title" className="text-lg font-semibold text-ink">
            {copy.login.personasTitle}
          </h2>
          <p className="mb-4 mt-1 text-sm text-ink-2">{copy.login.personasHint}</p>
          <ul className="grid gap-3 sm:grid-cols-3">
            {personas.data.map((persona) => (
              <li key={persona.email}>
                <button
                  type="button"
                  disabled={demoLogin.isPending}
                  onClick={() => demoLogin.mutate(persona.email, { onSuccess: (r) => done(r.access_token) })}
                  className="flex h-full w-full items-start gap-3 rounded-2xl border border-line bg-surface p-4 text-left transition hover:border-accent hover:shadow-md disabled:opacity-60 sm:flex-col sm:gap-0"
                >
                  <span className="flex h-11 w-11 flex-none items-center justify-center rounded-full bg-accent-soft text-lg font-semibold text-accent-ink">
                    {persona.display_name.slice(0, 1)}
                  </span>
                  <span className="min-w-0 sm:mt-3">
                    <span className="block text-lg font-semibold text-ink">{persona.display_name}</span>
                    <span className="block text-sm text-ink-2">{persona.title}</span>
                    {persona.note && <span className="mt-2 block text-sm text-ink-2">{persona.note}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {demoLogin.isError && <p role="alert" className="mt-3 text-sm text-crit-ink">{copy.common.error}</p>}
        </section>
      ) : personas.isError && !(personas.error instanceof ApiError && personas.error.status === 404) ? (
        <ErrorBlock onRetry={() => personas.refetch()} />
      ) : null}

      <JuryPicker onToken={done} />

      <Card className="mt-8">
        <h2 className="text-lg font-semibold text-ink">{personas.data?.length ? copy.login.orLogin : copy.login.title}</h2>
        <form onSubmit={submit} className="mt-3 grid gap-3 sm:max-w-sm">
          <label className="grid gap-1 text-sm text-ink-2">
            {copy.login.email}
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="min-h-11 rounded-xl border border-line-2 bg-surface px-3 text-base text-ink"
            />
          </label>
          <label className="grid gap-1 text-sm text-ink-2">
            {copy.login.password}
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="min-h-11 rounded-xl border border-line-2 bg-surface px-3 text-base text-ink"
            />
          </label>
          {loginError && (
            <p role="alert" className="text-sm text-crit-ink">
              {loginError.status === 429 ? copy.login.tooMany : copy.login.wrong}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={login.isPending}>
            {copy.login.submit}
          </Button>
        </form>
        <p className="mt-4 text-sm">
          <Link to="/register" className="text-accent-ink underline">
            {copy.login.toRegister}
          </Link>
        </p>
      </Card>
    </div>
  )
}
