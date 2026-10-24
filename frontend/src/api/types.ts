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

export type RiskLevel = Risk['level']
