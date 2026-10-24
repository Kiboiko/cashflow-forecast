import dayjs from 'dayjs'

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const MONTHS_GEN = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]
const MONTHS_NOM = [
  'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
]
const WEEKDAYS_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']

const rubFormat = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 0,
})
const numFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 })

/** Разбор даты ГГГГ-ММ-ДД без сдвига по часовому поясу. */
export function parseDate(iso: string): dayjs.Dayjs {
  return dayjs(iso)
}

/** «416 000 ₽». Минус — настоящий (−), чтобы не слипался с текстом. */
export function rub(value: number): string {
  const text = rubFormat.format(Math.round(Math.abs(value)))
  return value < 0 && Math.round(Math.abs(value)) !== 0 ? `−${text}` : text
}

/** «около 416 000 ₽»: сумма-оценка округляется до тысяч (до сотен, если она меньше десяти тысяч). */
export function rubApprox(value: number): string {
  const abs = Math.abs(value)
  const unit = abs >= 10_000 ? 1_000 : 100
  return `около ${rub(Math.sign(value) * Math.max(unit, Math.round(abs / unit) * unit))}`
}

export function num(value: number): string {
  return numFormat.format(Math.round(value))
}

/** Для осей графика: «−400 тыс», «0», «1,2 млн». */
export function rubAxis(value: number): string {
  const abs = Math.abs(value)
  const sign = value < 0 ? '−' : ''
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1).replace('.', ',')} млн`
  if (abs >= 1_000) return `${sign}${Math.round(abs / 1_000)} тыс`
  return `${sign}${Math.round(abs)}`
}

/** «25 авг» */
export function dm(iso: string): string {
  const d = parseDate(iso)
  return `${d.date()} ${MONTHS_SHORT[d.month()]}`
}

/** «пн 10 авг» */
export function wdm(iso: string): string {
  const d = parseDate(iso)
  return `${WEEKDAYS_SHORT[d.day()]} ${d.date()} ${MONTHS_SHORT[d.month()]}`
}

/** «25 августа» */
export function dmLong(iso: string): string {
  const d = parseDate(iso)
  return `${d.date()} ${MONTHS_GEN[d.month()]}`
}

/** «31 июля 2026» */
export function dmyLong(iso: string): string {
  const d = parseDate(iso)
  return `${d.date()} ${MONTHS_GEN[d.month()]} ${d.year()}`
}

/** «август 2026» */
export function monthYear(iso: string): string {
  const d = parseDate(iso)
  return `${MONTHS_NOM[d.month()]} ${d.year()}`
}

/** «10.08.2026» */
export function dmyShort(iso: string): string {
  return parseDate(iso).format('DD.MM.YYYY')
}

export function weekdayShort(iso: string): string {
  return WEEKDAYS_SHORT[parseDate(iso).day()]
}

export function isWeekend(iso: string): boolean {
  const day = parseDate(iso).day()
  return day === 0 || day === 6
}

/** Доля 0..1 → «64%». */
export function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** Выбор формы слова: plural(5, ['день', 'дня', 'дней']) → «дней». */
export function plural(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(Math.trunc(n))
  const mod10 = abs % 10
  const mod100 = abs % 100
  if (mod10 === 1 && mod100 !== 11) return forms[0]
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1]
  return forms[2]
}

export const daysWord = (n: number): string => `${n} ${plural(n, ['день', 'дня', 'дней'])}`

/** Уровень шанса словами (словарь §9.3): «Шанс — высокий (64%)». */
export function chanceWords(p: number): string {
  if (p >= 0.4) return 'высокий'
  if (p >= 0.1) return 'заметный'
  return 'низкий'
}

export function chanceText(p: number): string {
  return `Шанс — ${chanceWords(p)} (${pct(p)})`
}

/** Уверенность в событии: «точно / вероятно / возможно». */
export function confidenceWord(probability: number): 'точно' | 'вероятно' | 'возможно' {
  if (probability >= 0.9) return 'точно'
  if (probability >= 0.6) return 'вероятно'
  return 'возможно'
}
