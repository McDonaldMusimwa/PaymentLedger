import { NavLink, Navigate, Outlet } from 'react-router'
import { auth, useAuth } from '../auth'
import { Loading } from './ui'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/invoices', label: 'Invoices' },
  { to: '/purchase-orders', label: 'Purchase orders' },
  { to: '/vendors', label: 'Vendors' },
  { to: '/payments', label: 'Payments' },
  { to: '/ledger', label: 'Ledger & audit' },
]

export function Layout() {
  const { user, ready } = useAuth()
  if (!ready) return <Loading />
  if (!user) return <Navigate to="/login" replace />

  return (
    <div className="min-h-screen lg:flex">
      <aside className="border-b border-slate-200 bg-white lg:sticky lg:top-0 lg:flex lg:h-screen lg:w-60 lg:flex-col lg:border-r lg:border-b-0">
        <div className="flex items-center gap-2 px-5 py-4">
          <img src="/favicon.svg" alt="" className="size-7" />
          <div>
            <div className="text-sm font-semibold">PaymentLedger</div>
            <div className="text-xs text-slate-500">{user.orgName}</div>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-1 lg:flex-col lg:overflow-visible">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `rounded-md px-3 py-2 text-sm whitespace-nowrap ${
                  isActive ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="hidden border-t border-slate-100 px-5 py-4 text-xs lg:block">
          <div className="truncate font-medium text-slate-700">{user.email}</div>
          <div className="flex items-center justify-between text-slate-500">
            <span>{user.role.replace('_', ' ')}</span>
            <button onClick={() => auth.logout()} className="text-slate-500 hover:text-slate-900">
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-8">
        <Outlet />
      </main>
    </div>
  )
}
