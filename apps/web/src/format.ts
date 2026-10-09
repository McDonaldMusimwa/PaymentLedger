export function money(value: string | number | null | undefined, currency?: string | null) {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  const formatted = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return currency ? `${formatted} ${currency}` : formatted
}

export function qty(value: string | number) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 })
}

export function date(value: string | null | undefined) {
  if (!value) return '—'
  // Plain dates (YYYY-MM-DD) are calendar dates; don't shift them by timezone.
  const d = value.length === 10 ? new Date(`${value}T00:00:00`) : new Date(value)
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function dateTime(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function shortHash(value: string | null | undefined, size = 6) {
  if (!value) return '—'
  return value.length > size * 2 + 3 ? `${value.slice(0, size + 2)}…${value.slice(-size)}` : value
}

export function humanize(value: string) {
  return value.replaceAll('_', ' ').replaceAll('.', ' · ')
}
