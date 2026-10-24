import { useState } from 'react'

import { ApiError } from '../api/client'
import { useImportEvents, useReceivables } from '../api/hooks'
import type { Direction, ReceivableRow } from '../api/types'
import { copy } from '../lib/copy'
import { dm, plural, rub } from '../lib/format'
import { FileButton } from './forms'
import { IconAlertCircle, IconChevronDown, IconUpload } from './icons'
import { Badge, Card, EmptyState, ErrorBlock, Spinner, Stat, cx } from './ui'

function ClientCard({ row, direction }: { row: ReceivableRow; direction: Direction }) {
  const [open, setOpen] = useState(false)
  const delay = direction === 'in' ? copy.payments.typicalDelay : copy.payments.typicalDelayOut
  return (
    <li className="rounded-2xl border border-line bg-surface">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-start gap-3 p-4 text-left">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="font-medium text-ink">{row.counterparty ?? row.counterparty_inn ?? 'Без названия'}</span>
            <span className="tnum text-lg font-semibold text-ink">{rub(row.open_amount)}</span>
          </div>
          <div className="mt-0.5 text-sm text-ink-2">
            {delay(row.typical_delay_days, row.delay_spread_days)} · {row.invoices.length} {plural(row.invoices.length, ['счёт', 'счёта', 'счетов'])}
          </div>
          {row.overdue_amount > 0 && (
            <Badge tone="warn" className="mt-2">
              <IconAlertCircle size={14} />
              {copy.payments.overdue}: {rub(row.overdue_amount)}
            </Badge>
          )}
        </div>
        <IconChevronDown size={20} className={cx('mt-1 flex-none text-ink-2 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <ul className="divide-y divide-line border-t border-line px-4">
          {row.invoices.map((invoice) => (
            <li key={`${invoice.doc_no}-${invoice.doc_date}`} className="py-3 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium text-ink">
                  № {invoice.doc_no} от {dm(invoice.doc_date)}
                </span>
                <span className="tnum font-semibold">{rub(invoice.amount)}</span>
              </div>
              <div className="mt-0.5 text-ink-2">
                {invoice.age_days} {plural(invoice.age_days, ['день', 'дня', 'дней'])} с даты счёта ·{' '}
                {copy.payments.expectedWindow(dm(invoice.expected_from), dm(invoice.expected_to))}
              </div>
              {invoice.overdue_by_client_standard && (
                <Badge tone="warn" className="mt-1.5">
                  {copy.payments.overdue}
                </Badge>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

/** «Вам должны» (in) и «Вы должны» (out): открытые счета по контрагентам, обычная задержка и просрочка. */
export function ReceivablesPanel({ bid, direction }: { bid: string; direction: Direction }) {
  const data = useReceivables(bid, direction)
  const upload = useImportEvents(bid)
  const [message, setMessage] = useState<string | null>(null)
  const error = upload.error instanceof ApiError ? upload.error.message : null

  const picker = (
    <FileButton
      label={copy.payments.importInvoices}
      accept=".csv,text/csv,.txt"
      disabled={upload.isPending}
      icon={<IconUpload size={18} />}
      onFile={(file) => upload.mutate(file, { onSuccess: (r) => setMessage(copy.payments.imported(r.added, r.skipped)) })}
    />
  )

  if (data.isPending) return <Spinner />
  if (data.isError) return <ErrorBlock onRetry={() => data.refetch()} />
  const info = data.data

  return (
    <div className="space-y-4">
      {message && (
        <p role="status" className="rounded-xl bg-good-soft px-3.5 py-2.5 text-sm text-good-ink">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-xl bg-crit-soft px-3.5 py-2.5 text-sm text-crit-ink">
          {error}
        </p>
      )}
      {!info.connected ? (
        <EmptyState title={copy.payments.notConnected} text={copy.payments.notConnectedHint} action={picker} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Stat label={direction === 'in' ? copy.payments.owedTotal : copy.payments.oweTotal} value={rub(info.total)} />
            <Stat label="Из них просрочено" value={rub(info.overdue_total)} tone={info.overdue_total > 0 ? 'crit' : 'neutral'} sub={copy.payments.overdue} />
          </div>
          {info.source_label && (
            <p className="text-sm text-ink-2">
              Источник: <span className="font-medium text-ink">{info.source_label}</span>
            </p>
          )}
          {info.rows.length === 0 ? (
            <EmptyState title={direction === 'in' ? 'Все счета оплачены' : 'Все счета поставщиков оплачены'} />
          ) : (
            <ul className="space-y-3">
              {info.rows.map((row) => (
                <ClientCard key={row.counterparty_inn ?? row.counterparty ?? row.open_amount} row={row} direction={direction} />
              ))}
            </ul>
          )}
          <Card className="space-y-3">
            <p className="text-sm text-ink-2">Есть свой файл со счетами? Он заменит демо-данные и сразу попадёт в прогноз.</p>
            {picker}
            <p className="text-[13px] text-ink-2">{copy.payments.csvHint}</p>
          </Card>
        </>
      )}
    </div>
  )
}
