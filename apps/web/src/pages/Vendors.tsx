import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, type Currency, type Vendor } from '../api'
import { useAuth } from '../auth'
import { Badge, Button, Card, Empty, ErrorText, Field, Input, Loading, Mono, PageHeader, Select, Table, Td } from '../components/ui'
import { dateTime } from '../format'

const WALLET_RE = /^0x[0-9a-fA-F]{40}$/
const HOLD_HOURS = 72

function recentlyChanged(v: Vendor) {
  return !!v.wallet_updated_at && Date.now() - new Date(v.wallet_updated_at).getTime() < HOLD_HOURS * 3600_000
}

function NewVendor({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ name: '', email: '', default_currency: 'USDC' as Currency, wallet_address: '' })
  const create = useMutation({
    mutationFn: () =>
      api.createVendor({ ...form, email: form.email || null, wallet_address: form.wallet_address || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vendors'] })
      onDone()
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }
  return (
    <Card title="New vendor" className="mb-6">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">
          <Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus />
        </Field>
        <Field label="Billing email">
          <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
        <Field label="Pays in">
          <Select value={form.default_currency} onChange={(e) => setForm({ ...form, default_currency: e.target.value as Currency })}>
            <option value="USDC">USDC</option>
            <option value="EURC">EURC</option>
          </Select>
        </Field>
        <Field label="Payout wallet (Arc / EVM address)">
          <Input
            placeholder="0x…"
            pattern={WALLET_RE.source}
            value={form.wallet_address}
            onChange={(e) => setForm({ ...form, wallet_address: e.target.value.trim() })}
          />
        </Field>
        <div className="flex gap-2 sm:col-span-2">
          <Button type="submit" loading={create.isPending}>Add vendor</Button>
          <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
        <div className="sm:col-span-2">
          <ErrorText error={create.error} />
        </div>
      </form>
    </Card>
  )
}

function WalletCell({ vendor, editable }: { vendor: Vendor; editable: boolean }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(vendor.wallet_address ?? '')
  const update = useMutation({
    mutationFn: () => api.updateVendor(vendor.id, { wallet_address: value || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vendors'] })
      setEditing(false)
    },
  })

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {vendor.wallet_address ? <Mono>{vendor.wallet_address}</Mono> : <Badge tone="red">no wallet</Badge>}
        {recentlyChanged(vendor) && <Badge tone="amber">changed {dateTime(vendor.wallet_updated_at!)}</Badge>}
        {editable && (
          <button className="text-xs text-indigo-600" onClick={() => setEditing(true)}>
            change
          </button>
        )}
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <Input value={value} onChange={(e) => setValue(e.target.value.trim())} placeholder="0x…" className="font-mono text-xs" />
      <p className="text-xs text-amber-700">
        Payments to a changed wallet are held for review for {HOLD_HOURS}h. Confirm the change with the vendor by phone
        first; a wallet-change email is a common invoice-fraud trick.
      </p>
      <div className="flex gap-2">
        <Button onClick={() => update.mutate()} loading={update.isPending} disabled={!!value && !WALLET_RE.test(value)}>
          Save wallet
        </Button>
        <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
      </div>
      <ErrorText error={update.error} />
    </div>
  )
}

export function Vendors() {
  const { can } = useAuth()
  const [adding, setAdding] = useState(false)
  const vendors = useQuery({ queryKey: ['vendors'], queryFn: api.vendors })
  const editable = can('admin', 'ap_clerk')

  return (
    <>
      <PageHeader
        title="Vendors"
        subtitle="Who you pay and where. A vendor needs a payout wallet before any invoice can be paid."
        actions={editable && !adding && <Button onClick={() => setAdding(true)}>Add vendor</Button>}
      />
      {adding && <NewVendor onDone={() => setAdding(false)} />}
      <Card>
        {vendors.isPending ? (
          <Loading />
        ) : vendors.error ? (
          <ErrorText error={vendors.error} />
        ) : vendors.data.length === 0 ? (
          <Empty>No vendors yet.</Empty>
        ) : (
          <Table head={['Vendor', 'Currency', 'Payout wallet']}>
            {vendors.data.map((v) => (
              <tr key={v.id}>
                <Td>
                  <div className="font-medium">{v.name}</div>
                  <div className="text-xs text-slate-500">{v.email}</div>
                </Td>
                <Td>{v.default_currency}</Td>
                <Td>
                  <WalletCell vendor={v} editable={editable} />
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  )
}
