import { createContext, useContext, useState, useEffect } from 'react'

const AuthContext = createContext(null)

const TOKEN_KEY = 'da_token'
const USER_KEY = 'da_user'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const saved = localStorage.getItem(USER_KEY)
      return saved ? JSON.parse(saved) : null
    } catch { return null }
  })

  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || null)
  const [loading, setLoading] = useState(true)

  // Verify token on mount / token changes
  useEffect(() => {
    if (token) {
      verifyToken(token)
    } else {
      setLoading(false)
    }
  }, [token])

  async function verifyToken(tokenToVerify) {
    try {
      const res = await fetch(apiUrl('/auth/me'), {
        headers: { Authorization: `Bearer ${tokenToVerify}` },
      })
      if (res.ok) {
        const data = await res.json()
        setUser(data.user)
        localStorage.setItem(USER_KEY, JSON.stringify(data.user))
      } else {
        // Token expired
        logout()
      }
    } catch {
      // Network error — keep user for offline
    } finally {
      setLoading(false)
    }
  }

  function login(userData, tokenStr) {
    setUser(userData)
    setToken(tokenStr)
    localStorage.setItem(TOKEN_KEY, tokenStr)
    localStorage.setItem(USER_KEY, JSON.stringify(userData))
  }

  function logout() {
    if (token) {
      fetch(apiUrl('/auth/logout'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {})
    }
    setUser(null)
    setToken(null)
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  return (
    <AuthContext.Provider value={{ user, token, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}

import { apiUrl } from './apiBase'
