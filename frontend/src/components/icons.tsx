import type { ReactNode, SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function make(children: ReactNode) {
  return function Icon({ size = 20, ...rest }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
        {...rest}
      >
        {children}
      </svg>
    )
  }
}

export const IconCheckCircle = make(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="m8.5 12.5 2.5 2.5 4.5-5.5" />
  </>,
)
export const IconAlertCircle = make(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v5.5M12 16.4v.1" />
  </>,
)
export const IconAlertTriangle = make(
  <>
    <path d="M10.3 4.2 2.7 17.4a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9.5v4.5M12 17.4v.1" />
  </>,
)
export const IconArrowDownRight = make(<path d="m7 7 10 10M17 8.5V17H8.5" />)
export const IconArrowUpRight = make(<path d="M7 17 17 7M8.5 7H17v8.5" />)
export const IconPlus = make(<path d="M12 5v14M5 12h14" />)
export const IconX = make(<path d="m6 6 12 12M18 6 6 18" />)
export const IconChevronDown = make(<path d="m6 9 6 6 6-6" />)
export const IconInfo = make(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 7.6v.1" />
  </>,
)
export const IconHome = make(<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1v-7.5Z" />)
export const IconList = make(<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.1M4.5 12h.1M4.5 18h.1" />)
export const IconSliders = make(<path d="M5 6h9M18 6h1M5 12h2M11 12h8M5 18h11M20 18h-1M14 4v4M9 10v4M16 16v4" />)
export const IconCompare = make(
  <>
    <path d="M4 18V9M9 18V5M14 18v-7M19 18V8" />
  </>,
)
export const IconTable = make(
  <>
    <rect x="4" y="5" width="16" height="14" rx="2" />
    <path d="M4 10h16M10 10v9" />
  </>,
)
export const IconBook = make(<path d="M5 5.5A1.5 1.5 0 0 1 6.5 4H19v14H6.5A1.5 1.5 0 0 0 5 19.5v-14ZM5 19.5A1.5 1.5 0 0 0 6.5 21H19" />)
export const IconSettings = make(
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M18.4 5.6l-1.8 1.8M7.4 16.6l-1.8 1.8" />
  </>,
)
export const IconLogout = make(<path d="M14 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H14M10 12h10M17 8.5l3.5 3.5-3.5 3.5" />)
export const IconClock = make(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5V12l3 2" />
  </>,
)
export const IconUpload = make(<path d="M12 16V5M7.5 9.5 12 5l4.5 4.5M5 15v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />)
export const IconSearch = make(
  <>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4 4" />
  </>,
)
export const IconRadar = make(
  <>
    <path d="M4.5 17.5 9.5 11l3.5 3 6-8" />
    <path d="M4.5 20h15" />
  </>,
)
export const IconBell = make(<path d="M6 9.5a6 6 0 1 1 12 0c0 4.2 1.5 5.5 2 6.5H4c.5-1 2-2.3 2-6.5ZM10 19a2 2 0 0 0 4 0" />)
export const IconSparkle =make(<path d="M12 4v4M12 16v4M4 12h4M16 12h4M7 7l2 2M15 15l2 2M17 7l-2 2M9 15l-2 2" />)
