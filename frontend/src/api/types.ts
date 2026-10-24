/** Удобные имена для типов, сгенерированных из OpenAPI (`make types` → types.gen.ts). */
import type { components } from './types.gen'

type S = components['schemas']

export type ForecastResponse = S['ForecastResponse']
export type DailyPoint = S['DailyPoint']
export type ForecastEvent = S['ForecastEvent']
export type Risk = S['Risk']
export type Driver = S['Driver']
export type MandatoryCoverage = S['MandatoryCoverage']
export type Recommendation = S['Recommendation']
export type Insight = S['Insight']
export type DataQuality = S['DataQuality']

export type TokenResponse = S['TokenResponse']
export type UserOut = S['UserOut']
export type MeResponse = S['MeResponse']
export type BusinessBrief = S['BusinessBrief']
export type BusinessOut = S['BusinessOut']
export type BusinessSettings = S['BusinessSettings']
export type SettingsPatch = S['SettingsPatch']
export type DemoPersona = S['DemoPersona']

export type TransactionOut = S['TransactionOut']
export type TransactionPage = S['TransactionPage']
export type CategoryInfo = S['CategoryInfo']
export type CategoryPatch = S['CategoryPatch']
export type CategoryPatchResult = S['CategoryPatchResult']
export type CategorySummary = S['CategorySummary']
export type CategorySummaryRow = S['CategorySummaryRow']
export type ImportResult = S['ImportResult']

export type EventOut = S['EventOut']
export type EventCreate = S['EventCreate']
export type EventPatch = S['EventPatch']
export type EventsImportResult = S['EventsImportResult']
export type RecurringOut = S['RecurringOut']
export type ReceivablesResponse = S['ReceivablesResponse']
export type ReceivableRow = S['ReceivableRow']
export type ReceivableInvoice = S['ReceivableInvoice']

export type ScenarioPreset = S['ScenarioPreset']
export type ScenarioPresets = S['ScenarioPresets']
export type ScenarioRequest = S['ScenarioRequest']
export type ScenarioResponse = S['ScenarioResponse']

export type PlanFactResponse = S['PlanFactResponse']
export type PlanFactEvent = S['PlanFactEvent']
export type SnapshotBrief = S['SnapshotBrief']
export type Alert = S['Alert']
export type ReplayResult = S['ReplayResult']
export type BacktestReport = S['BacktestReport']

export type RiskLevel = Risk['level']
export type Direction = 'in' | 'out'
