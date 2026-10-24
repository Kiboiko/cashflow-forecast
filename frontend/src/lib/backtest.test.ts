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

  it('достаёт матрицы ошибок, метрики и пометку контрольного датасета', () => {
    const at30 = {
      ...horizon(0.19, 0.78, 0.96, 0.43),
      detection: { recall: 0.96, precision: 0.43, roc_auc: 0.94, pr_auc: 0.8, brier: 0.063, tp: 113, fp: 152, fn: 5, tn: 495, accuracy: 0.79, specificity: 0.77, f1: 0.59 },
      detection_red: { recall: 0.62, precision: 0.74, tp: 73, fp: 25, fn: 45, tn: 622, accuracy: 0.91, specificity: 0.96, f1: 0.68 },
      mae_rub_mean: 1_250_000, mae_rub_median: 210_000, rmse_rub: 3_400_000, mse_rub2: 1.2e13, wape: 0.31,
    }
    const control = {
      ...sample,
      control: { dataset: 'control', period: ['2025-01-01', '2026-06-30'] },
      test: { ...sample.test, approaches: { [OUR_APPROACH]: { title: 'Итоговая', horizons: { '30': at30 } } } },
    }
    const report = parseBacktest(control)!
    expect(report.dataset).toBe('control (2025-01-01 — 2026-06-30)')
    expect(report.metrics?.yellow).toMatchObject({ tp: 113, fn: 5, accuracy: 0.79, f1: 0.59 })
    expect(report.metrics?.red).toMatchObject({ tp: 73, precision: 0.74 })
    expect(report.metrics).toMatchObject({ rocAuc: 0.94, brier: 0.063, maeRub: 1_250_000, rmseRub: 3_400_000, wape: 0.31 })
    expect(parseBacktest(sample)!.metrics).toBeNull()                   // старый отчёт без матрицы — карточку не показываем
    expect(parseBacktest(sample)!.dataset).toBeNull()
  })

  it('не падает на чужом формате', () => {
    expect(parseBacktest(null)).toBeNull()
    expect(parseBacktest({})).toBeNull()
    expect(parseBacktest({ test: { approaches: {} } })).toBeNull()
  })
})
