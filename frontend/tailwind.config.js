/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // ── Neutral surface ramp ──────────────────────────────────────────
        // Vrednosti so v CSS spremenljivkah (glej index.css), da isti razred
        // deluje v temni in svetli temi. Zapis `rgb(var(--x) / <alpha-value>)`
        // ohrani Tailwindove modifikatorje prosojnosti (npr. bg-dark-900/60).
        dark: {
          900: 'rgb(var(--c-surface-900) / <alpha-value>)',  // ozadje aplikacije
          800: 'rgb(var(--c-surface-800) / <alpha-value>)',  // navigacija, dvignjeni paneli
          700: 'rgb(var(--c-surface-700) / <alpha-value>)',  // kartice, vnosna polja
          600: 'rgb(var(--c-surface-600) / <alpha-value>)',  // hover, dvignjeni čipi
          500: 'rgb(var(--c-surface-500) / <alpha-value>)',  // obrobe, ločnice
        },
        // Ospredje. `white` je namenoma preslikan na spremenljivko: razredi
        // text-white/70, border-white/10 in bg-white/5 se s tem v svetli temi
        // sami obrnejo v temno ospredje na svetli podlagi.
        white: 'rgb(var(--c-ink) / <alpha-value>)',
        // Kadar je potrebna zares bela ne glede na temo (npr. besedilo na
        // barvnem gumbu), uporabi `pure-white`.
        'pure-white': '#ffffff',
        // ── Accents ───────────────────────────────────────────────────────
        // One calm, modern family. `red` is the PRIMARY brand token (used for
        // buttons, active nav, spinners). It now holds a refined indigo — the
        // name stays for backwards-compat; pair hovers with indigo-600.
        accent: {
          red: '#6366f1',    // PRIMARY (indigo) — was harsh #e74c3c
          pink: '#e6a4b4',   // soft rose — speaker banner bg (dark text)
          blue: '#5b9bf2',   // info / labels
          purple: '#a78bfa', // fallacies / solo accents (distinct violet)
          green: '#5fd0a3',  // fact-check / success accents
        },
        // Convenience alias for new code / polish (same as primary).
        brand: {
          DEFAULT: '#6366f1',
          400: '#818cf8',
          500: '#6366f1',
          600: '#4f46e5',
        },
      },
      boxShadow: {
        // Jakost sence je v spremenljivki: na beli podlagi mora biti bistveno
        // rahlejša kot na temni, sicer izgleda kot madež.
        'soft': '0 4px 20px -4px rgb(0 0 0 / var(--c-shadow))',
        'card': '0 8px 30px -8px rgb(0 0 0 / var(--c-shadow-strong))',
        'glow': '0 0 0 1px rgba(99, 102, 241, 0.15), 0 8px 30px -8px rgba(99, 102, 241, 0.25)',
      },
    },
  },
  plugins: [],
}
