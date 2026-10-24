import { copy } from '../../lib/copy'
import { wdm } from '../../lib/format'
import type { TooltipContent } from './model'

const COLOR = { accent: 'var(--accent)', series2: 'var(--series-2)' } as const

type Style = Partial<CSSStyleDeclaration>

function node(tag: string, style: Style, text?: string): HTMLElement {
  const element = document.createElement(tag)
  Object.assign(element.style, style)
  // Подписи приходят из данных (имена контрагентов) — только textContent, никакого innerHTML.
  if (text !== undefined) element.textContent = text
  return element
}

function key(kind: 'line' | 'band' | 'text', color?: 'accent' | 'series2'): HTMLElement {
  if (kind === 'text') return node('span', { width: '14px', flex: 'none' })
  const paint = COLOR[color ?? 'accent']
  if (kind === 'line') return node('span', { width: '14px', height: '3px', borderRadius: '2px', background: paint, flex: 'none' })
  return node('span', { width: '14px', height: '8px', borderRadius: '2px', background: paint, opacity: '0.25', flex: 'none' })
}

function row(keyEl: HTMLElement, value: string, label: string): HTMLElement {
  const line = node('div', { display: 'flex', alignItems: 'baseline', gap: '8px', padding: '2px 0' })
  keyEl.style.alignSelf = 'center'
  line.append(
    keyEl,
    node('span', { fontWeight: '600', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }, value),
    node('span', { color: 'var(--ink-2)' }, label),
  )
  return line
}

/** Подсказка графика: значения крупнее подписей, ключи-линии вместо квадратов. */
export function tooltipElement(content: TooltipContent): HTMLElement {
  const root = node('div', {
    padding: '10px 12px',
    minWidth: '210px',
    maxWidth: '300px',
    font: '13px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif',
    color: 'var(--ink)',
  })
  const head = node('div', { display: 'flex', justifyContent: 'space-between', gap: '12px', marginBottom: '4px' })
  head.append(node('span', { fontWeight: '600' }, wdm(content.date)))
  if (content.nonWorking) head.append(node('span', { color: 'var(--ink-2)' }, copy.chart.weekend.toLowerCase()))
  root.append(head)

  for (const r of content.rows) root.append(row(key(r.kind, r.color), r.value, r.label))

  if (content.events.length) {
    const box = node('div', { marginTop: '6px', paddingTop: '6px', borderTop: '1px solid var(--line)' })
    for (const e of content.events) {
      const glyph = node('span', { width: '14px', flex: 'none', textAlign: 'center', fontSize: '10px', color: 'var(--ink)' }, e.direction === 'out' ? '▼' : '▲')
      box.append(row(glyph, e.value, e.label))
    }
    root.append(box)
  }
  return root
}
