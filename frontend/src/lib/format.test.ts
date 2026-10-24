import { describe, expect, it } from 'vitest'

import {
  chanceText,
  confidenceWord,
  daysWord,
  dm,
  dmLong,
  dmyLong,
  isWeekend,
  pct,
  plural,
  rub,
  rubApprox,
  rubAxis,
  wdm,
} from './format'

// Intl отдаёт неразрывные пробелы (U+00A0, U+202F): в тестах сравниваем с обычными
const SPACES = new RegExp(`[${String.fromCharCode(0xa0)}${String.fromCharCode(0x202f)}]`, 'g')
const flat = (s: string) => s.replace(SPACES, ' ')

describe('форматирование', () => {
  it('рубли без копеек, настоящий минус', () => {
    expect(flat(rub(416000))).toBe('416 000 ₽')
    expect(flat(rub(-1234.6))).toBe('−1 235 ₽')
    expect(flat(rub(-0.2))).toBe('0 ₽')
  })

  it('суммы-оценки округляются', () => {
    expect(flat(rubApprox(415914.59))).toBe('около 416 000 ₽')
    expect(flat(rubApprox(4210))).toBe('около 4 200 ₽')
    expect(flat(rubApprox(40))).toBe('около 100 ₽')
  })

  it('подписи осей компактные', () => {
    expect(rubAxis(0)).toBe('0')
    expect(rubAxis(-400000)).toBe('−400 тыс')
    expect(rubAxis(1_250_000)).toBe('1,3 млн')
    expect(rubAxis(12_000_000)).toBe('12 млн')
  })

  it('даты по-русски', () => {
    expect(dm('2026-08-25')).toBe('25 авг')
    expect(wdm('2026-08-10')).toBe('пн 10 авг')
    expect(dmLong('2026-08-25')).toBe('25 августа')
    expect(dmyLong('2026-07-31')).toBe('31 июля 2026')
    expect(isWeekend('2026-08-01')).toBe(true)
    expect(isWeekend('2026-08-03')).toBe(false)
  })

  it('склонение', () => {
    expect([1, 2, 5, 11, 21, 22, 25].map((n) => plural(n, ['день', 'дня', 'дней']))).toEqual([
      'день', 'дня', 'дней', 'дней', 'день', 'дня', 'дней',
    ])
    expect(daysWord(3)).toBe('3 дня')
  })

  it('шанс словами (словарь §9.3)', () => {
    expect(pct(0.64)).toBe('64%')
    expect(chanceText(0.64)).toBe('Шанс — высокий (64%)')
    expect(chanceText(0.05)).toBe('Шанс — низкий (5%)')
  })

  it('уверенность в событии', () => {
    expect([0.97, 0.7, 0.3].map(confidenceWord)).toEqual(['точно', 'вероятно', 'возможно'])
  })
})
