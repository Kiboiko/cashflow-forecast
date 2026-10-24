import { useState } from 'react'
import { Link } from 'react-router-dom'

import type { DataQuality, Driver, ForecastResponse, MandatoryCoverage, Recommendation, Risk } from '../api/types'
import { copy } from '../lib/copy'
import { chanceText, dm, dmyLong, pct, plural, rub, rubApprox } from '../lib/format'
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconArrowDownRight,
  IconCheckCircle,
  IconInfo,
  IconSparkle,
} from './icons'
import { Badge, Button, Card, SectionTitle, Stat, cx } from './ui'

/* ---------- светофор и главная фраза ---------- */

const LEVEL = {
  green: { Icon: IconCheckCircle, box: 'border-good/40 bg-good-soft', chip: 'bg-good text-white', ink: 'text-good-ink' },
  yellow: { Icon: IconAlertCircle, box: 'border-warn/60 bg-warn-soft', chip: 'bg-warn text-[#0b0b0b]', ink: 'text-warn-ink' },
  red: { Icon: IconAlertTriangle, box: 'border-crit/40 bg-crit-soft', chip: 'bg-crit text-white', ink: 'text-crit-ink' },
} as const

export function RiskCard({ risk, dataAsOf, quality }: { risk: Risk; dataAsOf: string; quality: DataQuality }) {
  const level = LEVEL[risk.level]
  const notes: string[] = []
  if (quality.inactive) notes.push(copy.home.inactive)
  else if (quality.history !== 'full') notes.push(copy.home.roughForecast)

  return (
    <section aria-label="Главный вывод" className={cx('rounded-2xl border p-4 sm:p-6', level.box)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        {/* Светофор: цвет + значок + слова — смысл не держится на одном цвете */}
        <span className={cx('inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold', level.chip)}>
          <level.Icon size={18} />
          {copy.levels[risk.level].label}
        </span>
        <span className="text-sm text-ink-2">{copy.common.dataAsOf(dmyLong(dataAsOf))}</span>
      </div>
      <p className="mt-3 text-xl font-semibold leading-snug text-ink sm:text-2xl">{risk.headline}</p>
      {notes.map((note) => (
        <p key={note} className="mt-2 flex items-start gap-2 text-sm text-ink-2">
          <IconInfo size={16} className="mt-0.5 flex-none" />
          {note}
        </p>
      ))}
    </section>
  )
}

/* ---------- три числа ---------- */

export function SummaryStats({ forecast }: { forecast: ForecastResponse }) {
  const { risk, daily, start_balance: start, threshold } = forecast
  const lowest = daily.reduce((a, b) => (b.p50 < a.p50 ? b : a), daily[0])
  const noGap = risk.level === 'green' && risk.shortfall_p90 <= 0
  const shortfall = risk.shortfall_p50 > 0 ? risk.shortfall_p50 : risk.shortfall_p90

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <Stat label={copy.home.now} value={rub(start)} sub={dmyLong(forecast.as_of)} />
      <Stat
        label={copy.home.lowest}
        value={rub(lowest.p50)}
        sub={copy.home.lowestOn(dm(lowest.date))}
        tone={lowest.p50 < threshold ? 'crit' : 'neutral'}
      />
      <div className="col-span-2 sm:col-span-1">
        {noGap ? (
          <Stat label={copy.home.shortfall} value={copy.home.noShortfall} sub={chanceText(risk.gap_probability)} tone="good" />
        ) : (
          <Stat
            label={copy.home.shortfall}
            value={risk.shortfall_p50 > 0 ? rubApprox(shortfall) : `до ${rubApprox(shortfall).replace('около ', '')}`}
            sub={`${chanceText(risk.gap_probability)}${risk.shortfall_p50 > 0 ? '' : ', если всё пойдёт хуже обычного'}`}
            tone={risk.level === 'red' ? 'crit' : 'neutral'}
          />
        )}
      </div>
    </div>
  )
}

/* ---------- платежи, которые нельзя пропустить ---------- */

function coverageTone(p: number): { fill: string; track: string; ink: string; Icon: typeof IconCheckCircle; word: string } {
  if (p >= 0.9) return { fill: 'bg-good', track: 'bg-good-soft', ink: 'text-good-ink', Icon: IconCheckCircle, word: 'хватит' }
  if (p >= 0.5) return { fill: 'bg-warn', track: 'bg-warn-soft', ink: 'text-warn-ink', Icon: IconAlertCircle, word: 'риск' }
  return { fill: 'bg-crit', track: 'bg-crit-soft', ink: 'text-crit-ink', Icon: IconAlertTriangle, word: 'не хватит' }
}

export function ObligationsList({ items }: { items: MandatoryCoverage[] }) {
  return (
    <Card aria-labelledby="obligations-title">
      <SectionTitle id="obligations-title">{copy.home.obligations}</SectionTitle>
      {items.length === 0 ? (
        <p className="text-ink-2">{copy.home.obligationsEmpty}</p>
      ) : (
        <ul className="divide-y divide-line">
          {[...items]
            .sort((a, b) => a.date.localeCompare(b.date))
            .map((item) => {
              const tone = coverageTone(item.covered_probability)
              return (
                <li key={item.event_id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-medium text-ink">{item.title}</span>
                    <span className="tnum whitespace-nowrap font-semibold">{rub(item.amount)}</span>
                  </div>
                  <div className="mt-0.5 text-sm text-ink-2">до {dm(item.date)}</div>
                  <div className="mt-2 flex items-center gap-3">
                    <div className={cx('h-2 flex-1 overflow-hidden rounded-full', tone.track)} aria-hidden="true">
                      <div className={cx('h-full rounded-full', tone.fill)} style={{ width: `${Math.max(item.covered_probability * 100, 3)}%` }} />
                    </div>
                    <span className={cx('flex items-center gap-1 whitespace-nowrap text-sm font-medium', tone.ink)}>
                      <tone.Icon size={16} />
                      {tone.word}
                    </span>
                  </div>
                  <div className="mt-1 text-[13px] text-ink-2">{copy.home.coverage(pct(item.covered_probability))}</div>
                </li>
              )
            })}
        </ul>
      )}
    </Card>
  )
}

/* ---------- почему ---------- */

function driverText(d: Driver): string {
  if (d.kind === 'outflow' && d.title && d.amount != null) return `${d.title}${d.date ? ` ${dm(d.date)}` : ''} — ${rub(d.amount)}`
  if (d.kind === 'late_inflow' && d.title && d.amount != null)
    return `Ждём ${rub(d.amount)}: ${d.title}${d.date ? `, ожидаем ${dm(d.date)}` : ''}${d.text ? `. ${d.text}` : ''}`
  return d.text ?? d.title ?? ''
}

export function DriversList({ drivers }: { drivers: Driver[] }) {
  const shown = drivers.map(driverText).filter(Boolean)
  return (
    <Card aria-labelledby="why-title">
      <SectionTitle id="why-title">{copy.home.why}</SectionTitle>
      {shown.length === 0 ? (
        <p className="text-ink-2">{copy.home.whyEmpty}</p>
      ) : (
        <ul className="space-y-2.5">
          {drivers.map((d, i) => {
            const text = driverText(d)
            if (!text) return null
            return (
              <li key={i} className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-full bg-surface-2 text-ink-2">
                  {d.kind === 'outflow' ? <IconArrowDownRight size={14} /> : <IconInfo size={14} />}
                </span>
                <span className="text-ink">{text}</span>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

/* ---------- что сделать ---------- */

const REC_BADGE: Record<Recommendation['type'], { label: string; tone: 'good' | 'accent' | 'warn' | 'neutral' }> = {
  collect_receivable: { label: 'Попросить клиента', tone: 'good' },
  defer_supplier: { label: 'Договориться с поставщиком', tone: 'good' },
  move_own_funds: { label: 'Свои деньги', tone: 'accent' },
  postpone_withdrawal: { label: 'Отложить вывод', tone: 'accent' },
  credit_line: { label: 'Крайний вариант', tone: 'warn' },
}

function RecommendationCard({ rec, before }: { rec: Recommendation; before: number }) {
  const [open, setOpen] = useState(false)
  const badge = REC_BADGE[rec.type]
  const after = rec.effect.gap_probability_after
  return (
    <li className="rounded-xl border border-line p-3.5">
      <div className="flex items-start gap-3">
        <span className="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-accent text-sm font-semibold text-white" aria-label={`Вариант ${rec.priority}`}>
          {rec.priority}
        </span>
        <div className="min-w-0 flex-1">
          <Badge tone={badge.tone}>{badge.label}</Badge>
          <p className="mt-1.5 font-medium leading-snug text-ink">{rec.title}</p>
          {rec.deadline && <p className="mt-0.5 text-sm text-ink-2">Лучше до {dm(rec.deadline)}</p>}
          <Button size="sm" variant="secondary" className="mt-2.5" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? copy.home.effectHide : copy.home.effectButton}
          </Button>
          {open && (
            <div className="mt-3 rounded-lg bg-surface-2 p-3 text-sm">
              <p className="font-medium text-ink">
                {after < 0.01 ? copy.home.effectNoGap : copy.home.effect(pct(before), pct(after))}
              </p>
              <div className="mt-2 space-y-1.5" aria-hidden="true">
                <div className="flex items-center gap-2">
                  <span className="w-14 text-ink-2">Сейчас</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-crit-soft">
                    <div className="h-full rounded-full bg-crit" style={{ width: `${Math.max(before * 100, 2)}%` }} />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-14 text-ink-2">После</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-good-soft">
                    <div className="h-full rounded-full bg-good" style={{ width: `${Math.max(after * 100, 2)}%` }} />
                  </div>
                </div>
              </div>
              {rec.effect.shortfall_p50_after > 0 ? (
                <p className="mt-2 text-ink-2">Ожидаемая нехватка: {rub(rec.effect.shortfall_p50_after)}</p>
              ) : (
                <p className="mt-2 text-ink-2">Ожидаемой нехватки не останется.</p>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

export function Recommendations({ items, risk }: { items: Recommendation[]; risk: Risk }) {
  return (
    <Card aria-labelledby="todo-title">
      <SectionTitle id="todo-title">{copy.home.todo}</SectionTitle>
      {items.length === 0 ? (
        <p className="text-ink-2">{copy.home.todoEmpty}</p>
      ) : (
        <ul className="space-y-3">
          {[...items]
            .sort((a, b) => a.priority - b.priority)
            .map((rec) => (
              <RecommendationCard key={rec.id} rec={rec} before={risk.gap_probability} />
            ))}
        </ul>
      )}
    </Card>
  )
}

/* ---------- прочее ---------- */

export function Insights({ items }: { items: ForecastResponse['insights'] }) {
  if (items.length === 0) return null
  return (
    <Card aria-labelledby="insights-title">
      <SectionTitle id="insights-title">{copy.home.insights}</SectionTitle>
      <ul className="space-y-2">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-2.5 text-ink">
            <IconSparkle size={18} className="mt-0.5 flex-none text-accent" />
            {item.text}
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function QualityNotice({ quality }: { quality: DataQuality }) {
  const items: Array<{ key: string; text: string; to?: string }> = []
  if (quality.unknown_ops > 0) {
    items.push({
      key: 'unknown',
      text: `Не распознано ${quality.unknown_ops} ${plural(quality.unknown_ops, ['операция', 'операции', 'операций'])} — уточните, и прогноз станет точнее.`,
      to: '/operations?review=1',
    })
  }
  if (quality.gaps.length > 0) {
    items.push({ key: 'gaps', text: `В выписке есть пропуски данных (${quality.gaps.length}): на эти даты прогноз опирается осторожнее.` })
  }
  if (items.length === 0) return null
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.key} className="flex items-start gap-2.5 rounded-xl bg-surface-2 px-3.5 py-2.5 text-sm text-ink-2">
          <IconInfo size={18} className="mt-0.5 flex-none" />
          <span>
            {item.text}{' '}
            {item.to && (
              <Link to={item.to} className="font-medium text-accent-ink underline underline-offset-2">
                Открыть
              </Link>
            )}
          </span>
        </li>
      ))}
    </ul>
  )
}
