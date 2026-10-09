import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type Currency, type InvoiceDetail, type InvoiceFields } from '../api'
import { Button, Card, ErrorText, Field, Input, Loading, PageHeader, Select } from '../components/ui'
import { money } from '../format'

type LineDraft = { sku: string; description: string; quantity: string; unit_price: string }

const blankLine = (): LineDraft => ({ sku: '', description: '', quantity: '1', unit_price: '0' })

const lineAmount = (l: LineDraft) => (Number(l.quantity) || 0) * (Number(l.unit_price) || 0)

/** Prefill from what's already known: the stored fields, else the raw extraction. */
function initialDraft(invoice?: InvoiceDetail) {
  const ex = (invoice?.extraction ?? {}) as Record<string, any>
  return {
    vendor_name: invoice?.vendor_name ?? ex.vendor_name ?? '',
    invoice_number: invoice?.invoice_number ?? ex.invoice_number ?? '',
    po_number: invoice?.po_number ?? ex.po_number ?? '',
    invoice_date: invoice?.invoice_date ?? '',
    due_date: invoice?.due_date ?? '',
    payment_terms: invoice?.payment_terms ?? '',
    currency: (invoice?.currency === 'EURC' ? 'EURC' : 'USDC') as Currency,
    tax: invoice?.tax ?? '0',
    total: invoice?.total ?? '',
    lines: invoice?.lines.length
      ? invoice.lines.map((l) => ({ sku: l.sku ?? '', description: l.description, quantity: l.quantity, unit_price: l.unit_price }))
      : [blankLine()],
  }
}

function Form({ invoice }: { invoice?: InvoiceDetail }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const vendors = useQuery({ queryKey: ['vendors'], queryFn: api.vendors })
  const pos = useQuery({ queryKey: ['purchase-orders'], queryFn: api.purchaseOrders })
  const [draft, setDraft] = useState(() => initialDraft(invoice))
  const [totalTouched, setTotalTouched] = useState(!!invoice?.total)

  const computedTotal = draft.lines.reduce((sum, l) => sum + lineAmount(l), 0) + (Number(draft.tax) || 0)
  const total = totalTouched ? draft.total : computedTotal.toFixed(2)

  const save = useMutation({
    mutationFn: (body: InvoiceFields) => (invoice ? api.updateInvoiceFields(invoice.id, body) : api.createInvoice(body)),
    onSuccess: (saved) => {
      queryClient.setQueryData(['invoice', saved.id], saved)
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      navigate(`/invoices/${saved.id}`)
    },
  })

  const set = (patch: Partial<typeof draft>) => setDraft((d) => ({ ...d, ...patch }))
  const setLine = (i: number, patch: Partial<LineDraft>) =>
    set({ lines: draft.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate({
      vendor_name: draft.vendor_name,
      invoice_number: draft.invoice_number,
      po_number: draft.po_number || null,
      invoice_date: draft.invoice_date || null,
      due_date: draft.due_date || null,
      payment_terms: draft.payment_terms || null,
      currency: draft.currency,
      tax: String(draft.tax || 0),
      total: String(total),
      lines: draft.lines.map((l) => ({
        sku: l.sku || null,
        description: l.description,
        quantity: l.quantity,
        unit_price: l.unit_price,
        amount: lineAmount(l).toFixed(6),
      })),
    })
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card title="Header">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Vendor (as printed)">
            <Input required list="vendor-names" value={draft.vendor_name} onChange={(e) => set({ vendor_name: e.target.value })} />
            <datalist id="vendor-names">
              {vendors.data?.map((v) => <option key={v.id} value={v.name} />)}
            </datalist>
          </Field>
          <Field label="Invoice number">
            <Input required value={draft.invoice_number} onChange={(e) => set({ invoice_number: e.target.value })} />
          </Field>
          <Field label="PO number">
            <Input list="po-numbers" value={draft.po_number} onChange={(e) => set({ po_number: e.target.value })} />
            <datalist id="po-numbers">
              {pos.data?.map((p) => <option key={p.id} value={p.number}>{p.vendor_name}</option>)}
            </datalist>
          </Field>
          <Field label="Invoice date">
            <Input type="date" value={draft.invoice_date} onChange={(e) => set({ invoice_date: e.target.value })} />
          </Field>
          <Field label="Due date">
            <Input type="date" value={draft.due_date} onChange={(e) => set({ due_date: e.target.value })} />
          </Field>
          <Field label="Payment terms">
            <Input placeholder="Net 30" value={draft.payment_terms} onChange={(e) => set({ payment_terms: e.target.value })} />
          </Field>
          <Field label="Currency">
            <Select value={draft.currency} onChange={(e) => set({ currency: e.target.value as Currency })}>
              <option value="USDC">USDC</option>
              <option value="EURC">EURC</option>
            </Select>
          </Field>
        </div>
      </Card>

      <Card title="Lines" actions={<Button type="button" variant="secondary" onClick={() => set({ lines: [...draft.lines, blankLine()] })}>Add line</Button>}>
        <div className="space-y-3">
          {draft.lines.map((line, i) => (
            <div key={i} className="grid grid-cols-2 items-end gap-3 sm:grid-cols-[7rem_1fr_6rem_7rem_7rem_auto]">
              <Field label="SKU">
                <Input value={line.sku} onChange={(e) => setLine(i, { sku: e.target.value })} />
              </Field>
              <Field label="Description">
                <Input required value={line.description} onChange={(e) => setLine(i, { description: e.target.value })} />
              </Field>
              <Field label="Qty">
                <Input required type="number" min="0.0001" step="any" value={line.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} />
              </Field>
              <Field label="Unit price">
                <Input required type="number" min="0" step="any" value={line.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} />
              </Field>
              <div className="pb-2 text-right text-sm tabular-nums">{money(lineAmount(line))}</div>
              <Button
                type="button"
                variant="ghost"
                disabled={draft.lines.length === 1}
                onClick={() => set({ lines: draft.lines.filter((_, j) => j !== i) })}
                aria-label="Remove line"
              >
                ✕
              </Button>
            </div>
          ))}
        </div>
        <div className="mt-6 grid max-w-sm gap-3 sm:ml-auto sm:grid-cols-2">
          <Field label="Tax">
            <Input type="number" min="0" step="any" value={draft.tax} onChange={(e) => set({ tax: e.target.value })} />
          </Field>
          <Field label="Total (as printed)" hint={totalTouched ? `Lines + tax = ${money(computedTotal)}` : 'Lines + tax'}>
            <Input
              required
              type="number"
              min="0"
              step="any"
              value={total}
              onChange={(e) => {
                setTotalTouched(true)
                set({ total: e.target.value })
              }}
            />
          </Field>
        </div>
      </Card>

      <ErrorText error={save.error} />
      <div className="flex gap-2">
        <Button type="submit" loading={save.isPending}>
          {invoice ? 'Save and re-match' : 'Save and match'}
        </Button>
        <Link to={invoice ? `/invoices/${invoice.id}` : '/invoices'}>
          <Button type="button" variant="ghost">Cancel</Button>
        </Link>
      </div>
    </form>
  )
}

export function InvoiceForm() {
  const { id } = useParams()
  const invoice = useQuery({ queryKey: ['invoice', id], queryFn: () => api.invoice(id!), enabled: !!id })

  return (
    <>
      <PageHeader
        title={id ? 'Correct invoice fields' : 'Enter an invoice'}
        subtitle="Type values exactly as printed on the invoice. The match runs when you save."
      />
      {id && invoice.isPending ? <Loading /> : <Form invoice={invoice.data} />}
    </>
  )
}
