import { describe, expect, it } from 'vitest'

import type { ForecastEvent } from '../../api/types'
import { niceAxis, nonWorkingRuns, pickChartEvents, tooltipContent, type BalanceChartModel } from './model'

describe('niceAxis', () => {
  it('даёт круглые границы и шаг', () => {
    const axis = niceAxis(-385304, 371779)
    expect(axis.step).toBe(200000)
    expect(axis.min).toBe(-400000)
    expect(axis.max).toBe(400000)
  })

  it('не ломается на плоской линии', () => {
    const axis = niceAxis(100, 100)
    expect(axis.min).toBeLessThan(100)
    expect(axis.max).toBeGreaterThan(100)
    expect(Number.isFinite(axis.step)).toBe(true)
  })

  it('работает на нуле', () => {
    const axis = niceAxis(0, 0)
    expect(axis.max).toBeGreaterThan(axis.min)
  })
})

describe('nonWorkingRuns', () => {
  const dates = ['2026-08-01', '2026-08-02', '2026-08-03', '2026-08-04', '2026-08-08', '2026-08-09']
  it('склеивает подряд идущие выходные, включая хвост', () => {
    expect(nonWorkingRuns(dates, [true, true, false, false, true, true])).toEqual([
      ['2026-08-01', '2026-08-02'],
      ['2026-08-08', '2026-08-09'],
    ])
  })
  it('пусто, если все дни рабочие', () => {
    expect(nonWorkingRuns(dates, dates.map(() => false))).toEqual([])
  })
})

const event = (over: Partial<ForecastEvent>): ForecastEvent => ({
  id: 'e', date: '2026-08-10', date_range: null, direction: 'out', amount: 100, category: 'rent', title: 'Аренда',
  source: 'recurring', probability: 0.9, mandatory: true, status: 'planned', explain: '', ...over,
})

describe('pickChartEvents', () => {
  const dates = ['2026-08-10', '2026-08-11']
  it('берёт самые крупные, без недостоверных и вне графика', () => {
    const picked = pickChartEvents(
      [
        event({ id: 'a', amount: 50 }),
        event({ id: 'b', amount: 500 }),
        event({ id: 'c', amount: 900, probability: 0.2 }),
        event({ id: 'd', amount: 700, date: '2026-09-30' }),
        event({ id: 'e', amount: 10, date: null }),
      ],
      dates,
      5,
    )
    expect(picked.map((e) => e.id)).toEqual(['b', 'a'])
  })
})

describe('tooltipContent', () => {
  const model: BalanceChartModel = {
    dates: ['2026-08-10', '2026-08-11'],
    nonWorking: [false, true],
    series: [{ id: 'p50', name: 'Ожидаемый остаток', color: 'accent', values: [300000, 250000], band: { low: [200000, 100000], high: [400000, 350000] } }],
    threshold: 0,
    events: [{ id: 'r', date: '2026-08-11', direction: 'out', amount: 262700, title: 'Аренда', source: 'recurring', probability: 0.99, mandatory: true, explain: '' }],
    extras: [{ label: 'Шанс нехватки', values: ['3%', null] }],
  }
  it('значения, коридор, шанс и платежи дня', () => {
    const first = tooltipContent(model, 0)
    expect(first.rows.map((r) => r.kind)).toEqual(['line', 'band', 'band', 'text'])
    expect(first.events).toEqual([])
    const second = tooltipContent(model, 1)
    expect(second.nonWorking).toBe(true)
    expect(second.rows.some((r) => r.kind === 'text')).toBe(false)
    expect(second.events[0].value.startsWith('−')).toBe(true)
  })
})
