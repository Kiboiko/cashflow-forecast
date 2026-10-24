import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'

import { copy } from '../lib/copy'
import { IconInfo } from './icons'

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

export type Tone = 'neutral' | 'good' | 'warn' | 'crit' | 'accent'

const TONE_BG: Record<Tone, string> = {
  neutral: 'bg-surface-2 text-ink-2',
  good: 'bg-good-soft text-good-ink',
  warn: 'bg-warn-soft text-warn-ink',
  crit: 'bg-crit-soft text-crit-ink',
  accent: 'bg-accent-soft text-accent-ink',
}

export function Card({
  children,
  className,
  as: Tag = 'section',
  ...rest
}: {
  children: ReactNode
  className?: string
  as?: 'section' | 'div' | 'article' | 'li'
} & HTMLAttributes<HTMLElement>) {
  return (
    <Tag className={cx('rounded-2xl border border-line bg-surface p-4 sm:p-5', className)} {...rest}>
      {children}
    </Tag>
  )
}

export function SectionTitle({
  children,
  hint,
  action,
  id,
}: {
  children: ReactNode
  hint?: string
  action?: ReactNode
  id?: string
}) {
  return (
    <div className="mb-3 flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
      <h2 id={id} className="flex min-w-0 items-center gap-1.5 text-base font-semibold text-ink sm:text-lg">
        {children}
        {hint && <Hint text={hint} />}
      </h2>
      {action}
    </div>
  )
}

/** Подсказка с термином: раскрывается по нажатию, работает с клавиатуры и без JS-библиотек. */
export function Hint({ text }: { text: string }) {
  return (
    <details className="group relative inline-block align-middle">
      <summary
        className="flex h-6 w-6 cursor-pointer list-none items-center justify-center rounded-full text-ink-2 hover:bg-surface-2 [&::-webkit-details-marker]:hidden"
        aria-label="Пояснение"
      >
        <IconInfo size={16} />
      </summary>
      <div className="absolute left-0 top-7 z-30 w-64 rounded-xl border border-line bg-surface p-3 text-sm font-normal text-ink-2 shadow-lg sm:w-72">
        {text}
      </div>
    </details>
  )
}

export function Badge({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium',
        TONE_BG[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'md' | 'sm'
}

const BUTTON_VARIANT: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: 'bg-accent text-white hover:brightness-110 disabled:opacity-50',
  secondary: 'border border-line-2 bg-surface text-ink hover:bg-surface-2 disabled:opacity-50',
  ghost: 'text-ink-2 hover:bg-surface-2 disabled:opacity-50',
  danger: 'bg-crit text-white hover:brightness-110 disabled:opacity-50',
}

export function Button({ variant = 'secondary', size = 'md', className, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        'inline-flex items-center justify-center gap-2 rounded-xl font-medium transition',
        size === 'md' ? 'min-h-11 px-4 text-[15px]' : 'min-h-9 px-3 text-sm',
        BUTTON_VARIANT[variant],
        className,
      )}
      {...rest}
    />
  )
}

export function Spinner({ label = copy.common.loading }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-3 py-10 text-ink-2">
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-line-2 border-t-accent" />
      <span>{label}</span>
    </div>
  )
}

export function ErrorBlock({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-2xl border border-line bg-surface p-5 text-center">
      <p className="text-ink-2">{message ?? copy.common.error}</p>
      {onRetry && (
        <Button className="mt-3" onClick={onRetry}>
          {copy.common.retry}
        </Button>
      )}
    </div>
  )
}

export function EmptyState({ title, text, action }: { title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-line-2 p-6 text-center">
      <p className="font-medium text-ink">{title}</p>
      {text && <p className="mt-1 text-sm text-ink-2">{text}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

/** Число-плитка: подпись сверху, значение крупно, пояснение снизу. */
export function Stat({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'neutral' | 'crit' | 'good'
}) {
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface p-3.5 sm:p-4">
      <div className="text-[13px] leading-tight text-ink-2">{label}</div>
      <div
        className={cx(
          'mt-1.5 text-xl font-semibold leading-tight sm:text-2xl',
          tone === 'crit' && 'text-crit-ink',
          tone === 'good' && 'text-good-ink',
        )}
      >
        {value}
      </div>
      {sub && <div className="mt-1 text-[13px] leading-tight text-ink-2">{sub}</div>}
    </div>
  )
}

/** Подпись-фильтр в одну строку (сегментированный переключатель). */
export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-xl bg-surface-2 p-1">
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx(
            'min-h-9 rounded-lg px-3 text-sm font-medium transition',
            option.value === value ? 'bg-surface text-ink shadow-sm' : 'text-ink-2 hover:text-ink',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
