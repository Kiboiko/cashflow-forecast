import { describe, expect, it } from 'vitest'

import { OUR_APPROACH, parseBacktest } from './backtest'

const horizon = (relError: number, coverage: number, recall: number, precision: number) => ({
  rel_mae_median: relError,
  interval_coverage: coverage,
  detection: { recall, precision },
  lead_days_median: 8,
})

const sample = {
  generated: '2026-10-24',
  n_companies: 143,
  cutoffs: { test: ['2026-03-31', '2026-08-31'] },
  test: {
    n_points: 765,
    approaches: {
      naive: { title: 'Наивный', horizons: { '30': horizon(0.25, 0.03, 0.26, 1) } },
      [OUR_APPROACH]: {
        title: 'Структурный + калибровка шанса',
        horizons: { '7': horizon(0.076, 0.73, 0.91, 0.36), '14': horizon(0.124, 0.76, 0.93, 0.37), '30': horizon(0.195, 0.79, 0.96, 0.43) },
      },
      broken: { title: 'Без данных', horizons: {} },
    },
    reliability_30d_calibrated: [{ predicted: 0.02, observed: 0.01, n: 500 }, { predicted: 0.35, observed: 0.45, n: 38 }, { bad: true }],
    by_industry: { Автосервис: { n: 50, rel_mae_median: 0.14 }, Гостиницы: { n: 36, rel_mae_median: 0.42 } },
    external_ablation: {
      share_of_points: 0.4,
      flows_wape: {
        client_in: { structural: { wape: 0.18 }, structural_external: { wape: 0.17 } },
        suppliers: { structural: { wape: 0.22 }, structural_external: { wape: 0.19 } },
      },
    },
  },
}

describe('parseBacktest', () => {
  it('разбирает отчёт и держит порядок от простого к нашему подходу', () => {
    const report = parseBacktest(sample)!
    expect(report.approaches.map((a) => a.id)).toEqual(['naive', OUR_APPROACH])
    expect(report.points).toBe(765)
    expect(report.cutoffFrom).toBe('2026-03-31')
    expect(report.ours.map((h) => h.horizon)).toEqual([7, 14, 30])
    expect(report.approaches[1].recall).toBe(0.96)
    expect(report.approaches[1].leadDays).toBe(8)
  })

  it('пропускает битые точки и сортирует отрасли по ошибке', () => {
    const report = parseBacktest(sample)!
    expect(report.reliability).toHaveLength(2)
    expect(report.industries.map((i) => i.industry)).toEqual(['Автосервис', 'Гостиницы'])
    expect(report.ablation).toMatchObject({ clientBefore: 0.18, clientAfter: 0.17, share: 0.4 })
  })

  it('не падает на чужом формате', () => {
    expect(parseBacktest(null)).toBeNull()
    expect(parseBacktest({})).toBeNull()
    expect(parseBacktest({ test: { approaches: {} } })).toBeNull()
  })
})
