import { useState } from 'react'

import { useForecastTrace } from '../api/hooks'
import { copy } from '../lib/copy'
import { Card, ErrorBlock, Spinner } from './ui'

/** «Как посчитан этот прогноз»: пошаговый журнал расчёта (тот же прогноз, те же числа), грузится по раскрытию. */
export function TraceCard({ bid, horizon }: { bid: string; horizon: number }) {
  const [open, setOpen] = useState(false)
  const trace = useForecastTrace(bid, horizon, open)
  return (
    <Card>
      <details onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer font-semibold text-ink">{copy.home.traceTitle}</summary>
        <p className="mt-2 text-sm text-ink-2">{copy.home.traceLead}</p>
        {open &&
          (trace.isPending ? (
            <Spinner />
          ) : trace.isError ? (
            <ErrorBlock message={trace.error.message} onRetry={() => trace.refetch()} />
          ) : (
            <ol className="mt-3 space-y-2 text-sm">
              {trace.data.steps.map((step) => (
                <li key={step} className="rounded-xl bg-surface-2 px-3 py-2 text-ink">
                  {step}
                </li>
              ))}
            </ol>
          ))}
      </details>
    </Card>
  )
}
