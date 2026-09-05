import { createContext, useContext, useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { getMyJobs } from '../services/api'
import { useAuth } from '../services/auth'
import { chimeOnceFor } from '../utils/sound'

const POLL_FAST_MS = 5000
const POLL_SLOW_MS = 30000

const ActiveJobsContext = createContext(null)

export function isActiveJobStatus(status) {
  return status === 'processing' || status === 'queued'
}

export function ActiveJobsProvider({ children }) {
  const { user } = useAuth()
  const [jobs, setJobs] = useState([])
  const [initialLoading, setInitialLoading] = useState(true)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)
  const activeCountRef = useRef(0)
  const prevStatusRef = useRef({})   // job_id -> zadnji znani status (za zvok ob prehodu v completed)

  // Zapiska, ko prej aktivna naloga preide v 'completed' — tudi če uporabnik
  // ni na strani naloge. chimeOnceFor poskrbi, da vsak job zapiska samo enkrat.
  const _detectCompletions = useCallback((list) => {
    const prev = prevStatusRef.current
    for (const j of list) {
      if (j.status === 'completed' && isActiveJobStatus(prev[j.job_id])) {
        chimeOnceFor(j.job_id)
      }
    }
    prevStatusRef.current = Object.fromEntries(list.map(j => [j.job_id, j.status]))
  }, [])

  const refresh = useCallback(async () => {
    if (!user) {
      setJobs([])
      setInitialLoading(false)
      return []
    }
    try {
      const data = await getMyJobs()
      const list = data?.jobs || []
      setJobs(list)
      activeCountRef.current = list.filter(j => isActiveJobStatus(j.status)).length
      _detectCompletions(list)
      return list
    } catch {
      return []
    } finally {
      setInitialLoading(false)
    }
  }, [user])

  useEffect(() => {
    cancelledRef.current = false
    if (!user) {
      setJobs([])
      setInitialLoading(false)
      return () => { cancelledRef.current = true }
    }

    async function poll() {
      if (cancelledRef.current) return
      if (typeof document !== 'undefined' && document.hidden) return
      try {
        const data = await getMyJobs()
        if (cancelledRef.current) return
        const list = data?.jobs || []
        setJobs(list)
        activeCountRef.current = list.filter(j => isActiveJobStatus(j.status)).length
        _detectCompletions(list)
      } catch {
        // non-critical
      } finally {
        if (!cancelledRef.current) setInitialLoading(false)
      }
    }

    function schedule() {
      if (cancelledRef.current) return
      const interval = activeCountRef.current > 0 ? POLL_FAST_MS : POLL_SLOW_MS
      timerRef.current = setTimeout(async () => {
        await poll()
        schedule()
      }, interval)
    }

    poll().then(schedule)

    function onVisibilityChange() {
      if (!document.hidden && !cancelledRef.current) {
        poll().then(() => {
          if (timerRef.current) clearTimeout(timerRef.current)
          schedule()
        })
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelledRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [user])

  const activeJobs = useMemo(() => {
    const active = jobs.filter(j => isActiveJobStatus(j.status))
    active.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    return active
  }, [jobs])

  const primaryActiveJob = activeJobs[0] || null

  const value = useMemo(
    () => ({ jobs, activeJobs, primaryActiveJob, initialLoading, refresh }),
    [jobs, activeJobs, primaryActiveJob, initialLoading, refresh],
  )

  return (
    <ActiveJobsContext.Provider value={value}>
      {children}
    </ActiveJobsContext.Provider>
  )
}

export function useActiveJobs() {
  const ctx = useContext(ActiveJobsContext)
  if (!ctx) {
    throw new Error('useActiveJobs must be used within ActiveJobsProvider')
  }
  return ctx
}
