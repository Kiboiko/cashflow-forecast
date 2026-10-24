import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useAckAlert, useAlerts } from '../api/hooks'
import type { Alert } from '../api/types'
import { copy } from '../lib/copy'
import { dm } from '../lib/format'
import { IconAlertCircle, IconAlertTriangle, IconInfo } from './icons'
import { Button, Card, SectionTitle, cx } from './ui'

const SEVERITY = {
  critical: { Icon: IconAlertTriangle, box: 'bg-crit-soft', ink: 'text-crit-ink' },
  warning: { Icon: IconAlertCircle, box: 'bg-warn-soft', ink: 'text-warn-ink' },
  info: { Icon: IconInfo, box: 'bg-accent-soft', ink: 'text-accent-ink' },
} as const

function AlertRow({ alert, onAck, busy }: { alert: Alert; onAck: () => void; busy: boolean }) {
  const look = SEVERITY[alert.severity]
  const read = alert.acknowledged_at !== null
  return (
    <li className={cx('flex items-start gap-3 rounded-xl p-3', look.box, read && 'opacity-60')}>
      <look.Icon size={20} className={cx('mt-0.5 flex-none', look.ink)} />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-ink">{alert.title}</div>
        <p className="mt-0.5 text-sm text-ink-2">{alert.body}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-3">
          <span className="text-[13px] text-ink-2">{dm(alert.created_at.slice(0, 10))}</span>
          {!read && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onAck}>
              {copy.planFact.ack}
            </Button>
          )}
        </div>
      </div>
    </li>
  )
}

/** Центр предупреждений. `compact` — для Главной: только новые, не больше трёх, со ссылкой на полный список. */
export function AlertsCard({ bid, compact = false }: { bid: string; compact?: boolean }) {
  const [all, setAll] = useState(false)
  const alerts = useAlerts(bid, compact ? 'open' : all ? 'all' : 'open')
  const ack = useAckAlert(bid)
  const items = alerts.data ?? []
  if (compact && items.length === 0) return null
  const shown = compact ? items.slice(0, 3) : items

  return (
    <Card id="alerts" aria-labelledby="alerts-title">
      <SectionTitle
        id="alerts-title"
        action={
          compact ? (
            items.length > 3 ? (
              <Link to="/plan-fact#alerts" className="text-sm font-medium text-accent-ink underline underline-offset-2">
                Все ({items.length})
              </Link>
            ) : undefined
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setAll((v) => !v)}>
              {all ? copy.planFact.onlyOpen : copy.planFact.allAlerts}
            </Button>
          )
        }
      >
        {copy.planFact.alertsTitle}
      </SectionTitle>
      {shown.length === 0 ? (
        <p className="text-ink-2">{copy.planFact.alertsEmpty}</p>
      ) : (
        <ul className="space-y-2.5">
          {shown.map((alert) => (
            <AlertRow key={alert.id} alert={alert} busy={ack.isPending} onAck={() => ack.mutate(alert.id)} />
          ))}
        </ul>
      )}
    </Card>
  )
}
