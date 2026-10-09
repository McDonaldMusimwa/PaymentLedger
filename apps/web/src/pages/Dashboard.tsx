import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api } from '../api'
import { useAuth } from '../auth'
import { ActorBadge, Button, Card, Empty, ErrorText, InvoiceStatusBadge, Loading, PageHeader } from '../components/ui'
import { dateTime, money } from '../format'

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-slate-200">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function Dashboard() {
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats })
  const exceptions = useQuery({ queryKey: ['invoices', 'exception'], queryFn: () => api.invoices('exception') })
  const ready = useQuery({ queryKey: ['invoices', 'matched'], queryFn: () => api.invoices('matched') })
  const decisions = useQuery({ queryKey: ['decisions'], queryFn: api.decisions })
  const seed = useMutation({ mutationFn: api.seedDemo, onSuccess: () => queryClient.invalidateQueries() })

  if (stats.isPending) return <Loading />
  if (stats.error) return <ErrorText error={stats.error} />
  const s = stats.data
  const paid = s.payments_settled.map((p) => money(p.amount, p.currency)).join(' · ') || money(0)
  const outstanding = s.payments_outstanding.map((p) => money(p.amount, p.currency)).join(' · ') || 'nothing scheduled'

  return (
    <>
      <PageHeader title="Overview" subtitle="Invoices in, matched against POs and receipts, paid in USDC on Arc." />

      {s.vendors === 0 && (
        <Card className="mb-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="font-medium">Start with your vendors and purchase orders</div>
              <p className="text-sm text-slate-500">
                Add them by hand, or load a demo set: 3 vendors, 4 POs with goods receipts, and 5 invoices that show a
                clean match, a price variance, a short shipment with a changed wallet, and a duplicate.
              </p>
            </div>
            <div className="flex gap-2">
              <Link to="/vendors">
                <Button variant="secondary">Add vendors</Button>
              </Link>
              {can('admin') && (
                <Button onClick={() => seed.mutate()} loading={seed.isPending}>
                  Load demo data
                </Button>
              )}
            </div>
          </div>
          <ErrorText error={seed.error} />
        </Card>
      )}

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Invoices processed" value={s.invoices_total} sub={`${s.invoices_matched_clean} matched with no exceptions`} />
        <Stat label="Waiting on review" value={s.invoices_by_status.exception} sub={`${s.invoices_by_status.matched} matched, ready to approve`} />
        <Stat label="Paid" value={s.invoices_by_status.paid} sub={paid} />
        <Stat label="Scheduled to pay" value={s.invoices_by_status.approved} sub={outstanding} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Needs attention" actions={<Link to="/invoices" className="text-xs text-indigo-600">All invoices</Link>}>
          {[...(exceptions.data ?? []), ...(ready.data ?? [])].length === 0 ? (
            <Empty>Nothing waiting. New invoices land here when they need a decision.</Empty>
          ) : (
            <ul className="-my-2 divide-y divide-slate-100">
              {[...(exceptions.data ?? []), ...(ready.data ?? [])].map((inv) => (
                <li key={inv.id}>
                  <Link to={`/invoices/${inv.id}`} className="flex items-center justify-between gap-4 py-2 hover:text-indigo-700">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">
                        {inv.vendor_name ?? inv.file_name ?? 'Unknown vendor'} · {inv.invoice_number ?? '—'}
                      </div>
                      <div className="text-xs text-slate-500">{money(inv.total, inv.currency)}</div>
                    </div>
                    <InvoiceStatusBadge status={inv.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Recent decisions" actions={<Link to="/ledger" className="text-xs text-indigo-600">Audit trail</Link>}>
          {decisions.data?.length ? (
            <ul className="-my-2 divide-y divide-slate-100">
              {decisions.data.slice(0, 8).map((d) => (
                <li key={d.seq} className="py-2 text-sm">
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <ActorBadge type={d.actor_type} />
                    <span className="truncate">{d.actor_id}</span>
                    <span className="ml-auto whitespace-nowrap">{dateTime(d.created_at)}</span>
                  </div>
                  <div className="mt-1 text-slate-700">
                    {d.invoice_id ? (
                      <Link to={`/invoices/${d.invoice_id}`} className="hover:text-indigo-700">
                        {d.summary}
                      </Link>
                    ) : (
                      d.summary
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>Every extraction, match, approval and payment is logged here.</Empty>
          )}
        </Card>
      </div>
    </>
  )
}
