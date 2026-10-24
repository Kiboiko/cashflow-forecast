import { lazy, Suspense, useMemo, useState } from 'react'

import { ApiError } from '../api/client'
import { useBusiness, usePlanFact, useReplayAdvance, useReplayReset, useSnapshots } from '../api/hooks'
import type { PlanFactEvent as PlanEvent, PlanFactResponse as PlanFactData, ReplayResult } from '../api/types'
import { useCurrentBusiness } from '../auth'
import { AlertsCard } from '../components/AlertsCard'
import { ChartLegend } from '../components/chart/ChartLegend'
import type { BalanceChartModel } from '../components/chart/model'
import { IconArrowDownRight, IconArrowUpRight, IconClock } from '../components/icons'
import { Badge, Button, Card, EmptyState, ErrorBlock, SectionTitle, Spinner, Stat, cx } from '../components/ui'
import { SelectInput } from '../components/forms'
import { copy } from '../lib/copy'
import { dm, dmyLong, isWeekend, pct, rub } from '../lib/format'

const BalanceChart = lazy(() => import('../components/chart/BalanceChart'))

function planFactModel(data: PlanFactData): BalanceChartModel {
  const dates = data.daily.map((d) => d.date)
  const hasFact = data.fact_until > data.snapshot_as_of
  return {
    dates,
    nonWorking: dates.map(isWeekend),
    series: [
      { id: 'plan', name: copy.planFact.plan, color: 'accent', values: data.daily.map((d) => d.plan_balance_p50) },
      ...(hasFact ? [{ id: 'fact', name: copy.planFact.fact, color: 'series2' as const, values: data.daily.map((d) => d.fact_balance ?? null) }] : []),
    ],
    threshold: null,
    events: [],
    marker: hasFact ? { date: data.fact_until, label: `данные по ${dm(data.fact_until)}` } : undefined,
    extras: [
      { label: `${copy.planFact.inflow} (план → факт)`, values: data.daily.map((d) => (d.fact_in != null ? `${rub(d.plan_in)} → ${rub(d.fact_in)}` : null)) },
      { label: `${copy.planFact.outflow} (план → факт)`, values: data.daily.map((d) => (d.fact_out != null ? `${rub(d.plan_out)} → ${rub(d.fact_out)}` : null)) },
    ],
  }
}

/* ---------- машина времени ---------- */
function TimeMachine({ bid, asOf, canAdvance, onResult }: { bid: string; asOf: string; canAdvance: boolean; onResult: (r: ReplayResult | null) => void }) {
  const advance = useReplayAdvance(bid)
  const reset = useReplayReset(bid)
  const busy = advance.isPending || reset.isPending
  const error = [advance.error, reset.error].find((e) => e instanceof ApiError)?.message ?? null
  const step = (days: number) => advance.mutate(days, { onSuccess: (r) => onResult(r) })

  return (
    <Card aria-labelledby="machine-title" className="border-accent/40">
      <SectionTitle id="machine-title">
        <IconClock size={20} className="text-accent" />
        {copy.planFact.timeMachine}
      </SectionTitle>
      <p className="-mt-1 text-sm text-ink-2">{copy.planFact.timeMachineText}</p>
      <p className="mt-2 text-lg font-semibold text-ink">{copy.planFact.today(dmyLong(asOf))}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" disabled={!canAdvance || busy} onClick={() => step(1)}>
          {copy.planFact.next}
        </Button>
        <Button disabled={!canAdvance || busy} onClick={() => step(7)}>
          {copy.planFact.week}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => reset.mutate(undefined, { onSuccess: () => onResult(null) })}>
          {copy.planFact.reset}
        </Button>
      </div>
      {!canAdvance && <p className="mt-2 text-sm text-ink-2">{copy.planFact.end}</p>}
      {busy && <p role="status" className="mt-2 text-sm text-ink-2">{copy.common.loading}</p>}
      {error && (
        <p role="alert" className="mt-2 text-sm text-crit-ink">
          {error}
        </p>
      )}
    </Card>
  )
}

function StepBanner({ result }: { result: ReplayResult }) {
  const before = result.gap_probability_before
  return (
    <Card className="border-accent/40 bg-accent-soft" role="status">
      <p className="font-medium text-ink">{copy.planFact.stepDone(dmyLong(result.previous_as_of), dmyLong(result.as_of))}</p>
      <ul className="mt-1.5 space-y-0.5 text-sm text-ink-2">
        {before != null && <li>{copy.planFact.chanceMoved(pct(before), pct(result.gap_probability))}</li>}
        <li>Новых операций в выписке: {result.new_operations}</li>
        <li>{result.alerts.length ? copy.planFact.newAlerts(result.alerts.length) : copy.planFact.noAlerts}</li>
      </ul>
      <p className="mt-2 text-sm text-ink">{result.headline}</p>
    </Card>
  )
}

/* ---------- что ждали и что случилось ---------- */
const STATUS_TONE = { fulfilled: 'good', partially: 'warn', overdue: 'crit' } as const

function EventRow({ event }: { event: PlanEvent }) {
  const out = event.direction === 'out'
  const labels = out ? copy.planFact.statusOut : copy.planFact.statusIn
  const tone = out && event.status === 'overdue' ? 'neutral' : (STATUS_TONE[event.status as keyof typeof STATUS_TONE] ?? 'neutral')
  return (
    <li className="flex items-start gap-3 py-3">
      <span className={cx('mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-full', out ? 'bg-surface-2 text-ink-2' : 'bg-good-soft text-good-ink')}>
        {out ? <IconArrowDownRight size={18} /> : <IconArrowUpRight size={18} />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="font-medium text-ink">{event.title}</span>
          <span className="tnum font-semibold">{rub(event.amount)}</span>
        </div>
        <div className="mt-0.5 text-sm text-ink-2">ждали {dm(event.expected_date)}</div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <Badge tone={tone}>{labels[event.status] ?? event.status}</Badge>
          {event.days_late ? <Badge>{copy.planFact.daysLate(event.days_late)}</Badge> : null}
        </div>
      </div>
    </li>
  )
}

function Events({ events }: { events: PlanEvent[] }) {
  const order: Array<[string, string]> = [['overdue', 'Не пришло или не прошло'], ['partially', 'Пришло не всё'], ['fulfilled', 'Всё по плану']]
  const groups = order.map(([status, title]) => [title, events.filter((e) => e.status === status)] as const).filter(([, items]) => items.length)
  return (
    <Card aria-labelledby="events-title">
      <SectionTitle id="events-title">{copy.planFact.eventsTitle}</SectionTitle>
      {groups.length === 0 ? (
        <p className="text-ink-2">{copy.planFact.eventsEmpty}</p>
      ) : (
        <div className="space-y-4">
          {groups.map(([title, items]) => (
            <section key={title}>
              <h3 className="text-sm font-semibold uppercase tracking-wide text-ink-2">{title}</h3>
              <ul className="divide-y divide-line">
                {items.map((event) => (
                  <EventRow key={event.event_id} event={event} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </Card>
  )
}

/* ---------- страница ---------- */
export default function PlanFactPage() {
  const { business } = useCurrentBusiness()
  const bid = business?.id
  const details = useBusiness(bid)
  const [snapshotId, setSnapshotId] = useState<number | null>(null)
  const [last, setLast] = useState<ReplayResult | null>(null)
  const fact = usePlanFact(bid, snapshotId)
  const snapshots = useSnapshots(bid)

  const data = fact.data
  const model = useMemo(() => (data ? planFactModel(data) : null), [data])

  if (!bid || details.isPending || fact.isPending) return <Spinner />
  if (fact.isError) return <ErrorBlock message={fact.error.message} onRetry={() => fact.refetch()} />
  if (!data || !model) return <Spinner />

  const replay = details.data?.replay
  const showMachine = Boolean(replay?.enabled) || (snapshots.data?.length ?? 0) > 1
  const hasFact = data.fact_until > data.snapshot_as_of
  const asOf = details.data?.data_as_of ?? data.fact_until

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-ink sm:text-2xl">{copy.planFact.title}</h1>
      <p className="text-ink-2">{copy.planFact.intro}</p>

      {showMachine && <TimeMachine bid={bid} asOf={asOf} canAdvance={replay?.can_advance ?? false} onResult={(r) => { setLast(r); setSnapshotId(null) }} />}
      {last && <StepBanner result={last} />}

      {(snapshots.data?.length ?? 0) > 1 && (
        <label className="grid gap-1 text-sm font-medium text-ink-2 sm:max-w-sm">
          {copy.planFact.compareWith}
          <SelectInput value={snapshotId ?? ''} onChange={(e) => setSnapshotId(e.target.value === '' ? null : Number(e.target.value))}>
            <option value="">самым ранним из актуальных</option>
            {snapshots.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {dmyLong(s.as_of)} · шанс нехватки {pct(s.gap_probability)}
              </option>
            ))}
          </SelectInput>
        </label>
      )}

      <Card aria-labelledby="pf-chart">
        <SectionTitle id="pf-chart" hint="Синяя линия — что мы ожидали, оранжевая — что было на самом деле.">
          {copy.planFact.chartTitle}
          <span className="ml-2 text-sm font-normal text-ink-2">прогноз от {dmyLong(data.snapshot_as_of)}</span>
        </SectionTitle>
        <Suspense fallback={<Spinner />}>
          <div className="-mx-1 h-[280px] sm:mx-0 sm:h-[340px]">
            <BalanceChart model={model} label={`${copy.planFact.chartTitle}, прогноз от ${dmyLong(data.snapshot_as_of)}`} busy={fact.isPlaceholderData} />
          </div>
        </Suspense>
        <ChartLegend model={model} />
      </Card>

      {hasFact ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label={copy.planFact.inflow} value={`${data.summary.inflow_deviation_pct > 0 ? '+' : data.summary.inflow_deviation_pct < 0 ? '−' : ''}${Math.abs(Math.round(data.summary.inflow_deviation_pct))}%`} sub={copy.planFact.deviation(data.summary.inflow_deviation_pct)} tone={data.summary.inflow_deviation_pct <= -10 ? 'crit' : 'neutral'} />
            <Stat label={copy.planFact.outflow} value={`${data.summary.outflow_deviation_pct > 0 ? '+' : data.summary.outflow_deviation_pct < 0 ? '−' : ''}${Math.abs(Math.round(data.summary.outflow_deviation_pct))}%`} sub={copy.planFact.deviation(data.summary.outflow_deviation_pct)} />
            <div className="col-span-2 sm:col-span-1">
              <Stat label={copy.planFact.missed} value={data.summary.missed_events} sub={copy.planFact.missedSub} tone={data.summary.missed_events > 0 ? 'crit' : 'good'} />
            </div>
          </div>
          <Events events={data.events} />
        </>
      ) : (
        <EmptyState title="Пока нечего сравнивать" text={copy.planFact.noFactYet} />
      )}

      <AlertsCard bid={bid} />
    </div>
  )
}
