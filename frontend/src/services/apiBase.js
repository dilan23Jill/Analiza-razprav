/** Shared API base URL (dev proxy / production VITE_API_URL). */
export const API_BASE = import.meta.env.VITE_API_URL || '/api'

export function apiUrl(path) {
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${normalized}`
}
