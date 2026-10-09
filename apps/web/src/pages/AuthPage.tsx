import { useState, type FormEvent } from 'react'
import { Link, Navigate } from 'react-router'
import { auth, useAuth } from '../auth'
import { Button, ErrorText, Field, Input } from '../components/ui'

export function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { user } = useAuth()
  const [orgName, setOrgName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') await auth.login(email, password)
      else await auth.register(orgName, email, password)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-9" />
          <div>
            <div className="font-semibold">PaymentLedger</div>
            <div className="text-sm text-slate-500">Accounts payable, matched and settled on Arc</div>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-lg bg-white p-6 shadow-sm ring-1 ring-slate-200">
          <h1 className="text-lg font-semibold">{mode === 'login' ? 'Sign in' : 'Create your business account'}</h1>
          {mode === 'register' && (
            <Field label="Business name">
              <Input required value={orgName} onChange={(e) => setOrgName(e.target.value)} autoFocus />
            </Field>
          )}
          <Field label="Email">
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus={mode === 'login'} />
          </Field>
          <Field label="Password" hint={mode === 'register' ? 'At least 10 characters.' : undefined}>
            <Input
              type="password"
              required
              minLength={mode === 'register' ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <ErrorText error={error} />
          <Button type="submit" loading={busy} className="w-full">
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-slate-500">
          {mode === 'login' ? (
            <>
              New here? <Link to="/register" className="font-medium text-indigo-600">Create an account</Link>
            </>
          ) : (
            <>
              Have an account? <Link to="/login" className="font-medium text-indigo-600">Sign in</Link>
            </>
          )}
        </p>
      </div>
    </div>
  )
}
