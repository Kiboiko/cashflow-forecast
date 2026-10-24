import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <div className="mx-auto max-w-md px-4 py-20 text-center">
      <p className="text-5xl font-semibold text-ink">404</p>
      <p className="mt-3 text-ink-2">Такой страницы нет.</p>
      <Link to="/" className="mt-5 inline-block font-medium text-accent-ink underline underline-offset-2">
        На главную
      </Link>
    </div>
  )
}
