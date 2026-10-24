import { lazy, Suspense, useMemo } from 'react'

import { useBacktest } from '../api/hooks'
import { useCurrentBusiness } from '../auth'
import { Badge, Card, EmptyState, ErrorBlock, Hint, SectionTitle, Spinner, cx } from '../components/ui'
import { OUR_APPROACH, parseBacktest, type ApproachRow, type Backtest } from '../lib/backtest'
import { copy } from '../lib/copy'
import { dmyLong, pct } from '../lib/format'

const ReliabilityChart = lazy(() => import('../components/chart/ReliabilityChart'))

const how = copy.how

function Steps() {
  return (
    <ol className="grid gap-3 sm:grid-cols-2">
      {how.steps.map((step, i) => (
        <li key={step.title} className="flex gap-3 rounded-2xl border border-line bg-surface p-4">
          <span className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-accent text-sm font-semibold text-white">{i + 1}</span>
          <div>
            <h3 className="font-semibold text-ink">{step.title}</h3>
            <p className="mt-1 text-sm text-ink-2">{step.text}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}

function Th({ children, hint, right = true }: { children: string; hint?: string; right?: boolean }) {
  return (
    <th scope="col" className={cx('whitespace-nowrap px-3 py-2 font-medium', right && 'text-right')}>
      <span className="inline-flex items-center gap-1">
        {children}
        {hint && <Hint text={hint} />}
      </span>
    </th>
  )
}

function ApproachesTable({ rows }: { rows: ApproachRow[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[40rem] border-collapse text-sm">
        <caption className="sr-only">{how.tableTitle}</caption>
        <thead className="bg-surface-2 text-left text-ink-2">
          <tr>
            <Th right={false}>{how.cols.approach}</Th>
            <Th hint={how.hints.error}>{how.cols.error}</Th>
            <Th hint={how.hints.coverage}>{how.cols.coverage}</Th>
            <Th hint={how.hints.recall}>{how.cols.recall}</Th>
            <Th hint={how.hints.precision}>{how.cols.precision}</Th>
            <Th hint={how.hints.lead}>{how.cols.lead}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const ours = row.id === OUR_APPROACH
            return (
              <tr key={row.id} className={cx('border-t border-line', ours && 'bg-accent-soft font-medium')}>
                <th scope="row" className="px-3 py-2 text-left font-normal text-ink">
                  {row.title}
                  {ours && <Badge tone="accent" className="ml-2">{how.ours}</Badge>}
                </th>
                <td className="tnum px-3 py-2 text-right">{pct(row.relError)}</td>
                <td className="tnum px-3 py-2 text-right">{pct(row.coverage)}</td>
                <td className="tnum px-3 py-2 text-right">{pct(row.recall)}</td>
                <td className="tnum px-3 py-2 text-right">{pct(row.precision)}</td>
                <td className="tnum px-3 py-2 text-right">{row.leadDays != null ? `${row.leadDays} дн.` : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Quality({ report, industry }: { report: Backtest; industry?: string | null }) {
  const reliability = report.reliability
  const maxError = Math.max(...report.industries.map((i) => i.relError), 0.01)
  return (
    <div className="space-y-4">
      <p className="text-ink-2">{how.qualityLead(report.points, report.companies, dmyLong(report.cutoffFrom), dmyLong(report.cutoffTo))}</p>

      <Card aria-labelledby="approaches-title">
        <SectionTitle id="approaches-title">{how.tableTitle}</SectionTitle>
        <ApproachesTable rows={report.approaches} />
        {report.ours.length > 0 && (
          <>
            <h3 className="mb-2 mt-5 text-base font-semibold text-ink">{how.horizonTitle}</h3>
            <div className="grid grid-cols-3 gap-2">
              {report.ours.map((h) => (
                <div key={h.horizon} className="rounded-xl bg-surface-2 p-3 text-center">
                  <div className="text-sm text-ink-2">на {h.horizon} дн.</div>
                  <div className="mt-1 text-xl font-semibold text-ink">{pct(h.relError)}</div>
                  <div className="text-xs text-ink-2">ошибка остатка</div>
                  <div className="mt-1 text-xs text-ink-2">находим {pct(h.recall)}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </Card>

      {reliability.length > 0 && (
        <Card aria-labelledby="reliability-title">
          <SectionTitle id="reliability-title" hint={copy.hints.chance}>
            {how.reliabilityTitle}
          </SectionTitle>
          <p className="mb-3 text-sm text-ink-2">{how.reliabilityText}</p>
          <Suspense fallback={<Spinner />}>
            <ReliabilityChart points={reliability} label={`${how.reliabilityTitle}. ${how.reliabilityText}`} />
          </Suspense>
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer font-medium text-accent-ink">{how.reliabilityNumbers}</summary>
            <table className="mt-2 w-full max-w-md border-collapse">
              <thead className="text-left text-ink-2">
                <tr>
                  <th scope="col" className="py-1 font-medium">Мы говорили</th>
                  <th scope="col" className="py-1 text-right font-medium">Бывало</th>
                  <th scope="col" className="py-1 text-right font-medium">Случаев</th>
                </tr>
              </thead>
              <tbody>
                {reliability.map((p) => (
                  <tr key={p.predicted} className="border-t border-line">
                    <td className="tnum py-1">{pct(p.predicted)}</td>
                    <td className="tnum py-1 text-right">{pct(p.observed)}</td>
                    <td className="tnum py-1 text-right">{p.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </Card>
      )}

      {report.ablation && (
        <Card aria-labelledby="ablation-title">
          <SectionTitle id="ablation-title">{how.ablationTitle}</SectionTitle>
          <p className="text-ink">
            {how.ablation(report.ablation.share, report.ablation.clientBefore, report.ablation.clientAfter, report.ablation.suppliersBefore, report.ablation.suppliersAfter)}
          </p>
        </Card>
      )}

      {report.industries.length > 0 && (
        <Card aria-labelledby="industry-title">
          <SectionTitle id="industry-title">{how.industryTitle}</SectionTitle>
          <p className="-mt-2 mb-3 text-sm text-ink-2">{how.industryText}</p>
          <ul className="space-y-2.5">
            {report.industries.map((item) => {
              const mine = industry != null && item.industry === industry
              return (
                <li key={item.industry}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className={cx('text-ink', mine && 'font-semibold')}>
                      {item.industry}
                      {mine && <Badge tone="accent" className="ml-2">ваша отрасль</Badge>}
                    </span>
                    <span className="tnum whitespace-nowrap text-ink-2">{pct(item.relError)}</span>
                  </div>
                  <div className="mt-1 h-2 rounded-r-[4px] bg-surface-2" aria-hidden="true">
                    <div className="h-full rounded-r-[4px] bg-accent" style={{ width: `${Math.max((item.relError / maxError) * 100, 2)}%`, opacity: mine ? 1 : 0.55 }} />
                  </div>
                </li>
              )
            })}
          </ul>
        </Card>
      )}
    </div>
  )
}

export default function HowItWorksPage() {
  const { business } = useCurrentBusiness()
  const backtest = useBacktest()
  const report = useMemo(() => (backtest.data ? parseBacktest(backtest.data.report) : null), [backtest.data])

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-ink sm:text-2xl">{how.title}</h1>
        <p className="mt-2 text-ink-2">{how.lead}</p>
      </header>

      <section aria-labelledby="steps-title" className="space-y-3">
        <h2 id="steps-title" className="text-lg font-semibold text-ink">{how.stepsTitle}</h2>
        <Steps />
      </section>

      <section aria-labelledby="quality-title" className="space-y-3">
        <h2 id="quality-title" className="text-lg font-semibold text-ink">{how.qualityTitle}</h2>
        {backtest.isPending ? (
          <Spinner />
        ) : backtest.isError || !report ? (
          backtest.isError && !(backtest.error instanceof Error && 'status' in backtest.error && (backtest.error as { status: number }).status === 404) ? (
            <ErrorBlock onRetry={() => backtest.refetch()} />
          ) : (
            <EmptyState title={how.noReport} />
          )
        ) : (
          <Quality report={report} industry={business?.industry} />
        )}
      </section>

      <section aria-labelledby="sources-title" className="space-y-3">
        <h2 id="sources-title" className="text-lg font-semibold text-ink">{how.sourcesTitle}</h2>
        <ul className="grid gap-3 sm:grid-cols-2">
          {how.sources.map((source) => (
            <li key={source.title} className="rounded-2xl border border-line bg-surface p-4">
              <h3 className="font-semibold text-ink">{source.title}</h3>
              <p className="mt-1 text-sm text-ink-2">{source.text}</p>
            </li>
          ))}
        </ul>
      </section>

      <Card>
        <SectionTitle>{how.safetyTitle}</SectionTitle>
        <p className="text-ink">{how.safety}</p>
      </Card>
    </div>
  )
}
