import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { api, getToken } from './client'
import type {
  Alert,
  BacktestReport,
  BusinessOut,
  CategoryInfo,
  CategoryPatch,
  CategoryPatchResult,
  CategorySummary,
  CounterpartyChecks,
  DemoPersona,
  Direction,
  EventCreate,
  EventOut,
  EventPatch,
  EventsImportResult,
  ForecastResponse,
  ImportResult,
  JuryCompany,
  KeyRate,
  MeResponse,
  PlanFactResponse,
  ReceivablesResponse,
  RecurringOut,
  ReplayResult,
  ScenarioPresets,
  ScenarioRequest,
  ScenarioResponse,
  SettingsPatch,
  SnapshotBrief,
  TelegramTestResult,
  TokenResponse,
  TransactionPage,
} from './types'

/** Ключи запросов. Всё, что относится к бизнесу, начинается с ['b', id] — одним вызовом сбрасывается целиком. */
export const qk = {
  me: ['me'] as const,
  personas: ['personas'] as const,
  categories: ['categories'] as const,
  backtest: ['backtest'] as const,
  keyRate: ['key-rate'] as const,
  juryCompanies: ['jury-companies'] as const,
  business: (bid: string) => ['b', bid, 'business'] as const,
  checks: (bid: string, inns: string[]) => ['b', bid, 'counterparty-checks', inns.join(',')] as const,
  forecast: (bid: string, horizon: number) => ['b', bid, 'forecast', horizon] as const,
  transactions: (bid: string, params: string) => ['b', bid, 'transactions', params] as const,
  summary: (bid: string, months: number) => ['b', bid, 'summary', months] as const,
  events: (bid: string) => ['b', bid, 'events'] as const,
  receivables: (bid: string, direction: Direction) => ['b', bid, 'receivables', direction] as const,
  recurring: (bid: string) => ['b', bid, 'recurring'] as const,
  presets: (bid: string) => ['b', bid, 'presets'] as const,
  planFact: (bid: string, snapshotId: number | null) => ['b', bid, 'plan-fact', snapshotId ?? 'default'] as const,
  snapshots: (bid: string) => ['b', bid, 'snapshots'] as const,
  alerts: (bid: string, status: string) => ['b', bid, 'alerts', status] as const,
}

/** Данные бизнеса изменились (импорт, правка плана, шаг машины времени…) — всё зависимое перечитываем. */
export function refreshBusiness(client: QueryClient, bid: string) {
  return client.invalidateQueries({ queryKey: ['b', bid] })
}

/* ---------- вход ---------- */
export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => api.get<MeResponse>('/me'),
    enabled: !!getToken(),
    retry: false,
    staleTime: 60_000,
  })
}

export function usePersonas() {
  return useQuery({
    queryKey: qk.personas,
    queryFn: () => api.get<DemoPersona[]>('/auth/demo-personas'),
    retry: false,
    staleTime: 5 * 60_000,
  })
}

export function useLogin() {
  return useMutation({
    mutationFn: (body: { email: string; password: string }) => api.post<TokenResponse>('/auth/login', body),
  })
}

export function useDemoLogin() {
  return useMutation({ mutationFn: (email: string) => api.post<TokenResponse>('/auth/demo-login', { email }) })
}

/** Режим жюри: компании датасета, которые можно открыть. Нет датасета или демо-режим выключен — пусто. */
export function useJuryCompanies() {
  return useQuery({
    queryKey: qk.juryCompanies,
    queryFn: () => api.get<JuryCompany[]>('/auth/jury-companies'),
    retry: false,
    staleTime: 5 * 60_000,
  })
}

/** Первый вход в компанию загружает её выписку и считает прогноз — это может занять до минуты. */
export function useJuryLogin() {
  return useMutation({ mutationFn: (companyId: string) => api.post<TokenResponse>('/auth/jury-login', { company_id: companyId }) })
}

/* ---------- бизнес и прогноз ---------- */
export function useBusiness(bid: string | undefined) {
  return useQuery({
    queryKey: qk.business(bid ?? ''),
    queryFn: () => api.get<BusinessOut>(`/businesses/${bid}`),
    enabled: !!bid,
    staleTime: 30_000,
  })
}

/** Прогноз. При смене горизонта прежний график остаётся на экране (притушенным), пока приходит новый. */
export function useForecast(bid: string | undefined, horizon: number) {
  return useQuery({
    queryKey: qk.forecast(bid ?? '', horizon),
    queryFn: () => api.get<ForecastResponse>(`/businesses/${bid}/forecast?horizon=${horizon}`),
    enabled: !!bid,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: (count, error) => !(error instanceof Error && 'status' in error && (error as { status: number }).status === 409) && count < 1,
  })
}

export function useUpdateSettings(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.patch<BusinessOut>(`/businesses/${bid}/settings`, patch),
    onSuccess: () => refreshBusiness(client, bid),
  })
}

/** Ключевая ставка Банка России: если сайт ЦБ недоступен, сервер отвечает запасным значением (`source: 'fallback'`). */
export function useKeyRate() {
  return useQuery({
    queryKey: qk.keyRate,
    queryFn: () => api.get<KeyRate>('/reference/key-rate'),
    staleTime: 60 * 60_000,
    retry: false,
  })
}

const MAX_CHECKED_INNS = 200

/** Демо-проверка контрагентов по ИНН: в ответе только те, с кем что-то не так (ликвидация, банкротство…). */
export function useCounterpartyChecks(bid: string | undefined, inns: string[]) {
  const unique = [...new Set(inns)].sort().slice(0, MAX_CHECKED_INNS)
  return useQuery({
    queryKey: qk.checks(bid ?? '', unique),
    queryFn: () => api.get<CounterpartyChecks>(`/businesses/${bid}/counterparty-checks?${unique.map((inn) => `inn=${encodeURIComponent(inn)}`).join('&')}`),
    enabled: !!bid && unique.length > 0,
    staleTime: 5 * 60_000,
  })
}

/** Тестовое сообщение в Telegram: сервер шлёт его на сохранённый id чата. */
export function useTelegramTest(bid: string) {
  return useMutation({
    mutationFn: () => api.post<TelegramTestResult>(`/businesses/${bid}/telegram/test`),
  })
}

/* ---------- операции ---------- */
export interface TransactionFilters {
  q?: string
  category?: string
  review?: boolean
  from?: string
  to?: string
  limit: number
  offset: number
}

function query(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '' && value !== false) search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function useTransactions(bid: string | undefined, filters: TransactionFilters) {
  const search = query(filters)
  return useQuery({
    queryKey: qk.transactions(bid ?? '', search),
    queryFn: () => api.get<TransactionPage>(`/businesses/${bid}/transactions${search}`),
    enabled: !!bid,
    placeholderData: keepPreviousData,
  })
}

export function useCategories() {
  return useQuery({
    queryKey: qk.categories,
    queryFn: () => api.get<CategoryInfo[]>('/categories'),
    staleTime: Infinity,
  })
}

export function useCategorySummary(bid: string | undefined, months = 12) {
  return useQuery({
    queryKey: qk.summary(bid ?? '', months),
    queryFn: () => api.get<CategorySummary>(`/businesses/${bid}/categories/summary?months=${months}`),
    enabled: !!bid,
  })
}

export function usePatchTransaction(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: CategoryPatch & { id: number }) =>
      api.patch<CategoryPatchResult>(`/businesses/${bid}/transactions/${id}`, body),
    onSuccess: () => refreshBusiness(client, bid),
  })
}

export function useImportStatement(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api.upload<ImportResult>(`/businesses/${bid}/imports`, form)
    },
    onSuccess: () => refreshBusiness(client, bid),
  })
}

/* ---------- план ---------- */
export function useEvents(bid: string | undefined) {
  return useQuery({
    queryKey: qk.events(bid ?? ''),
    queryFn: () => api.get<EventOut[]>(`/businesses/${bid}/events`),
    enabled: !!bid,
  })
}

export function useCreateEvent(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: EventCreate) => api.post<EventOut>(`/businesses/${bid}/events`, body),
    onSuccess: () => refreshBusiness(client, bid),
  })
}

export function usePatchEvent(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: EventPatch & { id: number }) => api.patch<EventOut>(`/businesses/${bid}/events/${id}`, body),
    onSuccess: () => refreshBusiness(client, bid),
  })
}

export function useDeleteEvent(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/businesses/${bid}/events/${id}`),
    onSuccess: () => refreshBusiness(client, bid),
  })
}

export function useImportEvents(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api.upload<EventsImportResult>(`/businesses/${bid}/events/import`, form)
    },
    onSuccess: () => refreshBusiness(client, bid),
  })
}

export function useReceivables(bid: string | undefined, direction: Direction) {
  return useQuery({
    queryKey: qk.receivables(bid ?? '', direction),
    queryFn: () => api.get<ReceivablesResponse>(`/businesses/${bid}/receivables?direction=${direction}`),
    enabled: !!bid,
  })
}

export function useRecurring(bid: string | undefined) {
  return useQuery({
    queryKey: qk.recurring(bid ?? ''),
    queryFn: () => api.get<RecurringOut[]>(`/businesses/${bid}/recurring`),
    enabled: !!bid,
  })
}

/* ---------- «что если» ---------- */
export function useScenarioPresets(bid: string | undefined) {
  return useQuery({
    queryKey: qk.presets(bid ?? ''),
    queryFn: () => api.get<ScenarioPresets>(`/businesses/${bid}/scenarios/presets`),
    enabled: !!bid,
  })
}

/** Расчёт сценария. Это POST, но он ничего не меняет, поэтому живёт как запрос: одинаковые настройки — из кэша. */
export function useScenario(bid: string | undefined, request: ScenarioRequest | null) {
  return useQuery({
    queryKey: ['b', bid ?? '', 'scenario', JSON.stringify(request)] as const,
    queryFn: () => api.post<ScenarioResponse>(`/businesses/${bid}/scenarios/run`, request),
    enabled: !!bid && request !== null,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  })
}

/* ---------- план-факт, предупреждения, машина времени ---------- */
export function usePlanFact(bid: string | undefined, snapshotId: number | null) {
  return useQuery({
    queryKey: qk.planFact(bid ?? '', snapshotId),
    queryFn: () =>
      api.get<PlanFactResponse>(`/businesses/${bid}/plan-fact${snapshotId === null ? '' : `?snapshot_id=${snapshotId}`}`),
    enabled: !!bid,
    placeholderData: keepPreviousData,
  })
}

export function useSnapshots(bid: string | undefined) {
  return useQuery({
    queryKey: qk.snapshots(bid ?? ''),
    queryFn: () => api.get<SnapshotBrief[]>(`/businesses/${bid}/snapshots`),
    enabled: !!bid,
  })
}

export function useAlerts(bid: string | undefined, status: 'open' | 'all' = 'open') {
  return useQuery({
    queryKey: qk.alerts(bid ?? '', status),
    queryFn: () => api.get<Alert[]>(`/businesses/${bid}/alerts?status=${status}`),
    enabled: !!bid,
    refetchInterval: 60_000,
  })
}

export function useAckAlert(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post<Alert>(`/businesses/${bid}/alerts/${id}/ack`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['b', bid, 'alerts'] }),
  })
}

export function useReplayAdvance(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (days: number) => api.post<ReplayResult>(`/demo/businesses/${bid}/replay/advance`, { days }),
    onSuccess: () => refreshBusiness(client, bid),
  })
}

export function useReplayReset(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<ReplayResult>(`/demo/businesses/${bid}/replay/reset`, {}),
    onSuccess: () => refreshBusiness(client, bid),
  })
}

export function useBacktest() {
  return useQuery({
    queryKey: qk.backtest,
    queryFn: () => api.get<BacktestReport>('/quality/backtest'),
    staleTime: Infinity,
    retry: false,
  })
}
