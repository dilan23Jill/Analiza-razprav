import { createContext, useContext, useEffect, useState } from 'react'

/**
 * Svetla/temna tema.
 *
 * Barve niso zapisane tukaj, temveč v CSS spremenljivkah (index.css). Ta
 * kontekst samo doda ali odstrani razred `light` na elementu <html>, kar
 * preklopi celoten nabor spremenljivk naenkrat. Tailwindovi razredi
 * (bg-dark-900, text-white/70 ...) ostanejo nespremenjeni.
 *
 * Izbira se shrani v localStorage. Kadar je uporabnik še ni izbral, se
 * uporabi nastavitev operacijskega sistema.
 */

const ThemeContext = createContext(null)
const STORAGE_KEY = 'theme'

function initialTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    // localStorage je lahko onemogočen (zasebno okno, strogi piškotki) —
    // takrat pade nazaj na sistemsko nastavitev in ne shranjuje.
  }
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  }
  return 'dark'
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(initialTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light')
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // brez shranjevanja: izbira velja do osvežitve strani
    }
  }, [theme])

  // Kadar uporabnik teme še ni izbral sam, sledi sistemski nastavitvi tudi,
  // če jo med uporabo spremeni.
  useEffect(() => {
    if (!window.matchMedia) return
    let chosen = false
    try {
      chosen = localStorage.getItem(STORAGE_KEY) !== null
    } catch {
      chosen = false
    }
    if (chosen) return
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const onChange = (e) => setTheme(e.matches ? 'light' : 'dark')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const toggleTheme = () => setTheme((t) => (t === 'light' ? 'dark' : 'light'))

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme mora biti znotraj ThemeProvider')
  return ctx
}
