import { useEffect, useState } from 'react'

/** Цвета графика, прочитанные из CSS-переменных (ECharts не понимает `var(--x)`). Тёмная тема подхватывается сама. */
export interface ChartTokens {
  surface: string
  ink: string
  ink2: string
  ink3: string
  line: string
  line2: string
  accent: string
  series2: string
  crit: string
  accentWash: string
  series2Wash: string
  critWash: string
  weekendWash: string
}

export function withAlpha(color: string, alpha: number): string {
  const hex = color.trim().replace('#', '')
  const full = hex.length === 3 ? [...hex].map((c) => c + c).join('') : hex
  const n = Number.parseInt(full, 16)
  if (Number.isNaN(n) || full.length !== 6) return color
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

function token(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export function readChartTokens(): ChartTokens {
  const ink = token('--ink', '#0b0b0b')
  const accent = token('--accent', '#2a78d6')
  const series2 = token('--series-2', '#eb6834')
  const crit = token('--crit', '#d03b3b')
  return {
    surface: token('--surface', '#fcfcfb'),
    ink,
    ink2: token('--ink-2', '#52514e'),
    ink3: token('--ink-3', '#898781'),
    line: token('--line', '#e1e0d9'),
    line2: token('--line-2', '#c3c2b7'),
    accent,
    series2,
    crit,
    accentWash: withAlpha(accent, 0.11),
    series2Wash: withAlpha(series2, 0.11),
    critWash: withAlpha(crit, 0.1),
    weekendWash: withAlpha(ink, 0.04),
  }
}

export function useChartTokens(): ChartTokens {
  const [tokens, setTokens] = useState<ChartTokens>(readChartTokens)
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => setTokens(readChartTokens())
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return tokens
}
