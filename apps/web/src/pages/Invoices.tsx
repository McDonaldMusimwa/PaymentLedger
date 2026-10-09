import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState, type DragEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api, type InvoiceStatus } from '../api'
import { useAuth } from '../auth'
import { Button, Card, Empty, ErrorText, InvoiceStatusBadge, Loading, PageHeader, Table, Td } from '../components/ui'
import { date, money } from '../format'

const TABS: { label: string; status?: InvoiceStatus }[] = [
  { label: 'All' },
  { label: 'Exceptions', status: 'exception' },
  { label: 'Matched', status: 'matched' },
  { label: 'Approved', status: 'approved' },
  { label: 'Paid', status: 'paid' },
  { label: 'Received', status: 'received' },
  { label: 'Rejected', status: 'rejected' },
]

function UploadZone() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const upload = useMutation({
    mutationFn: api.uploadInvoice,
    onSuccess: (invoice) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      navigate(`/invoices/${invoice.id}`)
    },
  })

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) upload.mutate(file)
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={`mb-6 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
        dragging ? 'border-indigo-400 bg-indigo-50' : 'border-slate-300 bg-white'
      }`}
    >
      <input
        ref={input}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])}
      />
      <p className="text-sm text-slate-600">
        Drop a vendor invoice PDF here, or{' '}
        <button className="font-medium text-indigo-600" onClick={() => input.current?.click()} disabled={upload.isPending}>
          choose a file
        </button>
        . It's read, matched against the PO and goods received, and queued for a decision.
      </p>
      {upload.isPending && <p className="mt-2 text-sm text-slate-500">Uploading…</p>}
      <div className="mx-auto mt-3 max-w-md">
        <ErrorText error={upload.error} />
      </div>
    </div>
  )
}

export function Invoices() {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const status = (params.get('status') as InvoiceStatus | null) ?? undefined
  const invoices = useQuery({ queryKey: ['invoices', status ?? 'all'], queryFn: () => api.invoices(status) })

  return (
    <>
      <PageHeader
        title="Invoices"
        actions={
          can('admin', 'ap_clerk') && (
            <Link to="/invoices/new">
              <Button variant="secondary">Enter manually</Button>
            </Link>
          )
        }
      />
      {can('admin', 'ap_clerk') && <UploadZone />}

      <div className="mb-4 flex gap-1 overflow-x-auto">
        {TABS.map((tab) => (
          <button
            key={tab.label}
            onClick={() => setParams(tab.status ? { status: tab.status } : {})}
            className={`rounded-md px-3 py-1.5 text-sm whitespace-nowrap ${
              status === tab.status ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <Card>
        {invoices.isPending ? (
          <Loading />
        ) : invoices.error ? (
          <ErrorText error={invoices.error} />
        ) : invoices.data.length === 0 ? (
          <Empty>No invoices here yet.</Empty>
        ) : (
          <Table head={['Vendor', 'Invoice', 'PO', 'Due', 'Total', 'Status']}>
            {invoices.data.map((inv) => (
              <tr key={inv.id} className="hover:bg-slate-50">
                <Td>
                  <Link to={`/invoices/${inv.id}`} className="font-medium text-slate-900 hover:text-indigo-700">
                    {inv.vendor_name ?? inv.file_name ?? 'Reading…'}
                  </Link>
                </Td>
                <Td>{inv.invoice_number ?? '—'}</Td>
                <Td>{inv.po_number ?? '—'}</Td>
                <Td className="whitespace-nowrap">{date(inv.due_date)}</Td>
                <Td className="text-right whitespace-nowrap tabular-nums">{money(inv.total, inv.currency)}</Td>
                <Td>
                  <InvoiceStatusBadge status={inv.status} />
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  )
}
