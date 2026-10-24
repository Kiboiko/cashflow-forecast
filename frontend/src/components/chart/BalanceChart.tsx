import { LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import { useEffect, useMemo, useRef } from 'react'

import { useChartTokens } from '../../lib/chartTheme'
import { cx } from '../ui'
import { groupByDate, type BalanceChartModel, type ChartEvent } from './model'
import { buildOption } from './option'

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, SVGRenderer])

interface ClickParams {
  seriesId?: string
  data?: { markerDate?: string }
}

/** Тонкая обёртка над ECharts: SVG-отрисовка, подгон размера под контейнер, клик по маркерам. */
function EChart({ option, onClick }: { option: EChartsCoreOption; onClick?: (params: ClickParams) => void }) {
  const host = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const element = host.current!
    const instance = echarts.init(element, undefined, { renderer: 'svg' })
    chart.current = instance
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(element)
    return () => {
      observer.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true })
  }, [option])

  useEffect(() => {
    const instance = chart.current
    if (!instance || !onClick) return
    instance.on('click', onClick as never)
    return () => {
      // Эффект инициализации чистится раньше и уже уничтожил график (StrictMode, размонтирование)
      if (!instance.isDisposed()) instance.off('click', onClick as never)
    }
  }, [onClick])

  return <div ref={host} style={{ height: '100%', width: '100%' }} />
}

interface Props {
  model: BalanceChartModel
  /** Текстовое описание для скринридеров; полные значения — в табличном виде под графиком. */
  label: string
  height?: number
  endLabel?: boolean
  minMarker?: boolean
  /** Данные перезагружаются: оставляем прежний график, но притушенным (без «скелетона»). */
  busy?: boolean
  onPickMarker?: (date: string, events: ChartEvent[]) => void
}

export default function BalanceChart({ model, label, height, endLabel = false, minMarker = false, busy = false, onPickMarker }: Props) {
  const tokens = useChartTokens()
  const option = useMemo(() => buildOption(model, tokens, { endLabel, minMarker }), [model, tokens, endLabel, minMarker])
  const onClick = useMemo(
    () =>
      onPickMarker
        ? (params: ClickParams) => {
            const date = params.data?.markerDate
            if (params.seriesId === 'events' && date) onPickMarker(date, groupByDate(model.events).get(date) ?? [])
          }
        : undefined,
    [model.events, onPickMarker],
  )

  return (
    <div role="img" aria-label={label} className={cx('transition-opacity duration-200', busy && 'opacity-60')} style={{ height: height ?? '100%' }}>
      <EChart option={option} onClick={onClick} />
    </div>
  )
}
