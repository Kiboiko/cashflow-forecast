import { lazy, Suspense, useMemo, useState } from 'react'

import type { ForecastResponse } from '../api/types'
import { copy } from '../lib/copy'
import { dm, pct, rub, wdm } from '../lib/format'
import { ChartLegend } from './chart/ChartLegend'
import { forecastModel } from './chart/forecastModel'
import type { ChartEvent } from './chart/model'
import { EventLine } from './EventBits'
import { Button, Card, SectionTitle, Segmented, Spinner } from './ui'

const BalanceChart = lazy(() => import('./chart/BalanceChart'))

export type Horizon = 30 | 60 | 90
const HORIZONS: Array<{ value: Horizon; label: string }> = [
  { value: 30, label: '30 дней' },
  { value: 60, label: '60 дней' },
  { value: 90, label: '90 дней' },
]

/** Таблица значений графика — равноправная замена подсказкам (доступность). */
function ForecastTable({ forecast }: { forecast: ForecastResponse }) {
  return (
    <div className="max-h-96 overflow-auto rounded-xl border border-line">
      <table className="w-full min-w-[34rem] border-collapse text-sm">
        <caption className="sr-only">Прогноз остатка на счёте по дням</caption>
        <thead className="sticky top-0 bg-surface-2 text-left text-ink-2">
          <tr>
            <th className="px-3 py-2 font-medium">{copy.chart.date}</th>
            <th className="px-3 py-2 text-right font-medium">{copy.chart.expected}</th>
            <th className="px-3 py-2 text-right font-medium">{copy.chart.worse}</th>
            <th className="px-3 py-2 text-right font-medium">{copy.chart.better}</th>
            <th className="px-3 py-2 text-right font-medium">{copy.chart.chanceBelow}</th>
          </tr>
        </thead>
        <tbody>
          {forecast.daily.map((d) => (
            <tr key={d.date} className="border-t border-line">
              <th scope="row" className="px-3 py-1.5 text-left font-normal text-ink">
                {wdm(d.date)}
                {!d.working_day && <span className="ml-1.5 text-ink-2">выходной</span>}
              </th>
              <td className="tnum px-3 py-1.5 text-right font-medium">{rub(d.p50)}</td>
              <td className="tnum px-3 py-1.5 text-right">{rub(d.p10)}</td>
              <td className="tnum px-3 py-1.5 text-right">{rub(d.p90)}</td>
              <td className="tnum px-3 py-1.5 text-right">{pct(d.prob_below)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ForecastChartCard({
  forecast,
  horizon,
  onHorizon,
  busy,
}: {
  forecast: ForecastResponse
  horizon: Horizon
  onHorizon: (value: Horizon) => void
  busy: boolean
}) {
  const [table, setTable] = useState(false)
  const [picked, setPicked] = useState<{ date: string; events: ChartEvent[] } | null>(null)
  const model = useMemo(() => forecastModel(forecast), [forecast])
  const lowest = forecast.daily.reduce((a, b) => (b.p50 < a.p50 ? b : a), forecast.daily[0])
  const label = `${copy.home.chartTitle}. Сейчас ${rub(forecast.start_balance)}, самый низкий ожидаемый остаток ${rub(lowest.p50)} около ${dm(lowest.date)}.`

  return (
    <Card aria-labelledby="chart-title">
      <SectionTitle
        id="chart-title"
        hint={copy.hints.corridor}
        action={<Segmented label={copy.home.horizon} value={horizon} options={HORIZONS} onChange={onHorizon} />}
      >
        <span>
          {copy.home.chartTitle}
          <span className="block text-sm font-normal text-ink-2 sm:ml-2 sm:inline">{copy.home.chartSub(forecast.horizon_days)}</span>
        </span>
      </SectionTitle>

      {table ? (
        <ForecastTable forecast={forecast} />
      ) : (
        <>
          <Suspense fallback={<Spinner />}>
            <div className="-mx-1 h-[280px] sm:mx-0 sm:h-[340px]">
              <BalanceChart
                model={model}
                label={label}
                endLabel
                minMarker
                busy={busy}
                onPickMarker={(date, events) => setPicked({ date, events })}
              />
            </div>
          </Suspense>
          <ChartLegend model={model} />
        </>
      )}

      {picked && !table && (
        <div className="mt-4 rounded-xl border border-line bg-surface-2 p-3.5">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium text-ink-2">{wdm(picked.date)}</span>
            <Button size="sm" variant="ghost" onClick={() => setPicked(null)}>
              {copy.common.close}
            </Button>
          </div>
          <ul className="space-y-3">
            {picked.events.map((event) => (
              <li key={event.id}>
                <EventLine event={event} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 flex justify-end">
        <Button size="sm" variant="ghost" onClick={() => setTable((v) => !v)}>
          {table ? copy.common.chartView : copy.common.tableView}
        </Button>
      </div>
    </Card>
  )
}
