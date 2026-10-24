import type { EChartsCoreOption } from 'echarts/core'

import { copy } from '../../lib/copy'
import type { ChartTokens } from '../../lib/chartTheme'
import { dm, rubAxis } from '../../lib/format'
import { groupByDate, niceAxis, nonWorkingRuns, tooltipContent, type BalanceChartModel } from './model'
import { tooltipElement } from './tooltip'

export interface OptionFlags {
  /** Подпись последнего значения справа от линии. */
  endLabel: boolean
  /** Отметить самый низкий остаток первого ряда. */
  minMarker: boolean
}

/** Опции ECharts для графика остатка: коридор, линии, порог с красной зоной, выходные и маркеры платежей. */
export function buildOption(model: BalanceChartModel, t: ChartTokens, flags: OptionFlags): EChartsCoreOption {
  const paint = {
    accent: { line: t.accent, wash: t.accentWash },
    series2: { line: t.series2, wash: t.series2Wash },
  }

  const extent: number[] = []
  for (const s of model.series) {
    for (const v of s.values) if (v != null) extent.push(v)
    if (s.band) extent.push(...s.band.low, ...s.band.high)
  }
  if (model.threshold != null) extent.push(model.threshold)
  const axis = niceAxis(Math.min(...extent), Math.max(...extent))

  // Коридор: две стековые серии (нижняя граница невидима, разность закрашена). 'all' — иначе отрицательные
  // значения не складываются с положительными.
  const bands = model.series.flatMap((s) =>
    s.band
      ? [
          {
            type: 'line', id: `${s.id}-low`, stack: `${s.id}-band`, stackStrategy: 'all', data: s.band.low,
            symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, tooltip: { show: false }, z: 1,
          },
          {
            type: 'line', id: `${s.id}-span`, stack: `${s.id}-band`, stackStrategy: 'all',
            data: s.band.high.map((high, i) => high - s.band!.low[i]),
            symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: paint[s.color].wash }, tooltip: { show: false }, z: 1,
          },
        ]
      : [],
  )

  const weekendAreas = nonWorkingRuns(model.dates, model.nonWorking).map(([from, to]) => [
    { xAxis: from, itemStyle: { color: t.weekendWash } },
    { xAxis: to },
  ])
  const zoneArea =
    model.threshold != null ? [[{ yAxis: axis.min, itemStyle: { color: t.critWash } }, { yAxis: model.threshold }]] : []

  const first = model.series[0]
  const firstValues = first?.values ?? []
  const lowest = Math.min(...firstValues.filter((v): v is number => v != null))
  const lowestIndex = firstValues.indexOf(lowest)
  const lastValue = firstValues[firstValues.length - 1]
  // Минимум в конце ряда (или равный последнему значению) уже подписан справа — второй раз не отмечаем.
  const showMin =
    flags.minMarker && lowestIndex > 0 && lowestIndex < firstValues.length - 1 && !(lastValue != null && Math.abs(lowest - lastValue) < 1)

  // Горизонтальная линия порога и (необязательно) вертикальная отметка «данные по…»
  const lineMarks: object[] = []
  if (model.threshold != null) {
    lineMarks.push({
      yAxis: model.threshold,
      lineStyle: { color: t.crit, width: 1, type: 'solid' },
      label: {
        show: true, position: 'insideStartTop', color: t.ink2, fontSize: 11,
        formatter: () => `${copy.chart.threshold}: ${rubAxis(model.threshold!)}`,
      },
    })
  }
  if (model.marker) {
    lineMarks.push({
      xAxis: model.marker.date,
      lineStyle: { color: t.ink3, width: 1, type: 'solid' },
      label: { show: true, position: 'insideEndTop', color: t.ink2, fontSize: 11, formatter: () => model.marker!.label },
    })
  }

  const lines = model.series.map((s, k) => ({
    type: 'line',
    id: s.id,
    name: s.name,
    data: s.values,
    symbol: 'circle',
    symbolSize: 8,
    showSymbol: false,
    connectNulls: false,
    lineStyle: { width: 2, color: paint[s.color].line, cap: 'round', join: 'round' },
    itemStyle: { color: paint[s.color].line, borderColor: t.surface, borderWidth: 2 },
    emphasis: { disabled: false, scale: false },
    z: 3 + k,
    endLabel:
      flags.endLabel && k === 0
        ? { show: true, distance: 6, fontSize: 12, color: t.ink2, formatter: (p: { value: number }) => rubAxis(p.value) }
        : undefined,
    markArea: k === 0 ? { silent: true, data: [...weekendAreas, ...zoneArea], z: 0 } : undefined,
    markLine: k === 0 && lineMarks.length ? { silent: true, symbol: 'none', animation: false, data: lineMarks } : undefined,
    markPoint:
      k === 0 && showMin
        ? {
            silent: true, symbol: 'circle', symbolSize: 10,
            itemStyle: { color: paint[s.color].line, borderColor: t.surface, borderWidth: 2 },
            label: { show: true, position: 'bottom', distance: 6, color: t.ink2, fontSize: 11, formatter: (p: { value: number }) => rubAxis(p.value) },
            data: [{ type: 'min' }],
          }
        : undefined,
  }))

  const grouped = groupByDate(model.events)
  const markers = [...grouped.entries()].flatMap(([date, items]) => {
    const i = model.dates.indexOf(date)
    const y = first?.values[i]
    if (i < 0 || y == null) return []
    const biggest = items[0]
    return [{
      value: [date, y],
      symbol: 'triangle',
      symbolRotate: biggest.direction === 'out' ? 180 : 0,
      markerDate: date,
    }]
  })
  const scatter = {
    type: 'scatter', id: 'events', name: 'events', data: markers, symbolSize: 12, z: 6,
    itemStyle: { color: t.ink, borderColor: t.surface, borderWidth: 2 },
    emphasis: { scale: 1.25 },
    tooltip: { show: false },
  }

  return {
    animationDuration: 300,
    animationDurationUpdate: 200,
    textStyle: { fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
    grid: { left: 4, right: flags.endLabel ? 56 : 12, top: 12, bottom: 2, containLabel: true },
    xAxis: {
      type: 'category', data: model.dates, boundaryGap: true,
      axisLine: { lineStyle: { color: t.line2 } }, axisTick: { show: false },
      axisLabel: { color: t.ink3, fontSize: 11, hideOverlap: true, formatter: (value: string) => dm(value) },
    },
    yAxis: {
      type: 'value', min: axis.min, max: axis.max, interval: axis.step,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: t.ink3, fontSize: 11, formatter: (value: number) => rubAxis(value) },
      splitLine: { lineStyle: { color: t.line, width: 1, type: 'solid' } },
    },
    tooltip: {
      trigger: 'axis', confine: true, padding: 0, borderWidth: 1, borderColor: t.line, backgroundColor: t.surface,
      extraCssText: 'box-shadow: 0 8px 24px rgba(0,0,0,.14); border-radius: 12px;',
      axisPointer: { type: 'line', lineStyle: { color: t.line2, width: 1, type: 'solid' } },
      formatter: (params: Array<{ dataIndex: number }> | { dataIndex: number }) => {
        const first = Array.isArray(params) ? params[0] : params
        return first ? tooltipElement(tooltipContent(model, first.dataIndex)) : ''
      },
    },
    series: [...bands, ...lines, scatter],
  }
}
