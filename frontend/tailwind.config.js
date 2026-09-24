export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Neutral surface ramp
        dark: {
          900: 'rgb(var(--c-surface-900) / <alpha-value>)',
          800: 'rgb(var(--c-surface-800) / <alpha-value>)',
          700: 'rgb(var(--c-surface-700) / <alpha-value>)',
          600: 'rgb(var(--c-surface-600) / <alpha-value>)',
          500: 'rgb(var(--c-surface-500) / <alpha-value>)',
        },
        white: 'rgb(var(--c-ink) / <alpha-value>)',
        'pure-white': '#ffffff',
        // Accents
        accent: {
          red: '#6366f1',
          pink: '#e6a4b4',
          blue: '#5b9bf2',
          purple: '#a78bfa',
          green: '#5fd0a3',
        },
        brand: {
          DEFAULT: '#6366f1',
          400: '#818cf8',
          500: '#6366f1',
          600: '#4f46e5',
        },
      },
      boxShadow: {
        'soft': '0 4px 20px -4px rgb(0 0 0 / var(--c-shadow))',
        'card': '0 8px 30px -8px rgb(0 0 0 / var(--c-shadow-strong))',
        'glow': '0 0 0 1px rgba(99, 102, 241, 0.15), 0 8px 30px -8px rgba(99, 102, 241, 0.25)',
      },
    },
  },
  plugins: [],
}
