import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api } from '../api'
import { ActorBadge, Badge, Card, Empty, ErrorText, Loading, Mono, PageHeader, Table, Td } from '../components/ui'
import { dateTime, humanize, money, shortHash } from '../format'

export function Ledger() {
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const entries = useQuery({ queryKey: ['entries'], queryFn: api.entries })
  const decisions = useQuery({ queryKey: ['decisions'], queryFn: api.decisions })
  const chain = useQuery({ queryKey: ['decisions-verify'], queryFn: api.verifyDecisions })

  return (
    <>
      <PageHeader
        title="Ledger & audit"
        subtitle="Double-entry books the database won't let anyone edit, and a hash-chained record of every decision."
      />
      <div className="space-y-6">
        <Card title="Account balances">
          {accounts.isPending ? (
            <Loading />
          ) : accounts.error ? (
            <ErrorText error={accounts.error} />
          ) : (
            <Table head={['Code', 'Account', 'Type', 'Balance']}>
              {accounts.data.map((a) => (
                <tr key={`${a.code}-${a.currency}`}>
                  <Td><Mono>{a.code}</Mono></Td>
                  <Td>{a.name}</Td>
                  <Td className="text-slate-500">{a.type}</Td>
                  <Td className="text-right tabular-nums">{a.currency ? money(a.balance, a.currency) : '—'}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>

        <Card title="Journal entries">
          {entries.isPending ? (
            <Loading />
          ) : entries.error ? (
            <ErrorText error={entries.error} />
          ) : entries.data.length === 0 ? (
            <Empty>No entries yet. Approving an invoice posts the first one.</Empty>
          ) : (
            <ul className="divide-y divide-slate-100">
              {entries.data.map((e) => (
                <li key={e.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium">{e.memo}</span>
                    {e.tx_hash && (
                      <>
                        <Mono title={e.tx_hash}>{shortHash(e.tx_hash)}</Mono>
                        <Badge tone={e.chain === 'simulated' ? 'amber' : 'green'}>{e.chain}</Badge>
                      </>
                    )}
                    <span className="ml-auto text-xs text-slate-500">{dateTime(e.created_at)}</span>
                  </div>
                  <table className="mt-1 w-full max-w-xl table-fixed text-xs">
                    <thead className="sr-only">
                      <tr><th>Account</th><th>Debit</th><th>Credit</th><th>Currency</th></tr>
                    </thead>
                    <tbody>
                      {[...e.postings].sort((a, b) => Number(b.amount) - Number(a.amount)).map((p, i) => {
                        const amount = Number(p.amount)
                        return (
                          <tr key={i}>
                            <td className={`w-64 truncate py-0.5 pr-4 ${amount < 0 ? 'pl-6' : ''}`}>
                              {p.account_code} {p.account_name}
                            </td>
                            <td className="w-28 pr-4 text-right tabular-nums">{amount > 0 ? money(amount) : ''}</td>
                            <td className="w-28 pr-4 text-right tabular-nums">{amount < 0 ? money(-amount) : ''}</td>
                            <td className="w-12 text-slate-500">{p.currency}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Decision log"
          actions={
            chain.data &&
            (chain.data.valid ? (
              <Badge tone="green">chain intact · {chain.data.count} records</Badge>
            ) : (
              <Badge tone="red">chain broken at #{chain.data.broken_at_seq}</Badge>
            ))
          }
        >
          {decisions.isPending ? (
            <Loading />
          ) : decisions.error ? (
            <ErrorText error={decisions.error} />
          ) : (
            <Table head={['#', 'When', 'Actor', 'Action', 'Summary', 'Hash']}>
              {decisions.data.map((d) => (
                <tr key={d.seq}>
                  <Td className="text-slate-400">{d.seq}</Td>
                  <Td className="whitespace-nowrap text-slate-500">{dateTime(d.created_at)}</Td>
                  <Td>
                    <div className="flex items-center gap-2">
                      <ActorBadge type={d.actor_type} />
                      <span className="max-w-40 truncate text-xs text-slate-500" title={d.actor_id}>{d.actor_id}</span>
                    </div>
                  </Td>
                  <Td className="whitespace-nowrap">
                    {humanize(d.action)} {d.is_override && <Badge tone="amber">override</Badge>}
                  </Td>
                  <Td>
                    {d.invoice_id ? (
                      <Link to={`/invoices/${d.invoice_id}`} className="hover:text-indigo-700">{d.summary}</Link>
                    ) : (
                      d.summary
                    )}
                  </Td>
                  <Td><Mono title={d.hash}>{shortHash(d.hash, 4)}</Mono></Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </>
  )
}
