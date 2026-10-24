import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, getToken } from './client'
import type {
  BusinessOut,
  DemoPersona,
  ForecastResponse,
  MeResponse,
  SettingsPatch,
  TokenResponse,
} from './types'

export const qk = {
  me: ['me'] as const,
  personas: ['personas'] as const,
  business: (bid: string) => ['business', bid] as const,
  forecast: (bid: string, horizon: number) => ['forecast', bid, horizon] as const,
}

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
  return useMutation({
    mutationFn: (email: string) => api.post<TokenResponse>('/auth/demo-login', { email }),
  })
}

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
  })
}

export function useUpdateSettings(bid: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.patch<BusinessOut>(`/businesses/${bid}/settings`, patch),
    onSuccess: (business) => {
      client.setQueryData(qk.business(bid), business)
      void client.invalidateQueries({ queryKey: ['forecast', bid] })
    },
  })
}
