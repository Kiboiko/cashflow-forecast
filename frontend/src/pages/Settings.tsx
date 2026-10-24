import { useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import { useBusiness, useUpdateSettings } from '../api/hooks'
import type { BusinessOut } from '../api/types'
import { useCurrentBusiness } from '../auth'
import { Check, Field, TextInput } from '../components/forms'
import { IconPlus, IconX } from '../components/icons'
import { Button, Card, ErrorBlock, SectionTitle, Segmented, Spinner } from '../components/ui'
import { copy } from '../lib/copy'

interface AccountDraft {
  bank: string
  balance: string
}

const toNumber = (text: string): number => Number(text.replace(/\s/g, '').replace(',', '.'))

function SettingsForm({ business }: { business: BusinessOut }) {
  const update = useUpdateSettings(business.id)
  const s = business.settings
  const [horizon, setHorizon] = useState<30 | 60 | 90>(s.horizon_days)
  const [threshold, setThreshold] = useState(String(s.threshold))
  const [overdraft, setOverdraft] = useState(String(s.overdraft_limit))
  const [accounts, setAccounts] = useState<AccountDraft[]>(s.other_accounts.map((a) => ({ bank: a.bank, balance: String(a.balance) })))
  const [includeOther, setIncludeOther] = useState(s.include_other_accounts)
  const [withdrawals, setWithdrawals] = useState(s.include_owner_withdrawals)
  const [telegram, setTelegram] = useState(business.telegram_chat_id ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)
  const apiError = update.error instanceof ApiError ? update.error.message : null

  const save = () => {
    const found: Record<string, string> = {}
    const th = toNumber(threshold)
    const od = toNumber(overdraft)
    if (!Number.isFinite(th) || th < 0) found.threshold = 'Введите число не меньше нуля'
    if (!Number.isFinite(od) || od < 0) found.overdraft = 'Введите число не меньше нуля'
    const cleaned = accounts.filter((a) => a.bank.trim() || a.balance.trim())
    cleaned.forEach((a, i) => {
      if (!a.bank.trim()) found[`bank${i}`] = 'Укажите банк'
      if (!Number.isFinite(toNumber(a.balance))) found[`balance${i}`] = 'Введите остаток числом'
    })
    setErrors(found)
    setSaved(false)
    if (Object.keys(found).length) return
    update.mutate(
      {
        horizon_days: horizon,
        threshold: th,
        overdraft_limit: od,
        other_accounts: cleaned.map((a) => ({ bank: a.bank.trim(), balance: toNumber(a.balance) })),
        include_other_accounts: includeOther,
        include_owner_withdrawals: withdrawals,
        telegram_chat_id: telegram.trim(),
      },
      { onSuccess: () => setSaved(true) },
    )
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <Card aria-labelledby="forecast-settings">
        <SectionTitle id="forecast-settings">Прогноз</SectionTitle>
        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <span className="text-sm font-medium text-ink-2">{copy.settings.horizon}</span>
            <div className="flex">
              <Segmented
                label={copy.settings.horizon}
                value={horizon}
                onChange={setHorizon}
                options={[{ value: 30, label: '30 дней' }, { value: 60, label: '60 дней' }, { value: 90, label: '90 дней' }]}
              />
            </div>
          </div>
          <Field label={copy.settings.threshold} hint={copy.settings.thresholdHint} error={errors.threshold}>
            {(id) => <TextInput id={id} inputMode="decimal" value={threshold} onChange={(e) => setThreshold(e.target.value)} className="sm:max-w-xs" />}
          </Field>
          <Field label={copy.settings.overdraft} hint={copy.settings.overdraftHint} error={errors.overdraft}>
            {(id) => <TextInput id={id} inputMode="decimal" value={overdraft} onChange={(e) => setOverdraft(e.target.value)} className="sm:max-w-xs" />}
          </Field>
          <Check checked={withdrawals} onChange={setWithdrawals}>
            {copy.settings.withdrawals}
          </Check>
          <p className="text-sm text-ink-2">
            {copy.settings.risk}: {copy.settings.riskText}
          </p>
        </div>
      </Card>

      <Card aria-labelledby="other-accounts">
        <SectionTitle id="other-accounts">{copy.settings.otherTitle}</SectionTitle>
        <p className="-mt-1 mb-3 text-sm text-ink-2">{copy.settings.otherHint}</p>
        <ul className="space-y-3">
          {accounts.map((account, i) => (
            <li key={i} className="grid grid-cols-[1fr_1fr_auto] items-start gap-2">
              <Field label={copy.settings.bank} error={errors[`bank${i}`]}>
                {(id) => <TextInput id={id} value={account.bank} onChange={(e) => setAccounts(accounts.map((a, k) => (k === i ? { ...a, bank: e.target.value } : a)))} placeholder="Т-Банк" />}
              </Field>
              <Field label={copy.settings.balance} error={errors[`balance${i}`]}>
                {(id) => <TextInput id={id} inputMode="decimal" value={account.balance} onChange={(e) => setAccounts(accounts.map((a, k) => (k === i ? { ...a, balance: e.target.value } : a)))} placeholder="120 000" />}
              </Field>
              <Button variant="ghost" className="mt-6" aria-label="Убрать счёт" onClick={() => setAccounts(accounts.filter((_, k) => k !== i))}>
                <IconX size={18} />
              </Button>
            </li>
          ))}
        </ul>
        <Button className="mt-3" size="sm" onClick={() => setAccounts([...accounts, { bank: '', balance: '' }])}>
          <IconPlus size={16} />
          {copy.settings.otherAdd}
        </Button>
        {accounts.length > 0 && (
          <div className="mt-2">
            <Check checked={includeOther} onChange={setIncludeOther}>
              {copy.settings.otherInclude}
            </Check>
          </div>
        )}
      </Card>

      <Card aria-labelledby="telegram">
        <SectionTitle id="telegram">Уведомления</SectionTitle>
        <Field label={copy.settings.telegram} hint={copy.settings.telegramHint}>
          {(id) => <TextInput id={id} value={telegram} onChange={(e) => setTelegram(e.target.value)} placeholder="123456789" className="sm:max-w-xs" inputMode="numeric" />}
        </Field>
      </Card>

      {apiError && (
        <p role="alert" className="rounded-xl bg-crit-soft px-3.5 py-2.5 text-sm text-crit-ink">
          {apiError}
        </p>
      )}
      {saved && (
        <p role="status" className="rounded-xl bg-good-soft px-3.5 py-2.5 text-sm text-good-ink">
          {copy.settings.saved}
        </p>
      )}
      <Button type="submit" variant="primary" disabled={update.isPending} className="w-full sm:w-auto">
        {copy.common.save}
      </Button>
    </form>
  )
}

export default function SettingsPage() {
  const { business } = useCurrentBusiness()
  const details = useBusiness(business?.id)
  const [ready, setReady] = useState<BusinessOut | null>(null)

  // Форму наполняем один раз: после сохранения сервер возвращает те же значения, а поля не должны «прыгать»
  useEffect(() => {
    if (details.data && !ready) setReady(details.data)
  }, [details.data, ready])

  if (!business || details.isPending) return <Spinner />
  if (details.isError) return <ErrorBlock message={details.error.message} onRetry={() => details.refetch()} />
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-ink sm:text-2xl">{copy.settings.title}</h1>
      <p className="text-ink-2">{copy.settings.intro}</p>
      {ready ? <SettingsForm key={business.id} business={ready} /> : <Spinner />}
    </div>
  )
}
