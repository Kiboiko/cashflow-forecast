import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import {
  useCategories,
  useCategorySummary,
  useImportStatement,
  usePatchTransaction,
  useTransactions,
} from '../api/hooks'
import type { CategoryInfo, ImportResult, TransactionOut } from '../api/types'
import { useCurrentBusiness } from '../auth'
import { Check, FileButton, SelectInput, TextInput } from '../components/forms'
import { IconAlertCircle, IconArrowDownRight, IconArrowUpRight, IconSearch, IconUpload } from '../components/icons'
import { Modal } from '../components/Modal'
import { Badge, Button, Card, EmptyState, ErrorBlock, SectionTitle, Spinner, Stat, Tabs, cx } from '../components/ui'
import { copy } from '../lib/copy'
import { dm, num, pct, plural, rub, weekdayShort } from '../lib/format'
import { useDebounced } from '../lib/hooks'

const PAGE_SIZE = 50

/* ---------- сводка импорта ---------- */
function ImportSummary({ result, onReview }: { result: ImportResult; onReview: () => void }) {
  const dups = result.duplicates_removed
  const gaps = result.data_gaps.length
  return (
    <Card className="border-accent/40 bg-accent-soft">
      <p className="font-medium text-ink">
        Загрузили {num(result.added)} {plural(result.added, ['операцию', 'операции', 'операций'])}: убрали {dups}{' '}
        {plural(dups, ['дубль', 'дубля', 'дублей'])}, нашли {gaps} {plural(gaps, ['пропуск', 'пропуска', 'пропусков'])} данных,
        распознали {(result.categorized_share * 100).toFixed(1).replace('.', ',')}%.
      </p>
      {result.needs_review > 0 && (
        <Button size="sm" className="mt-3" onClick={onReview}>
          {copy.operations.review(result.needs_review)}
        </Button>
      )}
    </Card>
  )
}

/* ---------- окно «уточнить категорию» ---------- */
function groupCategories(categories: CategoryInfo[]): Array<[string, CategoryInfo[]]> {
  const groups = new Map<string, CategoryInfo[]>()
  for (const category of categories) {
    if (category.code.startsWith('unknown')) continue
    groups.set(category.group, [...(groups.get(category.group) ?? []), category])
  }
  return [...groups.entries()]
}

function EditCategory({ bid, tx, categories, onClose, onDone }: { bid: string; tx: TransactionOut; categories: CategoryInfo[]; onClose: () => void; onDone: (message: string) => void }) {
  const patch = usePatchTransaction(bid)
  const [category, setCategory] = useState(tx.category && !tx.category.startsWith('unknown') ? tx.category : '')
  const who = tx.counterparty_name ?? tx.counterparty_inn
  const [remember, setRemember] = useState(Boolean(who))
  const grouped = useMemo(() => groupCategories(categories), [categories])
  const error = patch.error instanceof ApiError ? patch.error.message : null

  const save = () =>
    patch.mutate(
      { id: tx.id, category, apply_to_counterparty: remember },
      { onSuccess: (result) => onDone(copy.operations.saved(result.updated)) },
    )

  return (
    <Modal
      title={copy.operations.editTitle}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{copy.common.cancel}</Button>
          <Button variant="primary" disabled={!category || patch.isPending} onClick={save}>
            {copy.common.save}
          </Button>
        </>
      }
    >
      <div className="mb-4 rounded-xl bg-surface-2 p-3 text-sm">
        <div className="flex justify-between gap-3">
          <span className="font-medium text-ink">{tx.counterparty_name ?? 'Без названия'}</span>
          <span className="tnum whitespace-nowrap font-semibold">
            {tx.direction === 'out' ? '−' : '+'}
            {rub(tx.amount)}
          </span>
        </div>
        {tx.purpose && <p className="mt-1 text-ink-2">{tx.purpose}</p>}
      </div>
      <div className="grid gap-3">
        <label className="grid gap-1 text-sm font-medium text-ink-2">
          {copy.operations.category}
          <SelectInput value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="" disabled>
              Выберите…
            </option>
            {grouped.map(([group, items]) => (
              <optgroup key={group} label={group[0].toUpperCase() + group.slice(1)}>
                {items.map((item) => (
                  <option key={item.code} value={item.code}>
                    {item.title}
                  </option>
                ))}
              </optgroup>
            ))}
          </SelectInput>
        </label>
        <Check checked={remember} onChange={setRemember}>
          {who ? copy.operations.remember(who) : copy.operations.rememberAnon}
        </Check>
        {error && (
          <p role="alert" className="text-sm text-crit-ink">
            {error}
          </p>
        )}
      </div>
    </Modal>
  )
}

/* ---------- строка операции ---------- */
function TransactionRow({ tx, onEdit }: { tx: TransactionOut; onEdit: () => void }) {
  const out = tx.direction === 'out'
  const dim = tx.flags.includes('duplicate')
  return (
    <li>
      <button
        type="button"
        onClick={onEdit}
        className={cx('flex w-full items-start gap-3 px-1 py-3 text-left hover:bg-surface-2 sm:px-3', dim && 'opacity-60')}
        aria-label={`${tx.counterparty_name ?? 'Операция'}, ${rub(tx.amount)}. ${copy.operations.edit}`}
      >
        <div className="w-14 flex-none text-sm text-ink-2">
          <div className="font-medium text-ink">{dm(tx.date)}</div>
          <div>{weekdayShort(tx.date)}</div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-ink">{tx.counterparty_name ?? 'Без названия'}</div>
          {tx.purpose && <div className="line-clamp-2 text-sm text-ink-2">{tx.purpose}</div>}
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {tx.needs_review ? (
              <Badge tone="warn">
                <IconAlertCircle size={14} />
                {copy.operations.sources.unknown}
              </Badge>
            ) : (
              <>
                <Badge>{tx.category_title ?? tx.category}</Badge>
                {tx.category_source && <span className="self-center text-xs text-ink-2">{copy.operations.sources[tx.category_source]}</span>}
              </>
            )}
            {tx.flags.map((flag) => (
              <Badge key={flag} tone="neutral">
                {copy.operations.flags[flag] ?? flag}
              </Badge>
            ))}
          </div>
        </div>
        <div className={cx('flex items-center gap-1 whitespace-nowrap text-right font-semibold tnum', out ? 'text-ink' : 'text-good-ink')}>
          {out ? <IconArrowDownRight size={16} className="text-ink-2" /> : <IconArrowUpRight size={16} />}
          {out ? '−' : '+'}
          {rub(tx.amount)}
        </div>
      </button>
    </li>
  )
}

/* ---------- «куда уходят деньги» ---------- */
function Summary({ bid }: { bid: string }) {
  const summary = useCategorySummary(bid, 12)
  if (summary.isPending) return <Spinner />
  if (summary.isError) return <ErrorBlock onRetry={() => summary.refetch()} />
  const data = summary.data
  const section = (direction: 'in' | 'out', title: string) => {
    const rows = data.rows.filter((r) => r.direction === direction && r.total > 0)
    const total = direction === 'in' ? data.inflow_total : data.outflow_total
    const max = Math.max(...rows.map((r) => r.total), 1)
    return (
      <Card>
        <SectionTitle>{title}</SectionTitle>
        <p className="-mt-2 mb-3 text-sm text-ink-2">
          Всего {rub(total)} {copy.operations.summarySub(data.months.length)}
        </p>
        {rows.length === 0 ? (
          <p className="text-ink-2">{copy.common.noData}</p>
        ) : (
          <ul className="space-y-3">
            {rows.map((row) => (
              <li key={row.category}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="font-medium text-ink">{row.title}</span>
                  <span className="tnum whitespace-nowrap text-ink">
                    {rub(row.total)} <span className="text-ink-2">· {pct(row.total / Math.max(total, 1))}</span>
                  </span>
                </div>
                <div className="mt-1.5 h-2 rounded-r-[4px] bg-surface-2" aria-hidden="true">
                  <div className="h-full rounded-r-[4px] bg-accent" style={{ width: `${Math.max((row.total / max) * 100, 1.5)}%` }} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    )
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat label={copy.operations.summaryIn} value={rub(data.inflow_total)} sub={copy.operations.summarySub(data.months.length)} tone="good" />
        <Stat label={copy.operations.summaryOut} value={rub(data.outflow_total)} sub={copy.operations.summarySub(data.months.length)} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {section('out', 'На что уходят деньги')}
        {section('in', 'Откуда приходят деньги')}
      </div>
    </div>
  )
}

/* ---------- страница ---------- */
export default function OperationsPage() {
  const { business } = useCurrentBusiness()
  const bid = business?.id
  const [search, setSearch] = useSearchParams()
  const review = search.get('review') === '1'
  const [tab, setTab] = useState<'list' | 'summary'>('list')
  const [text, setText] = useState('')
  const [category, setCategory] = useState('')
  const [page, setPage] = useState(0)
  const [editing, setEditing] = useState<TransactionOut | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [imported, setImported] = useState<ImportResult | null>(null)
  const q = useDebounced(text, 300)

  const categories = useCategories()
  const list = useTransactions(bid, { q, category, review, limit: PAGE_SIZE, offset: page * PAGE_SIZE })
  const upload = useImportStatement(bid ?? '')
  const uploadError = upload.error instanceof ApiError ? upload.error.message : null

  const setReview = (value: boolean) => {
    setPage(0)
    setSearch(value ? { review: '1' } : {}, { replace: true })
  }

  if (!bid) return <Spinner />
  const total = list.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const reviewCount = list.data?.needs_review_count ?? 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-xl font-semibold text-ink sm:text-2xl">{copy.operations.title}</h1>
        <FileButton
          label={upload.isPending ? copy.common.loading : copy.operations.import}
          accept=".csv,text/csv,.txt"
          disabled={upload.isPending}
          icon={<IconUpload size={18} />}
          onFile={(file) => upload.mutate(file, { onSuccess: (result) => { setImported(result); setNotice(null) } })}
        />
      </div>
      <p className="-mt-2 text-sm text-ink-2">{copy.operations.importHint}</p>
      {uploadError && (
        <p role="alert" className="rounded-xl bg-crit-soft px-3.5 py-2.5 text-sm text-crit-ink">
          {uploadError}
        </p>
      )}
      {imported && <ImportSummary result={imported} onReview={() => { setReview(true); setTab('list') }} />}
      {notice && (
        <p role="status" className="rounded-xl bg-good-soft px-3.5 py-2.5 text-sm text-good-ink">
          {notice}
        </p>
      )}

      <Tabs
        label="Раздел операций"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'list', label: copy.operations.tabList },
          { value: 'summary', label: copy.operations.tabSummary },
        ]}
      />

      {tab === 'summary' ? (
        <Summary bid={bid} />
      ) : (
        <>
          <div className="grid gap-2 sm:grid-cols-[1fr_16rem]">
            <div className="relative">
              <IconSearch size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-2" />
              <TextInput
                type="search"
                value={text}
                onChange={(e) => { setText(e.target.value); setPage(0) }}
                placeholder={copy.operations.search}
                aria-label={copy.operations.search}
                className="pl-10"
              />
            </div>
            <SelectInput value={category} onChange={(e) => { setCategory(e.target.value); setPage(0) }} aria-label={copy.operations.category}>
              <option value="">{copy.operations.allCategories}</option>
              {(categories.data ?? []).map((c) => (
                <option key={c.code} value={c.code}>
                  {c.title}
                </option>
              ))}
            </SelectInput>
          </div>

          {(reviewCount > 0 || review) && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant={review ? 'primary' : 'secondary'} onClick={() => setReview(!review)} aria-pressed={review}>
                <IconAlertCircle size={16} />
                {copy.operations.review(reviewCount)}
              </Button>
              {review && (
                <Button size="sm" variant="ghost" onClick={() => setReview(false)}>
                  {copy.operations.reviewOff}
                </Button>
              )}
            </div>
          )}

          <Card className="p-0 sm:p-0">
            {list.isPending ? (
              <Spinner />
            ) : list.isError ? (
              <ErrorBlock message={list.error instanceof ApiError && list.error.status === 409 ? copy.operations.noData : undefined} onRetry={() => list.refetch()} />
            ) : list.data.items.length === 0 ? (
              <div className="p-4">
                <EmptyState title={copy.operations.empty} text={copy.operations.emptyHint} />
              </div>
            ) : (
              <ul className={cx('divide-y divide-line px-3 transition-opacity sm:px-0', list.isPlaceholderData && 'opacity-60')}>
                {list.data.items.map((tx) => (
                  <TransactionRow key={tx.id} tx={tx} onEdit={() => setEditing(tx)} />
                ))}
              </ul>
            )}
          </Card>

          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-3">
              <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                {copy.operations.prev}
              </Button>
              <span className="text-sm text-ink-2">
                {page + 1} из {pages} · всего {num(total)}
              </span>
              <Button size="sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
                {copy.operations.next}
              </Button>
            </div>
          )}
        </>
      )}

      {editing && categories.data && (
        <EditCategory
          bid={bid}
          tx={editing}
          categories={categories.data}
          onClose={() => setEditing(null)}
          onDone={(message) => {
            setEditing(null)
            setNotice(message)
          }}
        />
      )}
    </div>
  )
}
