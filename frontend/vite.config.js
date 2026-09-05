import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    // Vite 6 blokira neznane hoste — dovoli ngrok domene za javni tunel.
    allowedHosts: ['.ngrok-free.app', '.ngrok.app', '.ngrok-free.dev', '.ngrok.io'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})