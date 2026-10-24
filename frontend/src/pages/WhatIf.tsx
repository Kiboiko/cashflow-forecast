import { lazy, Suspense, useEffect, useMemo, useState } from 'react'

import { useForecast, useScenario, useScenarioPresets } from '../api/hooks'
import type { RiskLevel, ScenarioPreset, ScenarioRequest, ScenarioResponse } from '../api/types'
import { useCurrentBusiness } from '../auth'
import { ChartLegend } from '../components/chart/ChartLegend'
import type { BalanceChartModel } from '../components/chart/model'
import { IconAlertCircle, IconAlertTriangle, IconCheckCircle } from '../components/icons'
import { Card, ErrorBlock, SectionTitle, Spinner, cx } from '../components/ui'
import { copy } from '../lib/copy'
import { chanceWords, pct, rub } from '../lib/format'
import { useDebounced } from '../lib/hooks'

const BalanceChart = lazy(() => import('../components/chart/BalanceChart'))

interface Control {
  key: 'delay_days' | 'pct' | 'amount'
  min: number
  max: number
  step: number
  format: (value: number) => string
}

const CONTROLS: Record<string, Control> = {
  client_delay: { key: 'delay_days', min: 7, max: 60, step: 7, format: (v) => `${v} дн.` },
  revenue_drop: { key: 'pct', min: 5, max: 50, step: 5, format: (v) => `${v}%` },
  one_off_expense: { key: 'amount', min: 20_000, max: 1_000_000, step: 10_000, format: (v) => rub(v) },
  fixed_cost_increase: { key: 'pct', min: 5, max: 30, step: 5, format: (v) => `${v}%` },
}

type Shock = Record<string, unknown>

function firstShock(request: ScenarioRequest): Shock {
  return request.shocks[0] as unknown as Shock
}

function withValue(preset: ScenarioPreset, value: number): ScenarioRequest {
  const request = structuredClone(preset.request)
  firstShock(request)[CONTROLS[preset.id].key] = value
  return request
}

const LEVEL_ICON = { green: IconCheckCircle, yellow: IconAlertCircle, red: IconAlertTriangle } as const
const LEVEL_TONE: Record<RiskLevel, string> = { green: 'text-good-ink', yellow: 'text-warn-ink', red: 'text-crit-ink' }

function scenarioModel(data: ScenarioResponse, threshold: number): BalanceChartModel {
  const base = data.base.daily
  const scenario = data.scenario.daily
  return {
    dates: base.map((d) => d.date),
    nonWorking: base.map((d) => !d.working_day),
    series: [
      { id: 'base', name: copy.whatIf.now, color: 'accent', values: base.map((d) => d.p50) },
      {
        id: 'scenario', name: copy.whatIf.scenario, color: 'series2', values: scenario.map((d) => d.p50),
        band: { low: scenario.map((d) => d.p10), high: scenario.map((d) => d.p90) },
      },
    ],
    threshold,
    events: [],
    extras: [
      { label: `${copy.whatIf.chance}: как сейчас`, values: base.map((d) => (d.prob_below >= 0.01 ? pct(d.prob_below) : null)) },
      { label: `${copy.whatIf.chance}: если`, values: scenario.map((d) => (d.prob_below >= 0.01 ? pct(d.prob_below) : null)) },
    ],
  }
}

function RiskColumn({ title, level, chance, headline }: { title: string; level: RiskLevel; chance: number; headline: string }) {
  const Icon = LEVEL_ICON[level]
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface p-4">
      <div className="text-sm text-ink-2">{title}</div>
      <div className={cx('mt-1 flex items-center gap-2 text-2xl font-semibold', LEVEL_TONE[level])}>
        <Icon size={24} />
        {pct(chance)}
      </div>
      <div className="text-[13px] text-ink-2">шанс нехватки · {chanceWords(chance)}</div>
      <p className="mt-2 text-sm text-ink">{headline}</p>
    </div>
  )
}

function Result({ data, threshold, busy }: { data: ScenarioResponse; threshold: number; busy: boolean }) {
  const model = useMemo(() => scenarioModel(data, threshold), [data, threshold])
  const { delta } = data
  const shift = delta.gap_date_shift_days
  const facts = [
    `${copy.whatIf.chance}: ${delta.gap_probability >= 0 ? '+' : '−'}${Math.round(Math.abs(delta.gap_probability) * 100)} п. п.`,
    delta.shortfall_p50 !== 0 ? `${copy.whatIf.shortfall}: ${delta.shortfall_p50 > 0 ? '+' : '−'}${rub(Math.abs(delta.shortfall_p50))}` : null,
    shift != null && shift !== 0 ? `${copy.whatIf.when}: ${shift < 0 ? copy.whatIf.earlier(-shift) : copy.whatIf.later(shift)}` : null,
  ].filter(Boolean)

  return (
    <div className={cx('space-y-4 transition-opacity', busy && 'opacity-60')}>
      <Card className="border-accent/40 bg-accent-soft">
        <p className="text-lg font-semibold leading-snug text-ink sm:text-xl">{data.summary}</p>
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-2">
          {facts.map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
      </Card>
      <div className="grid gap-3 sm:grid-cols-2">
        <RiskColumn title={copy.whatIf.now} level={data.base.risk.level} chance={data.base.risk.gap_probability} headline={data.base.risk.headline} />
        <RiskColumn title={copy.whatIf.scenario} level={data.scenario.risk.level} chance={data.scenario.risk.gap_probability} headline={data.scenario.risk.headline} />
      </div>
      <Card aria-labelledby="whatif-chart">
        <SectionTitle id="whatif-chart" hint={copy.hints.corridor}>
          {copy.whatIf.chartTitle}
        </SectionTitle>
        <Suspense fallback={<Spinner />}>
          <div className="-mx-1 h-[280px] sm:mx-0 sm:h-[340px]">
            <BalanceChart
              model={model}
              label={`${copy.whatIf.chartTitle}. ${data.summary}`}
              endLabel
              busy={busy}
            />
          </div>
        </Suspense>
        <ChartLegend model={model} />
      </Card>
    </div>
  )
}

export default function WhatIfPage() {
  const { business } = useCurrentBusiness()
  const bid = business?.id
  const presets = useScenarioPresets(bid)
  const forecast = useForecast(bid, 30)
  const [selected, setSelected] = useState<string | null>(null)
  const [value, setValue] = useState<number | null>(null)

  // Пока ничего не выбрано, показываем первый сценарий: страница сразу отвечает на вопрос «что если», а не пустует
  const preset = presets.data?.presets.find((p) => p.id === (selected ?? presets.data.presets[0]?.id)) ?? null
  const control = preset ? CONTROLS[preset.id] : null

  useEffect(() => {
    if (preset) setValue(firstShock(preset.request)[CONTROLS[preset.id].key] as number)
  }, [preset])

  const debounced = useDebounced(value, 400)
  const request = preset && debounced !== null ? withValue(preset, debounced) : null
  const scenario = useScenario(bid, request)

  if (!bid || presets.isPending) return <Spinner />
  if (presets.isError) return <ErrorBlock onRetry={() => presets.refetch()} message={presets.error.message} />

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-ink sm:text-2xl">{copy.whatIf.title}</h1>
      <p className="text-ink-2">{copy.whatIf.intro}</p>

      <section aria-labelledby="pick-title">
        <h2 id="pick-title" className="mb-2 text-base font-semibold text-ink">
          {copy.whatIf.pick}
        </h2>
        <ul className="grid gap-3 sm:grid-cols-2">
          {presets.data.presets.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                aria-pressed={p.id === preset?.id}
                onClick={() => setSelected(p.id)}
                className={cx(
                  'flex h-full w-full flex-col rounded-2xl border p-4 text-left transition',
                  p.id === preset?.id ? 'border-accent bg-accent-soft' : 'border-line bg-surface hover:border-line-2',
                )}
              >
                <span className="font-semibold text-ink">{p.title}</span>
                <span className="mt-1 text-sm text-ink-2">{p.description}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {preset && control && value !== null && (
        <Card>
          <label htmlFor="whatif-range" className="flex items-baseline justify-between gap-3 text-sm font-medium text-ink-2">
            <span>{copy.whatIf.controls[preset.id]}</span>
            <span className="text-lg font-semibold text-ink">{control.format(value)}</span>
          </label>
          <input
            id="whatif-range"
            type="range"
            min={control.min}
            max={control.max}
            step={control.step}
            value={value}
            onChange={(e) => setValue(Number(e.target.value))}
            aria-valuetext={control.format(value)}
            className="mt-3 h-8 w-full accent-[var(--accent)]"
          />
          <div className="flex justify-between text-xs text-ink-2">
            <span>{control.format(control.min)}</span>
            <span>{control.format(control.max)}</span>
          </div>
        </Card>
      )}

      {preset && scenario.isError && <ErrorBlock message={scenario.error.message} onRetry={() => scenario.refetch()} />}
      {preset && !scenario.data && scenario.isPending && <Spinner label={copy.whatIf.computing} />}
      {preset && scenario.data && (
        <Result data={scenario.data} threshold={forecast.data?.threshold ?? 0} busy={scenario.isPlaceholderData || scenario.isFetching} />
      )}
      {!preset && <p className="text-sm text-ink-2">{copy.whatIf.hint}</p>}
    </div>
  )
}
