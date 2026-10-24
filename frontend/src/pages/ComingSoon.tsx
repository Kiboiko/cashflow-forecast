import { EmptyState } from '../components/ui'

export default function ComingSoon({ title }: { title: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-ink sm:text-2xl">{title}</h1>
      <EmptyState title="Этот раздел появится в следующем шаге" />
    </div>
  )
}
