import { useMemo } from 'react'

import { useChartTokens } from '../../lib/chartTheme'
import { cx } from '../ui'
import { EChart, type ClickParams } from './EChart'
import { groupByDate, type BalanceChartModel, type ChartEvent } from './model'
import { buildOption } from './option'

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
