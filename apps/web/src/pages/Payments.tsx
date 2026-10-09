import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, Button, Card, Empty, ErrorText, Loading, Mono, PageHeader, PaymentStatusBadge, Table, Td } from '../components/ui'
import { date, money, shortHash } from '../format'

export function Payments() {
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const payments = useQuery({ queryKey: ['payments'], queryFn: api.payments })
  const simulate = useMutation({
    mutationFn: api.simulateSettlement,
    onSuccess: () => queryClient.invalidateQueries(),
  })

  return (
    <>
      <PageHeader
        title="Payments"
        subtitle="Approved invoices waiting to settle, and the onchain proof for the ones that have."
      />
      <Card>
        {payments.isPending ? (
          <Loading />
        ) : payments.error ? (
          <ErrorText error={payments.error} />
        ) : payments.data.length === 0 ? (
          <Empty>No payments yet. Approving a matched invoice schedules one.</Empty>
        ) : (
          <Table head={['Vendor', 'Amount', 'To', 'Scheduled', 'Status', 'Proof', '']}>
            {payments.data.map((p) => (
              <tr key={p.id}>
                <Td>
                  <Link to={`/invoices/${p.invoice_id}`} className="font-medium hover:text-indigo-700">{p.vendor_name}</Link>
                </Td>
                <Td className="tabular-nums whitespace-nowrap">{money(p.amount, p.currency)}</Td>
                <Td><Mono title={p.to_address}>{shortHash(p.to_address)}</Mono></Td>
                <Td className="whitespace-nowrap">{date(p.scheduled_for)}</Td>
                <Td>
                  <PaymentStatusBadge status={p.status} />
                </Td>
                <Td>
                  {p.tx_hash ? (
                    <span className="flex items-center gap-2">
                      <Mono title={p.tx_hash}>{shortHash(p.tx_hash)}</Mono>
                      <Badge tone={p.chain === 'simulated' ? 'amber' : 'green'}>{p.chain}</Badge>
                    </span>
                  ) : (
                    '—'
                  )}
                </Td>
                <Td>
                  {p.status === 'pending' && can('admin') && (
                    <Button variant="secondary" onClick={() => simulate.mutate(p.id)} loading={simulate.isPending && simulate.variables === p.id}>
                      Simulate
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
        )}
        <ErrorText error={simulate.error} />
      </Card>
      <p className="mt-3 text-xs text-slate-500">
        "Simulate" settles a payment in the books without moving funds and labels it <em>simulated</em>. Real payments are
        sent on Arc by the settlement-service.
      </p>
    </>
  )
}
