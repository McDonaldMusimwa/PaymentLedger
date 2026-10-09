import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, type Check, type InvoiceDetail as Detail } from '../api'
import { useAuth } from '../auth'
import {
  ActorBadge,
  Badge,
  Button,
  Card,
  ErrorText,
  InvoiceStatusBadge,
  Loading,
  Mono,
  PageHeader,
  PaymentStatusBadge,
  Spinner,
  Table,
  Td,
} from '../components/ui'
import { date, dateTime, humanize, money, qty, shortHash } from '../format'

const CHECK_STYLE: Record<Check['status'], { icon: string; className: string }> = {
  pass: { icon: '✓', className: 'text-emerald-600' },
  warn: { icon: '!', className: 'text-amber-600' },
  fail: { icon: '✕', className: 'text-rose-600' },
}

function usePdfUrl(invoiceId: string, hasFile: boolean) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!hasFile) return
    let objectUrl: string | null = null
    api.invoiceFile(invoiceId).then((blob) => {
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    })
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [invoiceId, hasFile])
  return url
}

function MatchChecks({ invoice }: { invoice: Detail }) {
  if (!invoice.match) return <p className="text-sm text-slate-500">Not matched yet.</p>
  // Most important first: failures, then warnings, then passes.
  const order = { fail: 0, warn: 1, pass: 2 }
  return (
    <ul className="space-y-1.5">
      {[...invoice.match.checks]
        .sort((a, b) => order[a.status] - order[b.status])
        .map((c, i) => (
          <li key={i} className="flex gap-2 text-sm">
            <span className={`w-4 shrink-0 text-center font-bold ${CHECK_STYLE[c.status].className}`}>{CHECK_STYLE[c.status].icon}</span>
            <span className={c.status === 'pass' ? 'text-slate-600' : 'text-slate-900'}>
              {c.line_no !== null && <span className="text-slate-400">Line {c.line_no}: </span>}
              {c.message}
            </span>
          </li>
        ))}
    </ul>
  )
}

function Actions({ invoice }: { invoice: Detail }) {
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const [override, setOverride] = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [mode, setMode] = useState<'none' | 'override' | 'reject'>('none')

  const onSuccess = (data: Detail) => {
    queryClient.setQueryData(['invoice', invoice.id], data)
    queryClient.invalidateQueries({ queryKey: ['invoices'] })
    queryClient.invalidateQueries({ queryKey: ['stats'] })
    setMode('none')
  }
  const approve = useMutation({ mutationFn: (reason?: string) => api.approve(invoice.id, reason), onSuccess })
  const reject = useMutation({ mutationFn: () => api.reject(invoice.id, rejectReason), onSuccess })
  const rematch = useMutation({ mutationFn: () => api.rematch(invoice.id), onSuccess })

  const decidable = invoice.status === 'matched' || invoice.status === 'exception'
  if (!decidable) return null
  const approver = can('admin', 'approver')

  return (
    <Card title="Decision">
      <div className="space-y-3">
        {invoice.status === 'matched' ? (
          <p className="text-sm text-slate-600">
            Invoice, PO and goods received agree. Approving accrues it to Accounts Payable and schedules payment of{' '}
            <strong>{money(invoice.total, invoice.currency)}</strong> for {date(invoice.due_date)}.
          </p>
        ) : (
          <p className="text-sm text-slate-600">
            The match found problems. Fix the data (correct the fields, record a goods receipt) and re-run the match, or
            approve with a written override that goes on the audit trail.
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          {approver && invoice.status === 'matched' && (
            <Button onClick={() => approve.mutate(undefined)} loading={approve.isPending}>
              Approve & schedule payment
            </Button>
          )}
          {approver && invoice.status === 'exception' && (
            <Button variant="secondary" onClick={() => setMode(mode === 'override' ? 'none' : 'override')}>
              Approve with override…
            </Button>
          )}
          {approver && (
            <Button variant="danger" onClick={() => setMode(mode === 'reject' ? 'none' : 'reject')}>
              Reject…
            </Button>
          )}
          {can('admin', 'ap_clerk') && (
            <>
              <Button variant="ghost" onClick={() => rematch.mutate()} loading={rematch.isPending}>
                Re-run match
              </Button>
              <Link to={`/invoices/${invoice.id}/edit`}>
                <Button variant="ghost">Correct fields</Button>
              </Link>
            </>
          )}
        </div>

        {mode === 'override' && (
          <div className="space-y-2">
            <textarea
              value={override}
              onChange={(e) => setOverride(e.target.value)}
              rows={3}
              placeholder="Why is it right to pay despite the exceptions? e.g. Price increase confirmed by email from vendor on Oct 8."
              className="block w-full rounded-md px-3 py-2 text-sm ring-1 ring-slate-300 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            />
            <Button onClick={() => approve.mutate(override)} loading={approve.isPending} disabled={override.trim().length < 5}>
              Approve with this override
            </Button>
          </div>
        )}
        {mode === 'reject' && (
          <div className="space-y-2">
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              rows={2}
              placeholder="Reason, e.g. Duplicate of NW-5521 already paid."
              className="block w-full rounded-md px-3 py-2 text-sm ring-1 ring-slate-300 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            />
            <Button variant="danger" onClick={() => reject.mutate()} loading={reject.isPending} disabled={rejectReason.trim().length < 3}>
              Reject invoice
            </Button>
          </div>
        )}
        <ErrorText error={approve.error ?? reject.error ?? rematch.error} />
      </div>
    </Card>
  )
}

export function InvoiceDetail() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can } = useAuth()
  const query = useQuery({
    queryKey: ['invoice', id],
    queryFn: () => api.invoice(id),
    // Poll while extraction runs in the background.
    refetchInterval: (q) => (q.state.data?.status === 'received' && !q.state.data.extraction_error ? 2000 : false),
  })
  const pdfUrl = usePdfUrl(id, !!query.data?.file_name)
  const retry = useMutation({
    mutationFn: () => api.retryExtraction(id),
    onSuccess: (data) => queryClient.setQueryData(['invoice', id], data),
  })
  const simulate = useMutation({
    mutationFn: (paymentId: string) => api.simulateSettlement(paymentId),
    onSuccess: () => queryClient.invalidateQueries(),
  })

  if (query.isPending) return <Loading />
  if (query.error) return <ErrorText error={query.error} />
  const inv = query.data
  const reading = inv.status === 'received' && !inv.extraction_error
  const hasSidePanel = !!inv.file_name || !!inv.extraction
  const lineChecks = (lineNo: number) => inv.match?.checks.filter((c) => c.line_no === lineNo && c.status !== 'pass') ?? []

  return (
    <>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            {inv.vendor_name ?? inv.file_name ?? 'Invoice'} {inv.invoice_number && <span className="text-slate-400">#{inv.invoice_number}</span>}
            <InvoiceStatusBadge status={inv.status} />
          </span>
        }
        subtitle={
          <>
            {money(inv.total, inv.currency)} · PO {inv.po_number ?? '—'} · due {date(inv.due_date)}
            {inv.payment_terms && ` · ${inv.payment_terms}`}
          </>
        }
        actions={<Link to="/invoices" className="text-sm text-slate-500 hover:text-slate-900">← All invoices</Link>}
      />

      {reading && (
        <div className="mb-6 flex items-center gap-2 rounded-lg bg-violet-50 px-4 py-3 text-sm text-violet-800 ring-1 ring-violet-200">
          <Spinner /> Reading the invoice and matching it against the PO and goods received…
        </div>
      )}
      {inv.extraction_error && (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900 ring-1 ring-amber-200">
          <span>{inv.extraction_error}</span>
          {can('admin', 'ap_clerk') && (
            <span className="flex gap-2">
              <Button variant="secondary" onClick={() => retry.mutate()} loading={retry.isPending}>
                Retry
              </Button>
              <Link to={`/invoices/${inv.id}/edit`}>
                <Button>Enter fields</Button>
              </Link>
            </span>
          )}
        </div>
      )}

      <div className={`grid gap-6 ${hasSidePanel ? 'xl:grid-cols-5' : ''}`}>
        <div className={`space-y-6 ${hasSidePanel ? 'xl:col-span-3' : ''}`}>
          <Actions invoice={inv} />

          <Card title="3-way match" actions={inv.match && <Badge tone={inv.match.outcome === 'matched' ? 'green' : 'amber'}>{inv.match.outcome}</Badge>}>
            <MatchChecks invoice={inv} />
          </Card>

          <Card title="Lines">
            {inv.lines.length === 0 ? (
              <p className="text-sm text-slate-500">No lines yet.</p>
            ) : (
              <Table head={['#', 'Item', 'Qty', 'Unit price', 'Amount', 'PO line']}>
                {inv.lines.map((line) => (
                  <tr key={line.line_no}>
                    <Td className="text-slate-400">{line.line_no}</Td>
                    <Td>
                      <div>{line.description}</div>
                      {line.sku && <Mono>{line.sku}</Mono>}
                      {lineChecks(line.line_no).map((c, i) => (
                        <div key={i} className={`text-xs ${CHECK_STYLE[c.status].className}`}>
                          {c.message}
                        </div>
                      ))}
                    </Td>
                    <Td className="tabular-nums">{qty(line.quantity)}</Td>
                    <Td className="tabular-nums">{money(line.unit_price)}</Td>
                    <Td className="tabular-nums">{money(line.amount)}</Td>
                    <Td>{line.po_line_id ? <Badge tone="green">paired</Badge> : <Badge tone="gray">none</Badge>}</Td>
                  </tr>
                ))}
                <tr>
                  <Td />
                  <Td className="text-right text-slate-500">Tax</Td>
                  <Td />
                  <Td />
                  <Td className="tabular-nums">{money(inv.tax)}</Td>
                  <Td />
                </tr>
                <tr className="font-medium">
                  <Td />
                  <Td className="text-right">Total</Td>
                  <Td />
                  <Td />
                  <Td className="tabular-nums">{money(inv.total, inv.currency)}</Td>
                  <Td />
                </tr>
              </Table>
            )}
          </Card>

          {inv.payment && (
            <Card title="Payment" actions={<PaymentStatusBadge status={inv.payment.status} />}>
              <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
                <dt className="text-slate-500">Amount</dt>
                <dd className="font-medium">{money(inv.payment.amount, inv.payment.currency)}</dd>
                <dt className="text-slate-500">To</dt>
                <dd><Mono>{inv.payment.to_address}</Mono></dd>
                <dt className="text-slate-500">Scheduled</dt>
                <dd>{date(inv.payment.scheduled_for)}</dd>
                {inv.payment.tx_hash && (
                  <>
                    <dt className="text-slate-500">Proof</dt>
                    <dd className="flex flex-wrap items-center gap-2">
                      <Mono title={inv.payment.tx_hash}>{shortHash(inv.payment.tx_hash, 10)}</Mono>
                      <Badge tone={inv.payment.chain === 'simulated' ? 'amber' : 'green'}>{inv.payment.chain}</Badge>
                    </dd>
                  </>
                )}
              </dl>
              {inv.payment.status === 'pending' && can('admin') && (
                <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
                  <Button variant="secondary" onClick={() => simulate.mutate(inv.payment!.id)} loading={simulate.isPending}>
                    Simulate settlement
                  </Button>
                  <span className="text-xs text-slate-500">Demo only, nothing moves onchain. Arc settlement comes with the settlement-service.</span>
                </div>
              )}
              <ErrorText error={simulate.error} />
            </Card>
          )}

          <Card title="Decision history">
            <ol className="relative space-y-4 border-l border-slate-200 pl-5">
              {inv.decisions.map((d) => (
                <li key={d.seq} className="relative">
                  <span className="absolute top-1.5 -left-[25px] size-2.5 rounded-full bg-white ring-2 ring-slate-300" />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <ActorBadge type={d.actor_type} />
                    <span>{d.actor_id}</span>
                    <span>·</span>
                    <span>{humanize(d.action)}</span>
                    {d.is_override && <Badge tone="amber">override</Badge>}
                    <span className="ml-auto">{dateTime(d.created_at)}</span>
                  </div>
                  <p className="mt-1 text-sm text-slate-800">{d.summary}</p>
                  <Mono title={d.hash}>#{d.seq} · {shortHash(d.hash)}</Mono>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        {hasSidePanel && (
        <div className="space-y-6 xl:col-span-2">
          {inv.file_name && (
            <Card title={inv.file_name}>
              {pdfUrl ? (
                <iframe src={pdfUrl} title="Invoice PDF" className="h-[70vh] w-full rounded ring-1 ring-slate-200" />
              ) : (
                <Loading />
              )}
              {inv.file_sha256 && (
                <p className="mt-2 text-xs text-slate-500">
                  SHA-256 <Mono title={inv.file_sha256}>{shortHash(inv.file_sha256, 10)}</Mono>
                </p>
              )}
            </Card>
          )}
          {inv.extraction && (
            <Card title="What the AI read" actions={typeof inv.extraction.confidence === 'number' && <Badge tone="purple">{Math.round(inv.extraction.confidence * 100)}% confident</Badge>}>
              {inv.extraction.notes ? <p className="text-sm text-slate-700">{String(inv.extraction.notes)}</p> : <p className="text-sm text-slate-500">No notes.</p>}
              {inv.extraction.is_invoice === false && <p className="mt-2 text-sm font-medium text-rose-700">This document may not be an invoice.</p>}
            </Card>
          )}
        </div>
        )}
      </div>
    </>
  )
}
