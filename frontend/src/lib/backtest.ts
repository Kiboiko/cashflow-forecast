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

export interface Backtest {
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
  return {
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
