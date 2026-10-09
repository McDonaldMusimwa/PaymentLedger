// HTTP client for both backends. Auth endpoints live under /auth, ledger-api under /api.
// The access token is held in memory only; the refresh token is an httpOnly cookie.

export type Role = 'admin' | 'approver' | 'ap_clerk' | 'vendor'

export interface User {
  id: string
  email: string
  role: Role
  orgId: string
  orgName: string
}

interface Session {
  accessToken: string
  user: User
}

export class ApiError extends Error {
  status: number
  code?: string
  detail?: unknown

  constructor(status: number, message: string, code?: string, detail?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.detail = detail
  }
}

let accessToken: string | null = null
let sessionListener: (user: User | null) => void = () => {}
let refreshing: Promise<boolean> | null = null

export function onSessionChange(listener: (user: User | null) => void) {
  sessionListener = listener
}

function setSession(session: Session | null) {
  accessToken = session?.accessToken ?? null
  sessionListener(session?.user ?? null)
}

const MESSAGES: Record<string, string> = {
  invalid_credentials: 'Wrong email or password.',
  email_taken: 'An account with that email already exists.',
}

async function toError(res: Response): Promise<ApiError> {
  let body: any = null
  try {
    body = await res.json()
  } catch {
    // not JSON
  }
  // auth-service: { error }, ledger-api: { detail: string | {code, message} | validation[] }
  const detail = body?.detail
  if (body?.error) return new ApiError(res.status, MESSAGES[body.error] ?? body.error, body.error, body)
  if (typeof detail === 'string') return new ApiError(res.status, detail.replaceAll('_', ' '), detail)
  if (detail?.message) return new ApiError(res.status, detail.message, detail.code, detail)
  if (Array.isArray(detail)) {
    const msg = detail.map((d: any) => `${d.loc?.slice(1).join('.')}: ${d.msg}`).join('; ')
    return new ApiError(res.status, msg, 'validation', detail)
  }
  return new ApiError(res.status, `Request failed (${res.status})`)
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('content-type', 'application/json')
  if (accessToken) headers.set('authorization', `Bearer ${accessToken}`)
  return fetch(path, { ...init, headers, credentials: 'same-origin' })
}

/** Exchanges the refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const res = await fetch('/auth/refresh', { method: 'POST', credentials: 'same-origin' })
      if (!res.ok) {
        setSession(null)
        return false
      }
      setSession(await res.json())
      return true
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await send(path, init)
  if (res.status === 401 && path.startsWith('/api') && (await refreshSession())) {
    res = await send(path, init)
  }
  if (!res.ok) throw await toError(res)
  if (res.status === 204) return undefined as T
  return res.json()
}

export async function fetchBlob(path: string): Promise<Blob> {
  let res = await send(path)
  if (res.status === 401 && (await refreshSession())) res = await send(path)
  if (!res.ok) throw await toError(res)
  return res.blob()
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const auth = {
  async login(email: string, password: string) {
    setSession(await request<Session>('/auth/login', json({ email, password })))
  },
  async register(orgName: string, email: string, password: string) {
    setSession(await request<Session>('/auth/register', json({ orgName, email, password })))
  },
  async logout() {
    await request('/auth/logout', { method: 'POST' }).catch(() => {})
    setSession(null)
  },
}

// ---- ledger-api types ----

export type Currency = 'USDC' | 'EURC'
export type InvoiceStatus = 'received' | 'extracted' | 'matched' | 'exception' | 'approved' | 'rejected' | 'paid'
export type PaymentStatus = 'pending' | 'submitted' | 'settled' | 'failed'

export interface Vendor {
  id: string
  name: string
  email: string | null
  default_currency: Currency
  wallet_address: string | null
  wallet_updated_at: string | null
  created_at: string
}

export interface POLine {
  id: string
  line_no: number
  sku: string | null
  description: string
  quantity: string
  unit_price: string
  received: string
}

export interface PurchaseOrder {
  id: string
  number: string
  currency: Currency
  status: string
  vendor_id: string
  vendor_name: string
  total: string
  created_at: string
}

export interface GoodsReceipt {
  id: string
  received_at: string
  note: string | null
  lines: { po_line_id: string; quantity_received: string }[]
}

export interface PODetail extends PurchaseOrder {
  lines: POLine[]
  receipts: GoodsReceipt[]
}

export interface Invoice {
  id: string
  status: InvoiceStatus
  vendor_id: string | null
  vendor_name: string | null
  invoice_number: string | null
  po_number: string | null
  invoice_date: string | null
  due_date: string | null
  currency: string
  total: string | null
  file_name: string | null
  extraction_error: string | null
  created_at: string
}

export interface InvoiceLine {
  line_no: number
  sku: string | null
  description: string
  quantity: string
  unit_price: string
  amount: string
  po_line_id: string | null
}

export interface Check {
  code: string
  status: 'pass' | 'warn' | 'fail'
  message: string
  line_no: number | null
}

export interface Decision {
  seq: number
  invoice_id: string | null
  actor_type: 'user' | 'system' | 'agent'
  actor_id: string
  action: string
  summary: string
  detail: Record<string, unknown>
  is_override: boolean
  created_at: string
  prev_hash: string
  hash: string
}

export interface Payment {
  id: string
  invoice_id: string
  vendor_id: string
  vendor_name: string
  amount: string
  currency: Currency
  to_address: string
  status: PaymentStatus
  scheduled_for: string
  chain: string | null
  tx_hash: string | null
  error: string | null
  created_at: string
  settled_at: string | null
}

export interface InvoiceDetail extends Invoice {
  payment_terms: string | null
  subtotal: string | null
  tax: string
  po_id: string | null
  file_sha256: string | null
  extraction: (Record<string, unknown> & { confidence?: number; notes?: string }) | null
  lines: InvoiceLine[]
  match: { outcome: 'matched' | 'exception'; checks: Check[]; created_at: string } | null
  decisions: Decision[]
  payment: Payment | null
}

export interface InvoiceFields {
  vendor_name: string
  invoice_number: string
  po_number: string | null
  invoice_date: string | null
  due_date: string | null
  payment_terms: string | null
  currency: Currency
  tax: string
  total: string
  lines: { sku: string | null; description: string; quantity: string; unit_price: string; amount: string }[]
}

export interface AccountBalance {
  code: string
  name: string
  type: string
  currency: string | null
  balance: string
}

export interface JournalEntry {
  id: string
  memo: string
  source_type: string
  source_id: string | null
  chain: string | null
  tx_hash: string | null
  created_at: string
  postings: { account_code: string; account_name: string; amount: string; currency: string }[]
}

export interface Stats {
  invoices_total: number
  invoices_by_status: Record<InvoiceStatus, number>
  invoices_matched_clean: number
  payments_settled: { currency: string; count: number; amount: number }[]
  payments_outstanding: { currency: string; amount: number }[]
  vendors: number
}

const get = <T>(path: string) => request<T>(`/api${path}`)
const post = <T>(path: string, body?: unknown) =>
  request<T>(`/api${path}`, body === undefined ? { method: 'POST' } : json(body))

export const api = {
  stats: () => get<Stats>('/stats'),
  vendors: () => get<Vendor[]>('/vendors'),
  createVendor: (body: Partial<Vendor>) => post<Vendor>('/vendors', body),
  updateVendor: (id: string, body: Partial<Vendor>) =>
    request<Vendor>(`/api/vendors/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  purchaseOrders: () => get<PurchaseOrder[]>('/purchase-orders'),
  purchaseOrder: (id: string) => get<PODetail>(`/purchase-orders/${id}`),
  createPurchaseOrder: (body: unknown) => post<PODetail>('/purchase-orders', body),
  receiveGoods: (id: string, body: unknown) => post<PODetail>(`/purchase-orders/${id}/receipts`, body),

  invoices: (status?: InvoiceStatus) => get<Invoice[]>(`/invoices${status ? `?status=${status}` : ''}`),
  invoice: (id: string) => get<InvoiceDetail>(`/invoices/${id}`),
  createInvoice: (body: InvoiceFields) => post<InvoiceDetail>('/invoices', body),
  updateInvoiceFields: (id: string, body: InvoiceFields) =>
    request<InvoiceDetail>(`/api/invoices/${id}/fields`, { method: 'PUT', body: JSON.stringify(body) }),
  uploadInvoice: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<InvoiceDetail>('/api/invoices/upload', { method: 'POST', body: form })
  },
  invoiceFile: (id: string) => fetchBlob(`/api/invoices/${id}/file`),
  retryExtraction: (id: string) => post<InvoiceDetail>(`/invoices/${id}/extract`),
  rematch: (id: string) => post<InvoiceDetail>(`/invoices/${id}/match`),
  approve: (id: string, override_reason?: string) =>
    post<InvoiceDetail>(`/invoices/${id}/approve`, { override_reason: override_reason || null }),
  reject: (id: string, reason: string) => post<InvoiceDetail>(`/invoices/${id}/reject`, { reason }),

  payments: () => get<Payment[]>('/payments'),
  accounts: () => get<AccountBalance[]>('/ledger/accounts'),
  entries: () => get<JournalEntry[]>('/ledger/entries'),
  decisions: () => get<Decision[]>('/decisions'),
  verifyDecisions: () => get<{ valid: boolean; count: number; broken_at_seq: number | null; head?: string }>(
    '/decisions/verify',
  ),

  seedDemo: () => post<unknown>('/demo/seed'),
  simulateSettlement: (paymentId: string) => post<Payment>(`/demo/payments/${paymentId}/simulate-settlement`),
}
