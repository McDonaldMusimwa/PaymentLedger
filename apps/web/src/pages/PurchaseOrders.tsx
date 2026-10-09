import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type Currency } from '../api'
import { useAuth } from '../auth'
import { Badge, Button, Card, Empty, ErrorText, Field, Input, Loading, Mono, PageHeader, Select, Table, Td } from '../components/ui'
import { date, dateTime, money, qty } from '../format'

type LineDraft = { sku: string; description: string; quantity: string; unit_price: string }
const blankLine = (): LineDraft => ({ sku: '', description: '', quantity: '1', unit_price: '0' })

function NewPurchaseOrder({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const vendors = useQuery({ queryKey: ['vendors'], queryFn: api.vendors })
  const [vendorId, setVendorId] = useState('')
  const [number, setNumber] = useState('')
  const [currency, setCurrency] = useState<Currency>('USDC')
  const [lines, setLines] = useState<LineDraft[]>([blankLine()])
  const create = useMutation({
    mutationFn: () =>
      api.createPurchaseOrder({
        vendor_id: vendorId,
        number,
        currency,
        lines: lines.map((l) => ({ ...l, sku: l.sku || null })),
      }),
    onSuccess: (po) => {
      queryClient.invalidateQueries({ queryKey: ['purchase-orders'] })
      navigate(`/purchase-orders/${po.id}`)
    },
  })
  const setLine = (i: number, patch: Partial<LineDraft>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }

  return (
    <Card title="New purchase order" className="mb-6">
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Vendor">
            <Select
              required
              value={vendorId}
              onChange={(e) => {
                setVendorId(e.target.value)
                const v = vendors.data?.find((x) => x.id === e.target.value)
                if (v) setCurrency(v.default_currency)
              }}
            >
              <option value="">Choose…</option>
              {vendors.data?.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </Select>
          </Field>
          <Field label="PO number">
            <Input required placeholder="PO-1005" value={number} onChange={(e) => setNumber(e.target.value)} />
          </Field>
          <Field label="Currency">
            <Select value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}>
              <option value="USDC">USDC</option>
              <option value="EURC">EURC</option>
            </Select>
          </Field>
        </div>
        {lines.map((line, i) => (
          <div key={i} className="grid grid-cols-2 items-end gap-3 sm:grid-cols-[7rem_1fr_6rem_7rem_auto]">
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
            <Button type="button" variant="ghost" disabled={lines.length === 1} onClick={() => setLines(lines.filter((_, j) => j !== i))} aria-label="Remove line">
              ✕
            </Button>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" onClick={() => setLines([...lines, blankLine()])}>Add line</Button>
          <Button type="submit" loading={create.isPending}>Create PO</Button>
          <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
        <ErrorText error={create.error} />
      </form>
    </Card>
  )
}

export function PurchaseOrders() {
  const { can } = useAuth()
  const [adding, setAdding] = useState(false)
  const pos = useQuery({ queryKey: ['purchase-orders'], queryFn: api.purchaseOrders })

  return (
    <>
      <PageHeader
        title="Purchase orders"
        subtitle="What you agreed to buy. Invoices are matched against these and the goods actually received."
        actions={can('admin', 'ap_clerk') && !adding && <Button onClick={() => setAdding(true)}>New PO</Button>}
      />
      {adding && <NewPurchaseOrder onDone={() => setAdding(false)} />}
      <Card>
        {pos.isPending ? (
          <Loading />
        ) : pos.error ? (
          <ErrorText error={pos.error} />
        ) : pos.data.length === 0 ? (
          <Empty>No purchase orders yet.</Empty>
        ) : (
          <Table head={['PO', 'Vendor', 'Raised', 'Total', 'Status']}>
            {pos.data.map((po) => (
              <tr key={po.id} className="hover:bg-slate-50">
                <Td>
                  <Link to={`/purchase-orders/${po.id}`} className="font-medium hover:text-indigo-700">{po.number}</Link>
                </Td>
                <Td>{po.vendor_name}</Td>
                <Td>{date(po.created_at)}</Td>
                <Td className="tabular-nums">{money(po.total, po.currency)}</Td>
                <Td><Badge>{po.status}</Badge></Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  )
}

export function PurchaseOrderDetail() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const po = useQuery({ queryKey: ['purchase-order', id], queryFn: () => api.purchaseOrder(id) })
  const [receiving, setReceiving] = useState<Record<string, string>>({})
  const [note, setNote] = useState('')
  const receive = useMutation({
    mutationFn: () =>
      api.receiveGoods(id, {
        note: note || null,
        lines: Object.entries(receiving)
          .filter(([, q]) => Number(q) > 0)
          .map(([po_line_id, quantity_received]) => ({ po_line_id, quantity_received })),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(['purchase-order', id], data)
      // A receipt re-runs the match on invoices waiting on this PO.
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoice'] })
      setReceiving({})
      setNote('')
    },
  })

  if (po.isPending) return <Loading />
  if (po.error) return <ErrorText error={po.error} />
  const p = po.data
  const anyQty = Object.values(receiving).some((q) => Number(q) > 0)

  return (
    <>
      <PageHeader
        title={`${p.number} · ${p.vendor_name}`}
        subtitle={`${money(p.total, p.currency)} · raised ${date(p.created_at)}`}
        actions={<Link to="/purchase-orders" className="text-sm text-slate-500 hover:text-slate-900">← All POs</Link>}
      />
      <div className="space-y-6">
        <Card title="Lines and goods received">
          <Table head={['#', 'Item', 'Ordered', 'Unit price', 'Received', ...(can('admin', 'ap_clerk') ? ['Receive now'] : [])]}>
            {p.lines.map((line) => {
              const outstanding = Number(line.quantity) - Number(line.received)
              return (
                <tr key={line.id}>
                  <Td className="text-slate-400">{line.line_no}</Td>
                  <Td>
                    <div>{line.description}</div>
                    {line.sku && <Mono>{line.sku}</Mono>}
                  </Td>
                  <Td className="tabular-nums">{qty(line.quantity)}</Td>
                  <Td className="tabular-nums">{money(line.unit_price)}</Td>
                  <Td className="tabular-nums">
                    {qty(line.received)}
                    {outstanding > 0 && <span className="ml-2 text-xs text-amber-700">{qty(outstanding)} outstanding</span>}
                  </Td>
                  {can('admin', 'ap_clerk') && (
                    <Td>
                      <Input
                        type="number"
                        min="0"
                        step="any"
                        className="w-24"
                        placeholder="0"
                        value={receiving[line.id] ?? ''}
                        onChange={(e) => setReceiving({ ...receiving, [line.id]: e.target.value })}
                      />
                    </Td>
                  )}
                </tr>
              )
            })}
          </Table>
          {can('admin', 'ap_clerk') && (
            <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-4">
              <div className="min-w-64 flex-1">
                <Field label="Receipt note">
                  <Input placeholder="e.g. Delivery note DN-4471, 2 pallets damaged" value={note} onChange={(e) => setNote(e.target.value)} />
                </Field>
              </div>
              <Button onClick={() => receive.mutate()} loading={receive.isPending} disabled={!anyQty}>
                Record goods receipt
              </Button>
            </div>
          )}
          <ErrorText error={receive.error} />
        </Card>

        <Card title="Receipts">
          {p.receipts.length === 0 ? (
            <Empty>Nothing received yet. Invoices against this PO will fail the quantity check until goods arrive.</Empty>
          ) : (
            <ul className="divide-y divide-slate-100">
              {p.receipts.map((r) => (
                <li key={r.id} className="py-2 text-sm">
                  <span className="font-medium">{dateTime(r.received_at)}</span>
                  {r.note && <span className="text-slate-500"> · {r.note}</span>}
                  <div className="text-xs text-slate-500">
                    {r.lines
                      .map((l) => {
                        const line = p.lines.find((x) => x.id === l.po_line_id)
                        return `${qty(l.quantity_received)} × ${line?.sku ?? line?.description ?? 'line'}`
                      })
                      .join(', ')}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}
