
import { API_BASE } from './apiBase'

const BASE = API_BASE

function getToken() {
  return localStorage.getItem('da_token') || ''
}

function extractErrorMessage(body, status) {
  const detail = body?.detail ?? body?.message ?? body
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msg = detail
      .map((d) => (typeof d === 'string' ? d : d?.msg || d?.message))
      .filter(Boolean)
      .join('; ')
    if (msg) return msg
  }
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string') return detail.message
    if (typeof detail.msg === 'string') return detail.msg
  }
  return `HTTP ${status}`
}

async function request(path, options = {}) {
  const token = getToken()
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  }

  const res = await fetch(`${BASE}${path}`, { ...options, headers })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(extractErrorMessage(body, res.status))
    err.status = res.status
    err.body = body
    throw err
  }
  return res.json()
}

// Auth

export async function registerUser(username, email, password) {
  return request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, email, password }),
  })
}

export async function loginUser(login, password) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ login, password }),
  })
}

// Analysis jobs

export async function probeYoutube(url) {
  return request('/probe-youtube', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export async function submitAnalysis(youtubeUrl, mode = 'solo', language = 'sl', speakerNames = '', title = '', startTime = '', endTime = '') {
  return request('/analyze', {
    method: 'POST',
    body: JSON.stringify({
      youtube_url: youtubeUrl,
      mode,
      language,
      speaker_names: speakerNames || null,
      title: title || null,
      start_time: startTime || null,
      end_time: endTime || null,
    }),
  })
}

export async function submitUploadAnalysis(file, mode = 'solo', language = 'sl', speakerNames = '', title = '', startTime = '', endTime = '') {
  const token = getToken()
  const formData = new FormData()
  formData.append('file', file)
  formData.append('mode', mode)
  formData.append('language', language)
  formData.append('speaker_names', speakerNames || '')
  formData.append('title', title || '')
  formData.append('start_time', startTime || '')
  formData.append('end_time', endTime || '')

  const res = await fetch(`${BASE}/analyze/upload`, {
    method: 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: formData,
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(extractErrorMessage(body, res.status))
    err.status = res.status
    err.body = body
    throw err
  }
  return res.json()
}

export async function getJobStatus(jobId) {
  return request(`/jobs/${jobId}`)
}

export async function getMyJobs() {
  return request('/jobs')
}

// Saved debates (DB)

export async function listDebates(limit = 20, offset = 0, search = '', mode = '') {
  const params = new URLSearchParams({ limit, offset })
  if (search) params.set('search', search)
  if (mode) params.set('mode', mode)
  return request(`/debates?${params}`)
}

export async function deleteDebate(debateId) {
  return request(`/debates/${debateId}`, { method: 'DELETE' })
}

export async function rerunDebate(debateId, mode = '', language = '') {
  const body = {}
  if (mode) body.mode = mode
  if (language) body.language = language
  return request(`/debates/${debateId}/rerun`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function recheckDebate(debateId) {
  return request(`/debates/${debateId}/recheck`, { method: 'POST' })
}

export async function getDebate(debateId) {
  return request(`/debates/${debateId}`)
}

export async function editDebate(debateId, operations) {
  return request(`/debates/${debateId}`, {
    method: 'PATCH',
    body: JSON.stringify({ operations }),
  })
}

// PDF export

export async function downloadDebatePdf(debateId, filename = 'debate.pdf') {
  const token = getToken()
  const res = await fetch(`${BASE}/debates/${debateId}/pdf`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(extractErrorMessage(body, res.status))
    err.status = res.status
    throw err
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// Health

export async function healthCheck() {
  return request('/health')
}
