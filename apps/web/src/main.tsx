import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { AuthProvider } from './auth'
import { Layout } from './components/Layout'
import './index.css'
import { AuthPage } from './pages/AuthPage'
import { Dashboard } from './pages/Dashboard'
import { InvoiceDetail } from './pages/InvoiceDetail'
import { InvoiceForm } from './pages/InvoiceForm'
import { Invoices } from './pages/Invoices'
import { Ledger } from './pages/Ledger'
import { Payments } from './pages/Payments'
import { PurchaseOrderDetail, PurchaseOrders } from './pages/PurchaseOrders'
import { Vendors } from './pages/Vendors'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1 } },
})

const router = createBrowserRouter([
  { path: '/login', element: <AuthPage mode="login" /> },
  { path: '/register', element: <AuthPage mode="register" /> },
  {
    element: <Layout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'invoices', element: <Invoices /> },
      { path: 'invoices/new', element: <InvoiceForm /> },
      { path: 'invoices/:id', element: <InvoiceDetail /> },
      { path: 'invoices/:id/edit', element: <InvoiceForm /> },
      { path: 'purchase-orders', element: <PurchaseOrders /> },
      { path: 'purchase-orders/:id', element: <PurchaseOrderDetail /> },
      { path: 'vendors', element: <Vendors /> },
      { path: 'payments', element: <Payments /> },
      { path: 'ledger', element: <Ledger /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
