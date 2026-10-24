import type { ForecastEvent } from '../../api/types'
import { copy } from '../../lib/copy'
import { rub } from '../../lib/format'

/** Один ряд графика: линия и (необязательно) коридор «хуже/лучше обычного». */
export interface ChartSeries {
  id: string
  name: string
  color: 'accent' | 'series2'
  values: Array<number | null>
  band?: { low: number[]; high: number[] }
}

export interface ChartEvent {
  id: string
  date: string
  direction: 'in' | 'out'
  amount: number
  title: string
  source: string
  probability: number
  mandatory: boolean
  explain: string
}

export interface TooltipExtra {
  label: string
  /** Значение по индексу даты; null — строку не показываем. */
  values: Array<string | null>
}

export interface BalanceChartModel {
  dates: string[]
  nonWorking: boolean[]
  series: ChartSeries[]
  threshold: number | null
  events: ChartEvent[]
  extras?: TooltipExtra[]
  /** Вертикальная линия с подписью — например, «Данные по 7 авг» на графике план-факта. */
  marker?: { date: string; label: string }
}

export interface AxisScale {
  min: number
  max: number
  step: number
}

/** «Красивая» шкала оси: границы и шаг кратны 1, 2, 2,5 или 5 × 10ⁿ — подписи получаются круглыми. */
export function niceAxis(min: number, max: number, ticks = 5): AxisScale {
  let lo = min
  let hi = max
  if (!(hi > lo)) {
    const pad = Math.max(Math.abs(hi) * 0.1, 1)
    lo -= pad
    hi += pad
  }
  const raw = (hi - lo) / ticks
  const pow = 10 ** Math.floor(Math.log10(raw))
  const f = raw / pow
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10
  const step = nice * pow
  return { min: Math.floor(lo / step) * step, max: Math.ceil(hi / step) * step, step }
}

/** Непрерывные отрезки нерабочих дней (для светлой подложки выходных). */
export function nonWorkingRuns(dates: string[], nonWorking: boolean[]): Array<[string, string]> {
  const runs: Array<[string, string]> = []
  let start = -1
  nonWorking.forEach((off, i) => {
    if (off && start < 0) start = i
    if ((!off || i === nonWorking.length - 1) && start >= 0) {
      runs.push([dates[start], dates[off ? i : i - 1]])
      start = -1
    }
  })
  return runs
}

/** События для маркеров: только датированные, в границах графика, самые крупные. */
export function pickChartEvents(events: ForecastEvent[], dates: string[], limit = 10): ChartEvent[] {
  const inRange = new Set(dates)
  return events
    .filter((e): e is ForecastEvent & { date: string } => !!e.date && inRange.has(e.date) && e.probability >= 0.5)
    .sort((a, b) => b.amount - a.amount)
    .slice(0, limit)
    .map((e) => ({
      id: e.id,
      date: e.date,
      direction: e.direction,
      amount: e.amount,
      title: e.title,
      source: e.source,
      probability: e.probability,
      mandatory: e.mandatory,
      explain: e.explain,
    }))
}

export function groupByDate(events: ChartEvent[]): Map<string, ChartEvent[]> {
  const grouped = new Map<string, ChartEvent[]>()
  for (const event of [...events].sort((a, b) => b.amount - a.amount)) {
    grouped.set(event.date, [...(grouped.get(event.date) ?? []), event])
  }
  return grouped
}

export interface TooltipRow {
  kind: 'line' | 'band' | 'text'
  color?: 'accent' | 'series2'
  label: string
  value: string
}

export interface TooltipContent {
  date: string
  nonWorking: boolean
  rows: TooltipRow[]
  events: Array<{ direction: 'in' | 'out'; label: string; value: string }>
}

/** Что показать в подсказке для дня `index`. Чистая функция: DOM строится отдельно. */
export function tooltipContent(model: BalanceChartModel, index: number): TooltipContent {
  const rows: TooltipRow[] = []
  for (const series of model.series) {
    const value = series.values[index]
    if (value == null) continue
    rows.push({ kind: 'line', color: series.color, label: series.name, value: rub(value) })
    if (series.band) {
      rows.push({ kind: 'band', color: series.color, label: copy.chart.worse, value: rub(series.band.low[index]) })
      rows.push({ kind: 'band', color: series.color, label: copy.chart.better, value: rub(series.band.high[index]) })
    }
  }
  for (const extra of model.extras ?? []) {
    const value = extra.values[index]
    if (value != null) rows.push({ kind: 'text', label: extra.label, value })
  }
  const date = model.dates[index]
  const events = (groupByDate(model.events).get(date) ?? []).slice(0, 5).map((e) => ({
    direction: e.direction,
    label: e.title,
    value: `${e.direction === 'out' ? '−' : '+'}${rub(e.amount)}`,
  }))
  return { date, nonWorking: model.nonWorking[index] ?? false, rows, events }
}
