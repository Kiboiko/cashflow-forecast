import { useMemo } from 'react'

import type { ReliabilityPoint } from '../../lib/backtest'
import { useChartTokens } from '../../lib/chartTheme'
import { pct } from '../../lib/format'
import { EChart } from './EChart'
import { textTooltip } from './tooltip'

/** «Если мы говорим 30% — так и бывает»: назвали шанс (по горизонтали) → как часто нехватка случалась (по вертикали). */
export default function ReliabilityChart({ points, label }: { points: ReliabilityPoint[]; label: string }) {
  const t = useChartTokens()
  const option = useMemo(
    () => ({
      animationDuration: 300,
      textStyle: { fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
      grid: { left: 4, right: 18, top: 12, bottom: 4, containLabel: true },
      xAxis: {
        type: 'value', min: 0, max: 1, interval: 0.2, axisTick: { show: false }, axisLine: { lineStyle: { color: t.line2 } },
        axisLabel: { color: t.ink3, fontSize: 11, formatter: (v: number) => pct(v) }, splitLine: { lineStyle: { color: t.line, width: 1, type: 'solid' } },
      },
      yAxis: {
        type: 'value', min: 0, max: 1, interval: 0.2, axisTick: { show: false }, axisLine: { show: false },
        axisLabel: { color: t.ink3, fontSize: 11, formatter: (v: number) => pct(v) }, splitLine: { lineStyle: { color: t.line, width: 1, type: 'solid' } },
      },
      tooltip: {
        trigger: 'item', confine: true, padding: 0, borderWidth: 1, borderColor: t.line, backgroundColor: t.surface,
        extraCssText: 'box-shadow: 0 8px 24px rgba(0,0,0,.14); border-radius: 12px;',
        formatter: (p: { seriesId?: string; data?: number[] }) => {
          if (p.seriesId !== 'ours' || !p.data) return ''
          const [predicted, observed, n] = p.data
          return textTooltip(`Мы говорили: ${pct(predicted)}`, [`Бывало: ${pct(observed)}`, `Случаев: ${n}`])
        },
      },
      series: [
        { id: 'ideal', type: 'line', data: [[0, 0], [1, 1]], symbol: 'none', silent: true, tooltip: { show: false }, lineStyle: { color: t.ink3, width: 1.5 }, z: 1 },
        {
          id: 'ours', type: 'line', data: points.map((p) => [p.predicted, p.observed, p.n]), symbol: 'circle', symbolSize: 9, z: 3,
          lineStyle: { color: t.accent, width: 2, cap: 'round', join: 'round' }, itemStyle: { color: t.accent, borderColor: t.surface, borderWidth: 2 },
        },
      ],
    }),
    [points, t],
  )
  return (
    <div role="img" aria-label={label} className="h-[260px] sm:h-[300px]">
      <EChart option={option} />
    </div>
  )
}
