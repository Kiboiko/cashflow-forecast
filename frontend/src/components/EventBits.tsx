import { copy } from '../lib/copy'
import { confidenceWord, dm, rub } from '../lib/format'
import { IconArrowDownRight, IconArrowUpRight } from './icons'
import { Badge, type Tone } from './ui'

const SOURCE_TONE: Record<string, Tone> = {
  recurring: 'neutral',
  tax_calendar: 'neutral',
  rates: 'neutral',
  revenue: 'neutral',
  manual: 'accent',
  csv: 'accent',
  '1c': 'accent',
  invoice: 'accent',
  contract: 'accent',
  contract_detected: 'accent',
  demo_accounting: 'accent',
  bank_mock: 'accent',
}

/** Откуда мы узнали о событии (требование ★4: источник виден у каждого события). */
export function EventSourceBadge({ source }: { source: string }) {
  return <Badge tone={SOURCE_TONE[source] ?? 'neutral'}>{copy.sources[source] ?? source}</Badge>
}

export function ConfidenceBadge({ probability }: { probability: number }) {
  const word = confidenceWord(probability)
  return <Badge tone={word === 'точно' ? 'good' : word === 'вероятно' ? 'warn' : 'neutral'}>{word}</Badge>
}

export interface EventLike {
  id: string
  date?: string | null
  date_range?: [string, string] | null
  direction: 'in' | 'out'
  amount: number
  title: string
  source: string
  probability: number
  mandatory: boolean
  explain: string
}

export function eventWhen(event: Pick<EventLike, 'date' | 'date_range'>): string {
  if (event.date_range) {
    const [from, to] = event.date_range
    return from === to ? dm(from) : `${dm(from)} – ${dm(to)}`
  }
  return event.date ? dm(event.date) : '—'
}

/** Строка события: направление, название, когда и сколько, источник и уверенность. */
export function EventLine({ event }: { event: EventLike }) {
  const out = event.direction === 'out'
  return (
    <div className="flex items-start gap-3">
      <span
        className={`mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-full ${out ? 'bg-surface-2 text-ink-2' : 'bg-good-soft text-good-ink'}`}
        title={out ? 'Нужно заплатить' : 'Придут деньги'}
      >
        {out ? <IconArrowDownRight size={18} /> : <IconArrowUpRight size={18} />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="font-medium text-ink">{event.title}</span>
          <span className="tnum font-semibold text-ink">
            {out ? '−' : '+'}
            {rub(event.amount)}
          </span>
        </div>
        <div className="mt-0.5 text-sm text-ink-2">
          {eventWhen(event)}
          {event.mandatory && ' · нельзя пропустить'}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <EventSourceBadge source={event.source} />
          <ConfidenceBadge probability={event.probability} />
        </div>
        {event.explain && <p className="mt-1.5 text-sm text-ink-2">{event.explain}</p>}
      </div>
    </div>
  )
}
