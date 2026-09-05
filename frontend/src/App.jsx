import { useState, Suspense, lazy } from 'react'
import { Routes, Route, Link, useLocation, Navigate } from 'react-router-dom'
import { useAuth } from './services/auth'
import { useLanguage } from './utils/LanguageContext'
import { useTheme } from './utils/ThemeContext'
import HomePage from './pages/HomePage'
import AnalyzePage from './pages/AnalyzePage'
const DebateViewPage = lazy(() => import('./pages/DebateViewPage'))
import JobStatusPage from './pages/JobStatusPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import RunningJobBubble from './components/RunningJobBubble'

export default function App() {
  const location = useLocation()
  const { user, loading, logout } = useAuth()
  const { lang, setLang, t } = useLanguage()
  const [menuOpen, setMenuOpen] = useState(false)

  // Close mobile menu on navigation
  const closeMenu = () => setMenuOpen(false)

  if (loading) {
    return (
      <div className="min-h-screen bg-dark-900 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-accent-red border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-dark-900 overflow-x-hidden">
      {/* ── NAV ──────────────────────────────────────────── */}
      <nav className="border-b border-white/[0.06] bg-dark-900/70 backdrop-blur-xl sticky top-0 z-50 shadow-soft">
        <div className="max-w-[1500px] mx-auto px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2 sm:gap-3 group" onClick={closeMenu}>
            <img src="/icon.svg" alt="DA" className="logo-glow w-8 h-8 rounded-lg ring-1 ring-white/10 group-hover:ring-accent-red/40 transition-all" />
            <span className="text-base sm:text-lg font-semibold text-gradient">
              Debate Analyzer
            </span>
          </Link>

          {/* Desktop nav */}
          <div className="hidden md:flex items-center gap-6">
            {/* Language + theme switchers */}
            <LangSwitcher lang={lang} setLang={setLang} />
            <ThemeSwitcher />

            {user ? (
              <>
                <NavLink to="/" current={location.pathname}>
                  {t.myAnalyses}
                </NavLink>
                <NavLink to="/analyze" current={location.pathname}>
                  {t.newAnalysis}
                </NavLink>
                <div className="flex items-center gap-3 ml-2 pl-4 border-l border-white/10">
                  <span className="text-sm text-white/50">{user.username}</span>
                  <button
                    onClick={logout}
                    className="text-xs text-white/30 hover:text-red-400 transition-colors"
                  >
                    {t.logout}
                  </button>
                </div>
              </>
            ) : (
              <>
                <NavLink to="/login" current={location.pathname}>
                  {t.login}
                </NavLink>
                <Link
                  to="/register"
                  className="px-4 py-1.5 bg-accent-red hover:bg-brand-600 text-pure-white
                             text-sm font-medium rounded-lg transition-colors shadow-soft"
                >
                  {t.register}
                </Link>
              </>
            )}
          </div>

          {/* Mobile: lang switcher + hamburger */}
          <div className="flex items-center gap-3 md:hidden">
            <ThemeSwitcher />
            <LangSwitcher lang={lang} setLang={setLang} />
            <button
              type="button"
              onClick={() => setMenuOpen(!menuOpen)}
              className="p-2 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition-colors"
              aria-label="Toggle menu"
            >
              {menuOpen ? (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              )}
            </button>
          </div>
        </div>

        {/* Mobile menu dropdown */}
        {menuOpen && (
          <div className="md:hidden border-t border-white/10 bg-dark-900/95 backdrop-blur-sm px-4 py-3 space-y-1 animate-fade-in">
            {user ? (
              <>
                <MobileNavLink to="/" current={location.pathname} onClick={closeMenu}>
                  {t.myAnalyses}
                </MobileNavLink>
                <MobileNavLink to="/analyze" current={location.pathname} onClick={closeMenu}>
                  {t.newAnalysis}
                </MobileNavLink>
                <div className="flex items-center justify-between pt-2 mt-2 border-t border-white/10">
                  <span className="text-sm text-white/50">{user.username}</span>
                  <button
                    onClick={() => { logout(); closeMenu() }}
                    className="text-xs text-white/30 hover:text-red-400 transition-colors px-3 py-1.5"
                  >
                    {t.logout}
                  </button>
                </div>
              </>
            ) : (
              <>
                <MobileNavLink to="/login" current={location.pathname} onClick={closeMenu}>
                  {t.login}
                </MobileNavLink>
                <Link
                  to="/register"
                  onClick={closeMenu}
                  className="block w-full text-center px-4 py-2.5 bg-accent-red hover:bg-brand-600 text-pure-white
                             text-sm font-medium rounded-lg transition-colors mt-2"
                >
                  {t.register}
                </Link>
              </>
            )}
          </div>
        )}
      </nav>

      {/* ── CONTENT ──────────────────────────────────────── */}
      <main className="relative z-10 max-w-[1500px] mx-auto px-4 sm:px-6 py-4 sm:py-8">
        <Routes>
          {/* Public routes */}
          <Route path="/login" element={user ? <Navigate to="/" /> : <LoginPage />} />
          <Route path="/register" element={user ? <Navigate to="/" /> : <RegisterPage />} />

          {/* Protected routes */}
          <Route path="/" element={user ? <HomePage /> : <Navigate to="/login" />} />
          <Route path="/analyze" element={user ? <AnalyzePage /> : <Navigate to="/login" />} />
          <Route
            path="/debate/:id"
            element={user ? (
              <Suspense fallback={
                <div className="flex items-center justify-center py-20">
                  <div className="w-8 h-8 border-2 border-accent-red border-t-transparent rounded-full animate-spin" />
                </div>
              }>
                <DebateViewPage />
              </Suspense>
            ) : <Navigate to="/login" />}
          />
          <Route path="/job/:id" element={user ? <JobStatusPage /> : <Navigate to="/login" />} />
        </Routes>
      </main>

      {/* Floating bubble — surfaces any active analysis on every page so the
          user can navigate freely (browse, start a new one, check from phone)
          and always click back to the running job. */}
      <RunningJobBubble />
    </div>
  )
}

function ThemeSwitcher() {
  const { theme, toggleTheme } = useTheme()
  const light = theme === 'light'
  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={light ? 'Preklopi na temno temo' : 'Preklopi na svetlo temo'}
      title={light ? 'Temna tema' : 'Svetla tema'}
      className="p-1.5 rounded-lg bg-dark-600/30 text-white/60 hover:text-white/90 hover:bg-dark-600/60 transition-colors"
    >
      {light ? (
        /* luna — klik preklopi na temno */
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      ) : (
        /* sonce — klik preklopi na svetlo */
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
        </svg>
      )}
    </button>
  )
}

function LangSwitcher({ lang, setLang }) {
  return (
    <div className="flex gap-1 bg-dark-600/30 rounded-lg p-0.5">
      <button
        onClick={() => setLang('sl')}
        className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
          lang === 'sl'
            ? 'bg-accent-red text-pure-white'
            : 'text-white/50 hover:text-white/80'
        }`}
      >
        SL
      </button>
      <button
        onClick={() => setLang('en')}
        className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
          lang === 'en'
            ? 'bg-accent-red text-pure-white'
            : 'text-white/50 hover:text-white/80'
        }`}
      >
        EN
      </button>
    </div>
  )
}

function NavLink({ to, current, children }) {
  const active = current === to
  return (
    <Link
      to={to}
      className={`nav-underline text-sm font-medium transition-colors ${
        active
          ? 'nav-active text-accent-red'
          : 'text-white/60 hover:text-white'
      }`}
    >
      {children}
    </Link>
  )
}

function MobileNavLink({ to, current, onClick, children }) {
  const active = current === to
  return (
    <Link
      to={to}
      onClick={onClick}
      className={`block px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
        active
          ? 'text-accent-red bg-accent-red/10'
          : 'text-white/60 hover:text-white hover:bg-white/5'
      }`}
    >
      {children}
    </Link>
  )
}
