import dayjs from 'dayjs'
import { useState } from 'react'

import { ApiError } from '../api/client'
import { useCreateEvent, usePatchEvent } from '../api/hooks'
import type { EventOut } from '../api/types'
import { copy } from '../lib/copy'
import { Check, Field, SelectInput, TextInput } from './forms'
import { Modal } from './Modal'
import { Button } from './ui'

const SURE = [
  { value: '1', label: copy.payments.sure100 },
  { value: '0.8', label: copy.payments.sure80 },
  { value: '0.5', label: copy.payments.sure50 },
]

/** «Добавить событие» (4 поля + «насколько уверены») и правка уже добавленного события. После сохранения — новый прогноз. */
export function AddEventModal({
  bid,
  asOf,
  existing,
  onClose,
  onDone,
}: {
  bid: string
  asOf: string
  existing?: EventOut
  onClose: () => void
  onDone: (message: string) => void
}) {
  const create = useCreateEvent(bid)
  const patch = usePatchEvent(bid)
  const minDate = dayjs(asOf).add(1, 'day').format('YYYY-MM-DD')
  const [title, setTitle] = useState(existing?.title ?? '')
  const [direction, setDirection] = useState<'in' | 'out'>(existing?.direction ?? 'in')
  const [amount, setAmount] = useState(existing ? String(existing.amount) : '')
  const [date, setDate] = useState(existing?.expected_date ?? '')
  const [sure, setSure] = useState(String(existing?.probability ?? 1))
  const [mandatory, setMandatory] = useState(existing?.mandatory ?? false)
  const [party, setParty] = useState(existing?.counterparty_name ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})

  const mutation = existing ? patch : create
  const apiError = mutation.error instanceof ApiError ? mutation.error.message : null

  const submit = () => {
    const found: Record<string, string> = {}
    const value = Number(amount.replace(/\s/g, '').replace(',', '.'))
    if (!title.trim()) found.title = 'Напишите, что это за событие'
    if (!Number.isFinite(value) || value <= 0) found.amount = 'Сумма должна быть больше нуля'
    if (!date) found.date = 'Укажите дату'
    else if (date < minDate) found.date = 'Дата должна быть после даты данных — прогноз строится вперёд'
    setErrors(found)
    if (Object.keys(found).length) return
    const done = { onSuccess: () => onDone(existing ? 'Изменения сохранены, прогноз пересчитан.' : copy.payments.created) }
    if (existing) {
      patch.mutate({ id: existing.id, title: title.trim(), amount: value, date, probability: Number(sure), mandatory, counterparty_name: party.trim() || null }, done)
    } else {
      create.mutate({ title: title.trim(), direction, amount: value, date, probability: Number(sure), mandatory, counterparty_name: party.trim() || null }, done)
    }
  }

  return (
    <Modal
      title={existing ? copy.payments.edit : copy.payments.addTitle}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{copy.common.cancel}</Button>
          <Button variant="primary" disabled={mutation.isPending} onClick={submit}>
            {copy.common.save}
          </Button>
        </>
      }
    >
      {!existing && <p className="mb-4 text-sm text-ink-2">{copy.payments.addHint}</p>}
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <Field label={copy.payments.what} error={errors.title}>
          {(id) => <TextInput id={id} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={copy.payments.whatPlaceholder} maxLength={120} />}
        </Field>
        {!existing && (
          <Field label={copy.payments.direction}>
            {(id) => (
              <SelectInput id={id} value={direction} onChange={(e) => setDirection(e.target.value as 'in' | 'out')}>
                <option value="in">{copy.payments.dirIn}</option>
                <option value="out">{copy.payments.dirOut}</option>
              </SelectInput>
            )}
          </Field>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={copy.payments.how} error={errors.amount}>
            {(id) => <TextInput id={id} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="300 000" />}
          </Field>
          <Field label={copy.payments.when} error={errors.date}>
            {(id) => <TextInput id={id} type="date" min={minDate} value={date} onChange={(e) => setDate(e.target.value)} />}
          </Field>
        </div>
        <Field label={copy.payments.sure}>
          {(id) => (
            <SelectInput id={id} value={sure} onChange={(e) => setSure(e.target.value)}>
              {SURE.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <Field label="Кто (необязательно)">
          {(id) => <TextInput id={id} value={party} onChange={(e) => setParty(e.target.value)} placeholder="ООО «Ромашка»" maxLength={120} />}
        </Field>
        {(existing ? existing.direction === 'out' : direction === 'out') && (
          <Check checked={mandatory} onChange={setMandatory}>
            {copy.payments.mandatory}
          </Check>
        )}
        {apiError && (
          <p role="alert" className="text-sm text-crit-ink">
            {apiError}
          </p>
        )}
        <button type="submit" className="sr-only">
          {copy.common.save}
        </button>
      </form>
    </Modal>
  )
}
