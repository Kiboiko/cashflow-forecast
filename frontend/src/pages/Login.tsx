import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useDemoLogin, useLogin, usePersonas } from '../api/hooks'
import { useAuth } from '../auth'
import { IconRadar } from '../components/icons'
import { Button, Card, ErrorBlock, Spinner } from '../components/ui'
import { copy } from '../lib/copy'

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
      </Card>
    </div>
  )
}
