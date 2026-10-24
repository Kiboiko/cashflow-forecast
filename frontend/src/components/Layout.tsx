import { useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth, useCurrentBusiness } from '../auth'
import { copy } from '../lib/copy'
import { IconBook, IconClock, IconCompare, IconHome, IconList, IconLogout, IconRadar, IconSettings, IconSliders, IconTable, IconX } from './icons'
import { cx } from './ui'

interface NavItem {
  to: string
  label: string
  Icon: typeof IconHome
  end?: boolean
}

const MAIN_NAV: NavItem[] = [
  { to: '/', label: copy.nav.home, Icon: IconHome, end: true },
  { to: '/payments', label: copy.nav.payments, Icon: IconList },
  { to: '/what-if', label: copy.nav.whatIf, Icon: IconSliders },
  { to: '/plan-fact', label: copy.nav.planFact, Icon: IconCompare },
]
const MORE_NAV: NavItem[] = [
  { to: '/operations', label: copy.nav.operations, Icon: IconTable },
  { to: '/how-it-works', label: copy.nav.how, Icon: IconBook },
  { to: '/settings', label: copy.nav.settings, Icon: IconSettings },
]

function Logo() {
  return (
    <Link to="/" className="flex flex-none items-center gap-2.5 whitespace-nowrap font-semibold text-ink" aria-label={copy.appName}>
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-white">
        <IconRadar size={20} />
      </span>
      <span className="hidden sm:inline">{copy.appName}</span>
    </Link>
  )
}

function DesktopNav() {
  return (
    <nav aria-label="Разделы" className="hidden flex-1 items-center gap-0.5 md:flex">
      {[...MAIN_NAV, ...MORE_NAV].map(({ to, label, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cx(
              'whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[15px] font-medium transition lg:px-3',
              isActive ? 'bg-accent-soft text-accent-ink' : 'text-ink-2 hover:bg-surface-2 hover:text-ink',
            )
          }
        >
          {label}
        </NavLink>
      ))}
    </nav>
  )
}

function MobileTab({ to, label, Icon, end }: NavItem) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cx(
          'flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium',
          isActive ? 'text-accent-ink' : 'text-ink-2',
        )
      }
    >
      <Icon size={22} />
      {label}
    </NavLink>
  )
}

function MoreSheet({ onClose, onLogout }: { onClose: () => void; onLogout: () => void }) {
  return (
    <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label={copy.nav.more}>
      <button type="button" className="absolute inset-0 bg-black/40" aria-label={copy.common.close} onClick={onClose} />
      <div className="absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-line bg-surface p-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)]">
        <div className="mb-1 flex items-center justify-between px-2">
          <span className="text-sm font-medium text-ink-2">{copy.nav.more}</span>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-ink-2 hover:bg-surface-2" aria-label={copy.common.close}>
            <IconX size={20} />
          </button>
        </div>
        <ul>
          {MORE_NAV.map(({ to, label, Icon }) => (
            <li key={to}>
              <NavLink to={to} onClick={onClose} className="flex min-h-12 items-center gap-3 rounded-xl px-3 text-[15px] font-medium text-ink hover:bg-surface-2">
                <Icon size={20} />
                {label}
              </NavLink>
            </li>
          ))}
          <li>
            <button type="button" onClick={onLogout} className="flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-[15px] font-medium text-ink hover:bg-surface-2">
              <IconLogout size={20} />
              {copy.common.logout}
            </button>
          </li>
        </ul>
      </div>
    </div>
  )
}

export function Layout(): ReactNode {
  const { signOut } = useAuth()
  const { user, business, businesses, select } = useCurrentBusiness()
  const [more, setMore] = useState(false)
  const location = useLocation()
  const moreActive = MORE_NAV.some((item) => location.pathname.startsWith(item.to))

  return (
    <div className="min-h-dvh bg-page pb-[calc(4.5rem+env(safe-area-inset-bottom))] md:pb-10">
      <header className="sticky top-0 z-20 border-b border-line bg-page/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-2.5">
          <Logo />
          <DesktopNav />
          <div className="ml-auto flex min-w-0 items-center gap-2 md:ml-0">
            {businesses.length > 1 ? (
              <select
                aria-label="Бизнес"
                value={business?.id}
                onChange={(e) => select(e.target.value)}
                className="max-w-[12rem] truncate rounded-lg border border-line bg-surface px-2 py-1.5 text-sm"
              >
                {businesses.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            ) : (
              <span className="max-w-[11rem] truncate text-sm text-ink-2 lg:max-w-[14rem]" title={business?.name}>
                {user?.display_name ? `${user.display_name} · ` : ''}
                {business?.name}
              </span>
            )}
            <button
              type="button"
              onClick={signOut}
              className="hidden rounded-lg p-2 text-ink-2 hover:bg-surface-2 hover:text-ink md:block"
              aria-label={copy.common.logout}
              title={copy.common.logout}
            >
              <IconLogout size={20} />
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-4 sm:py-6">
        <Outlet />
      </main>

      <nav aria-label="Разделы" className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <div className="mx-auto flex max-w-lg">
          {MAIN_NAV.map((item) => (
            <MobileTab key={item.to} {...item} />
          ))}
          <button
            type="button"
            onClick={() => setMore(true)}
            className={cx('flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium', moreActive ? 'text-accent-ink' : 'text-ink-2')}
          >
            <IconClock size={22} />
            {copy.nav.more}
          </button>
        </div>
      </nav>
      {more && (
        <MoreSheet
          onClose={() => setMore(false)}
          onLogout={() => {
            setMore(false)
            signOut()
          }}
        />
      )}
    </div>
  )
}
