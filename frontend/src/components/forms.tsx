import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'

import { cx } from './ui'

const CONTROL = 'min-h-11 w-full rounded-xl border border-line-2 bg-surface px-3 text-base text-ink placeholder:text-ink-3 disabled:opacity-60'

/** Поле формы: подпись над контролом, подсказка под ним. */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: string
  error?: string | null
  children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className="grid gap-1">
      <label htmlFor={id} className="text-sm font-medium text-ink-2">
        {label}
      </label>
      {children(id)}
      {hint && !error && <p className="text-[13px] text-ink-2">{hint}</p>}
      {error && (
        <p role="alert" className="text-[13px] text-crit-ink">
          {error}
        </p>
      )}
    </div>
  )
}

export function TextInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(CONTROL, className)} {...rest} />
}

export function SelectInput({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(CONTROL, className)} {...rest}>
      {children}
    </select>
  )
}

/** Чекбокс с крупной областью нажатия. */
export function Check({ checked, onChange, children }: { checked: boolean; onChange: (value: boolean) => void; children: ReactNode }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-xl py-2 text-[15px] text-ink">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 h-5 w-5 flex-none accent-[var(--accent)]" />
      <span>{children}</span>
    </label>
  )
}

/** Кнопка выбора файла: настоящий input скрыт, но доступен с клавиатуры. */
export function FileButton({
  label,
  accept,
  onFile,
  disabled,
  variant = 'secondary',
  icon,
}: {
  label: string
  accept: string
  onFile: (file: File) => void
  disabled?: boolean
  variant?: 'primary' | 'secondary'
  icon?: ReactNode
}) {
  const id = useId()
  return (
    <>
      <input
        id={id}
        type="file"
        accept={accept}
        className="sr-only peer"
        disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) onFile(file)
          e.target.value = ''
        }}
      />
      <label
        htmlFor={id}
        className={cx(
          'inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-xl px-4 text-[15px] font-medium transition peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent peer-disabled:cursor-not-allowed peer-disabled:opacity-50',
          variant === 'primary' ? 'bg-accent text-white hover:brightness-110' : 'border border-line-2 bg-surface text-ink hover:bg-surface-2',
        )}
      >
        {icon}
        {label}
      </label>
    </>
  )
}
