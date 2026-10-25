import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useRegister } from '../api/hooks'
import { useAuth } from '../auth'
import { Field, SelectInput, TextInput } from '../components/forms'
import { IconRadar } from '../components/icons'
import { Button, Card } from '../components/ui'
import { copy } from '../lib/copy'

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const INN = /^(\d{10}|\d{12})$/
const t = copy.register

interface Errors {
  email?: string
  password?: string
  inn?: string
}

/** Те же правила, что на сервере: ошибка видна сразу под полем, а не после ответа 422. */
function validate(email: string, password: string, inn: string): Errors {
  const errors: Errors = {}
  if (!EMAIL.test(email.trim())) errors.email = t.errors.email
  if (password.length < 8) errors.password = t.errors.password
  if (inn.trim() !== '' && !INN.test(inn.trim())) errors.inn = t.errors.inn
  return errors
}

export default function RegisterPage() {
  const { token, signIn } = useAuth()
  const navigate = useNavigate()
  const register = useRegister()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [business, setBusiness] = useState('')
  const [legalForm, setLegalForm] = useState('')
  const [inn, setInn] = useState('')
  const [errors, setErrors] = useState<Errors>({})

  if (token) return <Navigate to="/" replace />

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const found = validate(email, password, inn)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    register.mutate(
      {
        email: email.trim(),
        password,
        display_name: name.trim(),
        business_name: business.trim(),
        legal_form: legalForm === 'ООО' || legalForm === 'ИП' ? legalForm : null,
        inn: inn.trim() === '' ? null : inn.trim(),
      },
      {
        onSuccess: (r) => {
          signIn(r.access_token)
          navigate('/', { replace: true })
        },
      },
    )
  }

  const status = register.error instanceof ApiError ? register.error.status : null
  const serverError =
    status === 409 ? t.errors.taken : status === 429 ? t.errors.tooMany : register.isError ? copy.common.error : null

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

      <Card>
        <h2 className="text-lg font-semibold text-ink">{t.title}</h2>
        <p className="mt-1 text-sm text-ink-2">{t.hint}</p>
        <form onSubmit={submit} noValidate className="mt-4 grid gap-3 sm:max-w-sm">
          <Field label={t.name}>
            {(id) => (
              <TextInput id={id} required autoComplete="name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
            )}
          </Field>
          <Field label={t.email} error={errors.email}>
            {(id) => (
              <TextInput
                id={id}
                type="email"
                required
                autoComplete="username"
                maxLength={255}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            )}
          </Field>
          <Field label={t.password} hint={t.passwordHint} error={errors.password}>
            {(id) => (
              <TextInput
                id={id}
                type="password"
                required
                autoComplete="new-password"
                maxLength={72}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </Field>
          <Field label={t.business}>
            {(id) => (
              <TextInput id={id} required maxLength={255} value={business} onChange={(e) => setBusiness(e.target.value)} />
            )}
          </Field>
          <Field label={t.legalForm}>
            {(id) => (
              <SelectInput id={id} value={legalForm} onChange={(e) => setLegalForm(e.target.value)}>
                <option value="">{t.legalFormNone}</option>
                <option value="ООО">ООО</option>
                <option value="ИП">ИП</option>
              </SelectInput>
            )}
          </Field>
          <Field label={t.inn} hint={t.innHint} error={errors.inn}>
            {(id) => (
              <TextInput id={id} inputMode="numeric" maxLength={12} value={inn} onChange={(e) => setInn(e.target.value)} />
            )}
          </Field>
          {serverError && (
            <p role="alert" className="text-sm text-crit-ink">
              {serverError}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={register.isPending || !name.trim() || !business.trim()}>
            {register.isPending ? t.submitting : t.submit}
          </Button>
        </form>
        <p className="mt-4 text-sm">
          <Link to="/login" className="text-accent-ink underline">
            {t.toLogin}
          </Link>
        </p>
      </Card>
    </div>
  )
}
