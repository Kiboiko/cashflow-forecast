import { useMemo, useState } from 'react'

import { ApiError } from '../api/client'
import { useBusiness, useDeleteEvent, useEvents, useForecast, useRecurring } from '../api/hooks'
import type { EventOut, ForecastEvent, RecurringOut } from '../api/types'
import { useCurrentBusiness } from '../auth'
import { AddEventModal } from '../components/AddEventModal'
import { ConfidenceBadge, EventLine, type EventLike } from '../components/EventBits'
import { IconPlus } from '../components/icons'
import { ReceivablesPanel } from '../components/Receivables'
import { Badge, Button, Card, EmptyState, ErrorBlock, Segmented, Spinner, Tabs } from '../components/ui'
import { copy } from '../lib/copy'
import { rub, wdm } from '../lib/format'

type Tab = 'upcoming' | 'owed' | 'owe' | 'recurring'
type Filter = 'all' | 'in' | 'out' | 'must'

const whenOf = (e: ForecastEvent): string => e.date ?? e.date_range?.[0] ?? ''

/** id события в БД, если его создал предприниматель: движок отдаёт `manual:12` и `contract:7`. */
function dbId(event: ForecastEvent): number | null {
  const match = /^(?:manual|contract):(\d+)$/.exec(event.id)
  return match ? Number(match[1]) : null
}

function Upcoming({ bid, asOf, horizon }: { bid: string; asOf: string; horizon: number }) {
  const forecast = useForecast(bid, horizon)
  const events = useEvents(bid)
  const remove = useDeleteEvent(bid)
  const [filter, setFilter] = useState<Filter>('all')
  const [editing, setEditing] = useState<EventOut | null>(null)
  const [adding, setAdding] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const groups = useMemo(() => {
    const items = (forecast.data?.events ?? [])
      .filter((e) => (filter === 'all' ? true : filter === 'must' ? e.mandatory : e.direction === filter))
      .sort((a, b) => whenOf(a).localeCompare(whenOf(b)) || b.amount - a.amount)
    const by = new Map<string, ForecastEvent[]>()
    for (const event of items) by.set(whenOf(event), [...(by.get(whenOf(event)) ?? []), event])
    return [...by.entries()]
  }, [forecast.data, filter])

  if (forecast.isPending) return <Spinner />
  if (forecast.isError) return <ErrorBlock message={forecast.error.message} onRetry={() => forecast.refetch()} />
  const byId = new Map((events.data ?? []).map((e) => [e.id, e]))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented
          label="Какие события показать"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: copy.payments.filterAll },
            { value: 'in', label: copy.payments.filterIn },
            { value: 'out', label: copy.payments.filterOut },
            { value: 'must', label: copy.payments.filterMust },
          ]}
        />
        <Button variant="primary" onClick={() => setAdding(true)}>
          <IconPlus size={18} />
          {copy.payments.add}
        </Button>
      </div>
      {notice && (
        <p role="status" className="rounded-xl bg-good-soft px-3.5 py-2.5 text-sm text-good-ink">
          {notice}
        </p>
      )}

      {groups.length === 0 ? (
        <EmptyState title={copy.payments.upcomingEmpty} />
      ) : (
        groups.map(([date, items]) => (
          <section key={date} aria-label={wdm(date)}>
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-ink-2">{wdm(date)}</h3>
            <Card padded={false} className="divide-y divide-line">
              {items.map((event) => {
                const id = dbId(event)
                const own = id !== null ? byId.get(id) : undefined
                return (
                  <div key={event.id} className="p-4">
                    <EventLine event={event as EventLike} />
                    {own && (
                      <div className="mt-2 flex gap-2 pl-11">
                        {own.kind === 'manual' && (
                          <Button size="sm" variant="ghost" onClick={() => setEditing(own)}>
                            {copy.payments.edit}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={remove.isPending}
                          onClick={() => {
                            if (window.confirm(copy.payments.deleteConfirm)) remove.mutate(own.id, { onSuccess: () => setNotice('Событие убрано, прогноз пересчитан.') })
                          }}
                        >
                          {copy.payments.delete}
                        </Button>
                      </div>
                    )}
                  </div>
                )
              })}
            </Card>
          </section>
        ))
      )}

      {(adding || editing) && (
        <AddEventModal
          bid={bid}
          asOf={asOf}
          existing={editing ?? undefined}
          onClose={() => {
            setAdding(false)
            setEditing(null)
          }}
          onDone={(message) => {
            setAdding(false)
            setEditing(null)
            setNotice(message)
          }}
        />
      )}
    </div>
  )
}

function RecurringRow({ item }: { item: RecurringOut }) {
  return (
    <li className="flex items-start justify-between gap-3 py-3">
      <div className="min-w-0">
        <div className="font-medium text-ink">{item.title}</div>
        <div className="text-sm text-ink-2">
          {item.direction === 'out' ? 'Платите' : 'Получаете'} {item.schedule}
          {item.counterparty ? ` · ${item.counterparty}` : ''}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {item.active ? <ConfidenceBadge probability={item.confidence} /> : <Badge tone="warn">{copy.payments.recurringInactive}</Badge>}
          {item.mandatory && <Badge>нельзя пропустить</Badge>}
        </div>
      </div>
      <span className="tnum whitespace-nowrap font-semibold">
        {item.direction === 'out' ? '−' : '+'}
        {rub(item.amount)}
      </span>
    </li>
  )
}

function Recurring({ bid }: { bid: string }) {
  const data = useRecurring(bid)
  if (data.isPending) return <Spinner />
  if (data.isError) return <ErrorBlock onRetry={() => data.refetch()} />
  if (data.data.length === 0) return <EmptyState title={copy.payments.recurringEmpty} />
  const section = (direction: 'out' | 'in', title: string) => {
    const rows = data.data.filter((r) => r.direction === direction)
    if (rows.length === 0) return null
    return (
      <Card>
        <h3 className="mb-1 text-base font-semibold text-ink">{title}</h3>
        <ul className="divide-y divide-line">
          {rows.map((r) => (
            <RecurringRow key={r.key} item={r} />
          ))}
        </ul>
      </Card>
    )
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {section('out', 'Платежи, которые повторяются')}
      {section('in', 'Поступления, которые повторяются')}
    </div>
  )
}

export default function PaymentsPage() {
  const { business } = useCurrentBusiness()
  const details = useBusiness(business?.id)
  const [tab, setTab] = useState<Tab>('upcoming')
  if (!business || details.isPending) return <Spinner />
  if (details.isError && !(details.error instanceof ApiError && details.error.status === 409)) return <ErrorBlock onRetry={() => details.refetch()} />
  const asOf = details.data?.data_as_of ?? business.data_as_of ?? new Date().toISOString().slice(0, 10)
  const horizon = details.data?.settings.horizon_days ?? 30

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-ink sm:text-2xl">{copy.payments.title}</h1>
      <Tabs
        label="Раздел платежей"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'upcoming', label: copy.payments.tabUpcoming },
          { value: 'owed', label: copy.payments.tabOwed },
          { value: 'owe', label: copy.payments.tabOwe },
          { value: 'recurring', label: copy.payments.tabRecurring },
        ]}
      />
      {tab === 'upcoming' && <Upcoming bid={business.id} asOf={asOf} horizon={horizon} />}
      {tab === 'owed' && <ReceivablesPanel bid={business.id} direction="in" />}
      {tab === 'owe' && <ReceivablesPanel bid={business.id} direction="out" />}
      {tab === 'recurring' && <Recurring bid={business.id} />}
    </div>
  )
}
