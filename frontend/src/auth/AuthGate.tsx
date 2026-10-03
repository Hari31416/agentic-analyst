import {
  createContext,
  FormEvent,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import { LogOut, RefreshCw, ShieldCheck, UserRound, X } from 'lucide-react'
import { apiFetch, SESSION_EXPIRED_EVENT } from '../lib/apiFetch'

export type AuthUser = {
  id: string
  username: string
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string
}

type AuthContextValue = {
  user: AuthUser
  logout: () => Promise<void>
  logoutError: string
  openUserManager: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthGate')
  return context
}

type LoginResult = {
  access_token: string
  token_type: 'bearer'
  user: AuthUser
}
type GateState = 'checking' | 'service-error' | 'signed-out' | 'signed-in'

async function readError(response: Response) {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Keep the status message if the server did not return JSON.
  }
  return `Request failed (${response.status})`
}

export default function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>('checking')
  const [user, setUser] = useState<AuthUser | null>(null)
  const [serviceError, setServiceError] = useState('')
  const [managerOpen, setManagerOpen] = useState(false)
  const [logoutError, setLogoutError] = useState('')

  const checkSession = useCallback(async () => {
    setState('checking')
    setServiceError('')
    try {
      const response = await fetch('/api/auth/me', {
        credentials: 'same-origin',
      })
      if (response.status === 401) {
        setUser(null)
        setState('signed-out')
        return
      }
      if (!response.ok) throw new Error(await readError(response))
      setUser((await response.json()) as AuthUser)
      setState('signed-in')
    } catch (error) {
      setServiceError(
        error instanceof Error ? error.message : 'Could not reach the service.',
      )
      setState('service-error')
    }
  }, [])

  useEffect(() => {
    void checkSession()
  }, [checkSession])

  useEffect(() => {
    const onExpired = () => {
      setUser(null)
      setManagerOpen(false)
      setState('signed-out')
    }
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
  }, [])

  const logout = useCallback(async () => {
    setLogoutError('')
    try {
      const response = await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
      })
      if (response.status !== 204 && response.status !== 401) {
        setLogoutError(await readError(response))
        return
      }
      setManagerOpen(false)
      setUser(null)
      setState('signed-out')
    } catch (reason) {
      setLogoutError(
        reason instanceof Error
          ? `Could not reach the service: ${reason.message}`
          : 'Could not reach the service. Try signing out again.',
      )
    }
  }, [])

  const contextValue = useMemo(
    () =>
      user
        ? {
            user,
            logout,
            logoutError,
            openUserManager: () => setManagerOpen(true),
          }
        : null,
    [logout, logoutError, user],
  )

  if (state === 'checking') {
    return (
      <main className="auth-screen">
        <p>Checking your session…</p>
      </main>
    )
  }
  if (state === 'service-error') {
    return (
      <main className="auth-screen">
        <section className="auth-card" aria-labelledby="service-title">
          <div className="auth-mark">
            <RefreshCw size={19} />
          </div>
          <p className="auth-kicker">Agentic analyst</p>
          <h1 id="service-title">The service is unavailable</h1>
          <p className="auth-copy">{serviceError}</p>
          <button className="auth-primary" onClick={() => void checkSession()}>
            Retry connection
          </button>
        </section>
      </main>
    )
  }
  if (state === 'signed-out') {
    return (
      <LoginScreen
        onSignedIn={(nextUser) => {
          setUser(nextUser)
          setState('signed-in')
        }}
      />
    )
  }

  return (
    <AuthContext.Provider value={contextValue!}>
      {children}
      {managerOpen && user?.role === 'admin' && (
        <UserManager onClose={() => setManagerOpen(false)} currentUser={user} />
      )}
    </AuthContext.Provider>
  )
}

function LoginScreen({ onSignedIn }: { onSignedIn: (user: AuthUser) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!response.ok) throw new Error(await readError(response))
      const result = (await response.json()) as LoginResult
      onSignedIn(result.user)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not sign in.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-mark">
          <ShieldCheck size={21} />
        </div>
        <p className="auth-kicker">Agentic analyst</p>
        <h1 id="login-title">Sign in to your workspace</h1>
        <p className="auth-copy">
          Your research and source connections are ready when you are.
        </p>
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="auth-username">Username</label>
          <input
            id="auth-username"
            autoComplete="username"
            required
            minLength={3}
            maxLength={50}
            pattern="[A-Za-z0-9._-]{3,50}"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <label htmlFor="auth-password">Password</label>
          <input
            id="auth-password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={128}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}
          <button className="auth-primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </section>
    </main>
  )
}

function UserManager({
  onClose,
  currentUser,
}: {
  onClose: () => void
  currentUser: AuthUser
}) {
  const [users, setUsers] = useState<AuthUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'admin' | 'user'>('user')
  const [busy, setBusy] = useState(false)
  const [resetId, setResetId] = useState<string | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [offset, setOffset] = useState(0)
  const [hasNextPage, setHasNextPage] = useState(false)
  const pageSize = 100

  const loadUsers = useCallback(
    async (nextOffset = 0) => {
      setLoading(true)
      setError('')
      try {
        const response = await apiFetch(
          `/api/auth/users?limit=${pageSize + 1}&offset=${nextOffset}`,
        )
        if (!response.ok) throw new Error(await readError(response))
        const result = (await response.json()) as AuthUser[]
        setOffset(nextOffset)
        setHasNextPage(result.length > pageSize)
        setUsers(result.slice(0, pageSize))
      } catch (reason) {
        setError(
          reason instanceof Error ? reason.message : 'Could not load accounts.',
        )
      } finally {
        setLoading(false)
      }
    },
    [pageSize],
  )

  useEffect(() => {
    void loadUsers()
  }, [loadUsers])

  async function updateUser(
    id: string,
    patch: Partial<Pick<AuthUser, 'role' | 'is_active'>> & {
      password?: string
    },
  ) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(
        `/api/auth/users/${encodeURIComponent(id)}`,
        {
          method: 'PATCH',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        },
      )
      if (!response.ok) throw new Error(await readError(response))
      const updated = (await response.json()) as AuthUser
      setUsers((items) =>
        items.map((item) => (item.id === id ? updated : item)),
      )
      if (id === currentUser.id) {
        window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT))
      }
      setNotice('Account updated.')
      setResetId(null)
      setResetPassword('')
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not update account.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function createUser(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await apiFetch('/api/auth/users', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, role }),
      })
      if (!response.ok) throw new Error(await readError(response))
      const created = (await response.json()) as AuthUser
      if (users.length >= pageSize) setHasNextPage(true)
      setUsers((items) => {
        return items.length >= pageSize ? items : [...items, created]
      })
      setUsername('')
      setPassword('')
      setRole('user')
      setNotice(`Created ${created.username}.`)
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Could not create account.',
      )
    } finally {
      setBusy(false)
    }
  }

  const activeAdminCount = users.filter(
    (account) => account.role === 'admin' && account.is_active,
  ).length

  return (
    <div
      className="auth-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        className="user-manager"
        role="dialog"
        aria-modal="true"
        aria-labelledby="users-title"
      >
        <header className="user-manager-heading">
          <div>
            <p className="auth-kicker">Administration</p>
            <h2 id="users-title">User accounts</h2>
          </div>
          <button
            className="auth-icon-button"
            aria-label="Close account management"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        <form
          className="user-create-form"
          onSubmit={(event) => void createUser(event)}
        >
          <h3>Create an account</h3>
          <div className="user-create-fields">
            <label>
              Username
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                minLength={3}
                maxLength={50}
                pattern="[A-Za-z0-9._-]{3,50}"
                required
                autoComplete="off"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={8}
                maxLength={128}
                required
                autoComplete="new-password"
              />
            </label>
            <label>
              Role
              <select
                value={role}
                onChange={(event) =>
                  setRole(event.target.value as 'admin' | 'user')
                }
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <button className="auth-primary" disabled={busy}>
              Create user
            </button>
          </div>
        </form>
        {(error || notice) && (
          <p
            className={error ? 'auth-error' : 'auth-notice'}
            role={error ? 'alert' : 'status'}
          >
            {error || notice}
          </p>
        )}
        <div className="user-list-heading">
          <h3>Accounts</h3>
          <button
            className="auth-text-button"
            onClick={() => void loadUsers(offset)}
            disabled={loading}
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
        {loading ? (
          <p className="auth-copy">Loading accounts…</p>
        ) : (
          <div className="user-list">
            {users.map((account) => {
              const isLastActiveAdmin =
                account.role === 'admin' &&
                account.is_active &&
                offset === 0 &&
                !hasNextPage &&
                activeAdminCount === 1
              return (
                <article className="user-row" key={account.id}>
                  <div className="user-identity">
                    <span className="user-avatar">
                      <UserRound size={16} />
                    </span>
                    <div>
                      <strong>
                        {account.username}
                        {account.id === currentUser.id ? ' (you)' : ''}
                      </strong>
                      <small>
                        Added{' '}
                        {new Date(account.created_at).toLocaleDateString()}
                      </small>
                    </div>
                  </div>
                  <div className="user-actions">
                    <select
                      aria-label={`${account.username} role`}
                      value={account.role}
                      disabled={busy || isLastActiveAdmin}
                      onChange={(event) =>
                        void updateUser(account.id, {
                          role: event.target.value as 'admin' | 'user',
                        })
                      }
                    >
                      <option value="user">User</option>
                      <option value="admin">Admin</option>
                    </select>
                    <span
                      className={`user-status ${account.is_active ? 'is-active' : ''}`}
                    >
                      {account.is_active ? 'Active' : 'Disabled'}
                    </span>
                    <button
                      className="auth-text-button"
                      disabled={busy || isLastActiveAdmin}
                      onClick={() =>
                        void updateUser(account.id, {
                          is_active: !account.is_active,
                        })
                      }
                    >
                      {account.is_active ? 'Disable' : 'Enable'}
                    </button>
                    {resetId === account.id ? (
                      <form
                        className="reset-form"
                        onSubmit={(event) => {
                          event.preventDefault()
                          void updateUser(account.id, {
                            password: resetPassword,
                          })
                        }}
                      >
                        <input
                          type="password"
                          aria-label="New password"
                          placeholder="New password"
                          minLength={8}
                          maxLength={128}
                          required
                          value={resetPassword}
                          onChange={(event) =>
                            setResetPassword(event.target.value)
                          }
                        />
                        <button className="auth-text-button" disabled={busy}>
                          Save
                        </button>
                        <button
                          type="button"
                          className="auth-text-button"
                          onClick={() => setResetId(null)}
                        >
                          Cancel
                        </button>
                      </form>
                    ) : (
                      <button
                        className="auth-text-button"
                        disabled={busy}
                        onClick={() => setResetId(account.id)}
                      >
                        Reset password
                      </button>
                    )}
                  </div>
                </article>
              )
            })}
            {users.length === 0 && (
              <p className="auth-copy">No accounts found.</p>
            )}
          </div>
        )}
        <div className="user-pagination" aria-live="polite">
          <span>
            {users.length
              ? `Showing accounts ${offset + 1}–${offset + users.length}`
              : 'No accounts on this page'}
          </span>
          <div>
            <button
              className="auth-text-button"
              disabled={loading || offset === 0}
              onClick={() => void loadUsers(Math.max(0, offset - pageSize))}
            >
              Previous
            </button>
            <button
              className="auth-text-button"
              disabled={loading || !hasNextPage}
              onClick={() => void loadUsers(offset + pageSize)}
            >
              Next
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}

export function AuthBar() {
  const { user, logout, logoutError, openUserManager } = useAuth()
  return (
    <div className="auth-bar">
      <span className="auth-bar-user">
        <UserRound size={14} />
        {user.username}
      </span>
      {user.role === 'admin' && (
        <button onClick={openUserManager}>Manage users</button>
      )}
      <button onClick={() => void logout()} aria-label="Sign out">
        <LogOut size={15} />
        <span>Sign out</span>
      </button>
      {logoutError && (
        <span className="auth-bar-error" role="alert">
          {logoutError}
        </span>
      )}
    </div>
  )
}
