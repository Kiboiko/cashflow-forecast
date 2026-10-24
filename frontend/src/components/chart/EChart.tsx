import { LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, SVGRenderer])

export interface ClickParams {
  seriesId?: string
  data?: { markerDate?: string }
}

/** Тонкая обёртка над ECharts: SVG-отрисовка, подгон размера под контейнер, клик по маркерам. */
export function EChart({ option, onClick }: { option: EChartsCoreOption; onClick?: (params: ClickParams) => void }) {
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
