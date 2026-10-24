import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useBusiness, useForecast } from '../api/hooks'
import { useCurrentBusiness } from '../auth'
import { AlertsCard } from '../components/AlertsCard'
import { ForecastChartCard, type Horizon } from '../components/ForecastChartCard'
import { DriversList, Insights, ObligationsList, QualityNotice, Recommendations, RiskCard, SummaryStats } from '../components/HomeParts'
import { EmptyState, ErrorBlock, Spinner } from '../components/ui'
import { copy } from '../lib/copy'

export default function HomePage() {
  const { business } = useCurrentBusiness()
  const details = useBusiness(business?.id)
  const [horizon, setHorizon] = useState<Horizon>(30)
  const [synced, setSynced] = useState(false)

  // Горизонт по умолчанию — из настроек бизнеса; дальше пользователь переключает его сам.
  useEffect(() => {
    if (!synced && details.data) {
      setHorizon(details.data.settings.horizon_days)
      setSynced(true)
    }
  }, [details.data, synced])

  const forecast = useForecast(synced ? business?.id : undefined, horizon)

  if (!business || details.isPending || (synced && forecast.isPending)) return <Spinner />
  if (forecast.isError) {
    if (forecast.error instanceof ApiError && forecast.error.status === 409) {
      return (
        <EmptyState
          title={copy.home.noDataTitle}
          text={copy.home.noDataText}
          action={
            <Link to="/operations" className="inline-flex min-h-11 items-center rounded-xl bg-accent px-4 font-medium text-white">
              {copy.operations.import}
            </Link>
          }
        />
      )
    }
    return <ErrorBlock message={forecast.error.message} onRetry={() => forecast.refetch()} />
  }
  const data = forecast.data
  if (!data) return <Spinner />

  return (
    <div className="space-y-4">
      <RiskCard risk={data.risk} dataAsOf={data.data_quality.data_as_of} quality={data.data_quality} />
      <SummaryStats forecast={data} />
      <AlertsCard bid={business.id} compact />
      <ForecastChartCard forecast={data} horizon={horizon} onHorizon={setHorizon} busy={forecast.isPlaceholderData} />
      <QualityNotice quality={data.data_quality} />
      <div className="grid gap-4 lg:grid-cols-2">
        <ObligationsList items={data.risk.mandatory_coverage} />
        <DriversList drivers={data.risk.drivers} />
      </div>
      <Recommendations items={data.recommendations} risk={data.risk} />
      <Insights items={data.insights} />
    </div>
  )
}
