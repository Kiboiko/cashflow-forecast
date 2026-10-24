/** Разбор отчёта бэктеста человека A (`reports/backtest_<дата>.json`) в то, что показывает страница «Как мы считаем». */

export interface ApproachRow {
  id: string
  title: string
  relError: number // медианная относительная ошибка остатка
  coverage: number // доля дней, когда факт попал в коридор P10–P90
  recall: number // доля реальных нехваток, о которых предупредили
  precision: number // доля верных тревог
  leadDays: number | null // за сколько дней знаем, медиана
}

export interface HorizonRow {
  horizon: number
  relError: number
  coverage: number
  recall: number
  precision: number
}

export interface ReliabilityPoint {
  predicted: number
  observed: number
  n: number
}

/** Матрица ошибок «был ли разрыв в 30 дней» × «подняли ли тревогу» и метрики классификации. */
export interface Confusion {
  tp: number
  fp: number
  fn: number
  tn: number
  accuracy: number | null
  precision: number | null
  recall: number | null
  specificity: number | null
  f1: number | null
}

/** Метрики итоговой модели на 30 дней: классификация (жёлтый и красный уровень) и ошибка остатка в рублях. */
export interface Metrics {
  yellow: Confusion
  red: Confusion | null
  rocAuc: number | null
  prAuc: number | null
  brier: number | null
  maeRub: number | null
  maeRubMedian: number | null
  rmseRub: number | null
  mseRub2: number | null
  wape: number | null
}

export interface Backtest {
  dataset: string | null // контрольный датасет: имя и период; null — датасет разработки
  metrics: Metrics | null
  generated: string
  companies: number
  points: number
  cutoffFrom: string
  cutoffTo: string
  approaches: ApproachRow[]
  ours: HorizonRow[]
  reliability: ReliabilityPoint[]
  industries: Array<{ industry: string; n: number; relError: number }>
  ablation: { clientBefore: number; clientAfter: number; suppliersBefore: number; suppliersAfter: number; share: number } | null
}

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)
const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

/** Порядок строк таблицы: от простого к нашему подходу. */
export const APPROACH_ORDER = ['naive', 'seasonal_naive', 'structural_m0', 'structural', 'structural_m2', 'structural_calibrated', 'structural_external']
/** Строка, которая и есть продукт: структурная модель + калибровка шанса. */
export const OUR_APPROACH = 'structural_calibrated'

function horizonRow(horizon: number, data: unknown): HorizonRow | null {
  if (!isObject(data) || !isObject(data.detection)) return null
  const relError = num(data.rel_mae_median)
  const coverage = num(data.interval_coverage)
  const recall = num(data.detection.recall)
  const precision = num(data.detection.precision)
  if (relError === null || coverage === null || recall === null || precision === null) return null
  return { horizon, relError, coverage, recall, precision }
}

function confusion(data: unknown): Confusion | null {
  if (!isObject(data)) return null
  const [tp, fp, fn, tn] = [num(data.tp), num(data.fp), num(data.fn), num(data.tn)]
  if (tp === null || fp === null || fn === null || tn === null) return null
  return { tp, fp, fn, tn, accuracy: num(data.accuracy), precision: num(data.precision), recall: num(data.recall), specificity: num(data.specificity), f1: num(data.f1) }
}

function metrics(at30: unknown): Metrics | null {
  if (!isObject(at30)) return null
  const yellow = confusion(at30.detection)
  if (!yellow || !isObject(at30.detection)) return null
  return {
    yellow,
    red: confusion(at30.detection_red),
    rocAuc: num(at30.detection.roc_auc),
    prAuc: num(at30.detection.pr_auc),
    brier: num(at30.detection.brier),
    maeRub: num(at30.mae_rub_mean),
    maeRubMedian: num(at30.mae_rub_median),
    rmseRub: num(at30.rmse_rub),
    mseRub2: num(at30.mse_rub2),
    wape: num(at30.wape),
  }
}

export function parseBacktest(raw: unknown): Backtest | null {
  if (!isObject(raw) || !isObject(raw.test) || !isObject(raw.test.approaches)) return null
  const test = raw.test
  const approachesRaw = test.approaches as Json

  const approaches: ApproachRow[] = []
  for (const id of APPROACH_ORDER) {
    const item = approachesRaw[id]
    if (!isObject(item) || !isObject(item.horizons)) continue
    const at30 = horizonRow(30, item.horizons['30'])
    if (!at30) continue
    const lead = isObject(item.horizons['30']) ? num(item.horizons['30'].lead_days_median) : null
    approaches.push({ id, title: typeof item.title === 'string' ? item.title : id, ...at30, leadDays: lead })
  }
  if (approaches.length === 0) return null

  const ours: HorizonRow[] = []
  const oursRaw = approachesRaw[OUR_APPROACH]
  if (isObject(oursRaw) && isObject(oursRaw.horizons)) {
    for (const horizon of [7, 14, 30]) {
      const row = horizonRow(horizon, oursRaw.horizons[String(horizon)])
      if (row) ours.push(row)
    }
  }

  const reliabilityRaw = Array.isArray(test.reliability_30d_calibrated) ? test.reliability_30d_calibrated : []
  const reliability = reliabilityRaw.flatMap((p): ReliabilityPoint[] => {
    if (!isObject(p)) return []
    const predicted = num(p.predicted)
    const observed = num(p.observed)
    const n = num(p.n)
    return predicted !== null && observed !== null && n !== null ? [{ predicted, observed, n }] : []
  })

  const industries = isObject(test.by_industry)
    ? Object.entries(test.by_industry).flatMap(([industry, value]) => {
        if (!isObject(value)) return []
        const relError = num(value.rel_mae_median)
        const n = num(value.n)
        return relError !== null && n !== null ? [{ industry, n, relError }] : []
      })
    : []

  let ablation: Backtest['ablation'] = null
  if (isObject(test.external_ablation) && isObject(test.external_ablation.flows_wape)) {
    const flows = test.external_ablation.flows_wape
    const wape = (flow: unknown, key: string): number | null => (isObject(flow) && isObject(flow[key]) ? num((flow[key] as Json).wape) : null)
    const a = [wape(flows.client_in, 'structural'), wape(flows.client_in, 'structural_external'), wape(flows.suppliers, 'structural'), wape(flows.suppliers, 'structural_external')]
    const share = num(test.external_ablation.share_of_points)
    if (a.every((v) => v !== null) && share !== null) {
      ablation = { clientBefore: a[0]!, clientAfter: a[1]!, suppliersBefore: a[2]!, suppliersAfter: a[3]!, share }
    }
  }

  const cutoffs = isObject(raw.cutoffs) && Array.isArray(raw.cutoffs.test) ? (raw.cutoffs.test as unknown[]).filter((c): c is string => typeof c === 'string') : []
  const oursAt30 = isObject(oursRaw) && isObject(oursRaw.horizons) ? oursRaw.horizons['30'] : null
  const control = isObject(raw.control) ? raw.control : null
  const period = control && Array.isArray(control.period) ? (control.period as unknown[]).filter((d): d is string => typeof d === 'string') : []
  const dataset = control && typeof control.dataset === 'string' ? `${control.dataset}${period.length === 2 ? ` (${period[0]} — ${period[1]})` : ''}` : null
  return {
    dataset,
    metrics: metrics(oursAt30),
    generated: typeof raw.generated === 'string' ? raw.generated : '',
    companies: num(raw.n_companies) ?? 0,
    points: num(test.n_points) ?? 0,
    cutoffFrom: cutoffs[0] ?? '',
    cutoffTo: cutoffs[cutoffs.length - 1] ?? '',
    approaches,
    ours,
    reliability,
    industries: industries.sort((a, b) => a.relError - b.relError),
    ablation,
  }
}
