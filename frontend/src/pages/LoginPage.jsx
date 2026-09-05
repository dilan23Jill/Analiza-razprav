import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { loginUser } from '../services/api'
import { useAuth } from '../services/auth'
import { useLanguage } from '../utils/LanguageContext'

export default function LoginPage() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const { t } = useLanguage()
  const [form, setForm] = useState({ login: '', password: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const data = await loginUser(form.login, form.password)
      login(data.user, data.token)
      navigate('/')
    } catch (err) {
      setError(err.message || t.loginError)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-sm mx-auto mt-8 sm:mt-16">
      <h1 className="text-2xl font-bold text-white text-center mb-2">{t.loginTitle}</h1>
      <p className="text-white/40 text-sm text-center mb-8">
        {t.loginSubtitle}
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm text-white/60 mb-1.5">{t.usernameOrEmail}</label>
          <input
            type="text"
            value={form.login}
            onChange={(e) => setForm({ ...form, login: e.target.value })}
            required
            className="w-full bg-dark-600 border border-white/10 rounded-lg px-4 py-2.5
                       text-white placeholder-white/30 focus:outline-none
                       focus:border-accent-red/50 transition-colors text-sm"
          />
        </div>

        <div>
          <label className="block text-sm text-white/60 mb-1.5">{t.password}</label>
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
            className="w-full bg-dark-600 border border-white/10 rounded-lg px-4 py-2.5
                       text-white placeholder-white/30 focus:outline-none
                       focus:border-accent-red/50 transition-colors text-sm"
          />
        </div>

        {error && (
          <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full py-2.5 bg-accent-red hover:bg-brand-600 disabled:bg-white/10
                     text-white font-semibold rounded-lg transition-colors text-sm"
        >
          {loading ? t.loggingIn : t.loginButton}
        </button>
      </form>

      <p className="text-center text-white/40 text-sm mt-6">
        {t.noAccount}{' '}
        <Link to="/register" className="text-accent-red hover:underline">
          {t.register}
        </Link>
      </p>
    </div>
  )
}
