import type { ForecastResponse } from '../../api/types'
import { copy } from '../../lib/copy'
import { pct } from '../../lib/format'
import { pickChartEvents, type BalanceChartModel } from './model'

/** Ответ прогноза → данные для графика остатка (одна линия, коридор, маркеры платежей). */
export function forecastModel(forecast: ForecastResponse): BalanceChartModel {
  const dates = forecast.daily.map((d) => d.date)
  return {
    dates,
    nonWorking: forecast.daily.map((d) => !d.working_day),
    series: [
      {
        id: 'p50',
        name: copy.chart.expected,
        color: 'accent',
        values: forecast.daily.map((d) => d.p50),
        band: { low: forecast.daily.map((d) => d.p10), high: forecast.daily.map((d) => d.p90) },
      },
    ],
    threshold: forecast.threshold,
    events: pickChartEvents(forecast.events, dates),
    extras: [
      { label: copy.chart.chanceBelow, values: forecast.daily.map((d) => (d.prob_below >= 0.01 ? pct(d.prob_below) : null)) },
    ],
  }
}
