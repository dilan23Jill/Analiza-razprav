import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './services/auth'
import { LanguageProvider, useLanguage } from './utils/LanguageContext'
import { ThemeProvider } from './utils/ThemeContext'
import { ActiveJobsProvider } from './hooks/ActiveJobsContext'
import ErrorBoundary from './components/ErrorBoundary'
import App from './App'
import './index.css'

function LocalizedErrorBoundary({ children }) {
  const { t } = useLanguage()
  return (
    <ErrorBoundary
      title={t.errorBoundaryTitle}
      message={t.errorBoundaryMessage}
      reloadLabel={t.errorBoundaryReload}
    >
      {children}
    </ErrorBoundary>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <LanguageProvider>
          <LocalizedErrorBoundary>
            <AuthProvider>
              <ActiveJobsProvider>
                <App />
              </ActiveJobsProvider>
            </AuthProvider>
          </LocalizedErrorBoundary>
        </LanguageProvider>
      </ThemeProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
