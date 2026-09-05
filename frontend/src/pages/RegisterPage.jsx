import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { registerUser } from '../services/api'
import { useAuth } from '../services/auth'
import { useLanguage } from '../utils/LanguageContext'

export default function RegisterPage() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const { t } = useLanguage()
  const [form, setForm] = useState({ username: '', email: '', password: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const data = await registerUser(form.username, form.email, form.password)
      login(data.user, data.token)
      navigate('/')
    } catch (err) {
      setError(err.message || t.registerError)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-sm mx-auto mt-8 sm:mt-16">
      <h1 className="text-2xl font-bold text-white text-center mb-2">{t.registerTitle}</h1>
      <p className="text-white/40 text-sm text-center mb-8">
        {t.registerSubtitle}
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm text-white/60 mb-1.5">{t.username}</label>
          <input
            type="text"
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            required
            minLength={3}
            maxLength={30}
            placeholder={t.usernamePlaceholder}
            className="w-full bg-dark-600 border border-white/10 rounded-lg px-4 py-2.5
                       text-white placeholder-white/30 focus:outline-none
                       focus:border-accent-red/50 transition-colors text-sm"
          />
        </div>

        <div>
          <label className="block text-sm text-white/60 mb-1.5">{t.email}</label>
          <input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            required
            placeholder={t.emailPlaceholder}
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
            minLength={6}
            placeholder={t.passwordPlaceholder}
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
          {loading ? t.registering : t.createAccount}
        </button>
      </form>

      <p className="text-center text-white/40 text-sm mt-6">
        {t.haveAccount}{' '}
        <Link to="/login" className="text-accent-red hover:underline">
          {t.loginButton}
        </Link>
      </p>
    </div>
  )
}
