/**
 * API service — connects React frontend to FastAPI backend.
 *
 * In dev mode Vite proxies /api/* -> localhost:8000/*
 * In production set VITE_API_URL to the real backend URL.
 */

import { API_BASE } from './apiBase'

const BASE = API_BASE

function getToken() {
  return localStorage.getItem('da_token') || ''
}

/**
 * Turn any FastAPI / fetch error body into a human-readable string.
 * FastAPI returns `detail` in several shapes:
 *   • a plain string                         → use as-is
 *   • an object  { message: "..." }          → use .message
 *   • a 422 validation array [{loc,msg,...}] → join the msg fields
 * Without this, `new Error(detail)` on an array/object stringifies to the
 * dreaded "[object Object]".
 */
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

// ── Auth ──────────────────────────────────────────────────────

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

// ── Analysis jobs ──────────────────────────────────────────

/**
 * Probe a YouTube URL to get its duration and metadata (without downloading).
 * Used to auto-size the trim slider so the user doesn't have to guess.
 * @returns {Promise<{duration:number,title:string,uploader:string,thumbnail:string,is_live:boolean,video_id:string}>}
 */
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

// ── Saved debates (DB) ────────────────────────────────────

export async function listDebates(limit = 20, offset = 0, search = '', mode = '') {
  const params = new URLSearchParams({ limit, offset })
  if (search) params.set('search', search)
  if (mode) params.set('mode', mode)
  return request(`/debates?${params}`)
}

export async function deleteDebate(debateId) {
  return request(`/debates/${debateId}`, { method: 'DELETE' })
}

/**
 * Re-run the analysis of a saved debate using its existing transcript —
 * no download, no transcription. Returns a JobStatus ({ job_id, ... });
 * the result is saved as a NEW debate entry.
 */
export async function rerunDebate(debateId, mode = '', language = '') {
  const body = {}
  if (mode) body.mode = mode
  if (language) body.language = language
  return request(`/debates/${debateId}/rerun`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * Re-run ONLY the fact-checking of a saved debate, in place. The arguments,
 * fallacies and rebuttals stay as they are; the sources and the verdicts are
 * refreshed. Returns a JobStatus.
 */
export async function recheckDebate(debateId) {
  return request(`/debates/${debateId}/recheck`, { method: 'POST' })
}

export async function getDebate(debateId) {
  return request(`/debates/${debateId}`)
}

/**
 * Apply edits to a debate. `operations` is an ordered array of:
 *   { op: "rename_speaker",     payload: { from_name, to_name } }
 *   { op: "edit_argument",      payload: { speaker, index, fields: {...} } }
 *   { op: "delete_argument",    payload: { speaker, index } }
 *   { op: "add_argument",       payload: { speaker, argument: {...} } }
 *   { op: "edit_speaker_meta",  payload: { speaker, fields: {position?, conclusions?, ...} } }
 *   { op: "edit_summary",       payload: { summary } }
 *   { op: "edit_metadata",      payload: { fields: { topic? } } }
 *   { op: "add_fallacy",       payload: { fallacy: { speaker, type, evidence, explanation?, target_arg_id? } } }
 *   { op: "edit_fallacy",      payload: { index, fields: { type?, evidence?, explanation? } } }
 *   { op: "delete_fallacy",    payload: { index } }
 * Returns: { status, applied, operations }
 */
export async function editDebate(debateId, operations) {
  return request(`/debates/${debateId}`, {
    method: 'PATCH',
    body: JSON.stringify({ operations }),
  })
}

// ── PDF export ─────────────────────────────────────────────

/**
 * Download a debate analysis as a PDF (authenticated). Triggers a browser
 * download with the given filename.
 */
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

// ── Health ─────────────────────────────────────────────────

export async function healthCheck() {
  return request('/health')
}
