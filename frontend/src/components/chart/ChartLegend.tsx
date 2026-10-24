import { copy } from '../../lib/copy'
import type { BalanceChartModel } from './model'

const swatch = (color: 'accent' | 'series2') => (color === 'accent' ? 'var(--accent)' : 'var(--series-2)')

/** Легенда: ключ повторяет метку на графике (линия — линия, коридор — полоса, платёж — треугольник). */
export function ChartLegend({ model }: { model: BalanceChartModel }) {
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-ink-2">
      {model.series.map((s) => (
        <li key={s.id} className="flex items-center gap-1.5">
          <span className="h-[3px] w-4 rounded-sm" style={{ background: swatch(s.color) }} />
          {s.name}
        </li>
      ))}
      {model.series.some((s) => s.band) && (
        <li className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm" style={{ background: 'var(--accent)', opacity: 0.22 }} />
          {copy.chart.corridor}
        </li>
      )}
      {model.threshold != null && (
        <li className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm border-t border-crit bg-crit/10" />
          {copy.chart.thresholdZone}
        </li>
      )}
      {model.events.length > 0 && (
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="text-[10px] leading-none text-ink">▼▲</span>
          {copy.chart.payment} / {copy.chart.income.toLowerCase()}
        </li>
      )}
    </ul>
  )
}
