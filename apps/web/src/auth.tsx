import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { auth, onSessionChange, refreshSession, type Role, type User } from './api'

interface AuthState {
  user: User | null
  ready: boolean
  can: (...roles: Role[]) => boolean
}

const AuthContext = createContext<AuthState>({ user: null, ready: false, can: () => false })

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    onSessionChange(setUser)
    // Restore the session from the refresh cookie on page load.
    refreshSession().finally(() => setReady(true))
  }, [])

  const can = (...roles: Role[]) => !!user && roles.includes(user.role)
  return <AuthContext.Provider value={{ user, ready, can }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
export { auth }
