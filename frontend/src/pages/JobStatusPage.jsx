import { useState, useEffect, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getJobStatus } from '../services/api'
import { useLanguage } from '../utils/LanguageContext'
import { chimeOnceFor } from '../utils/sound'

const STEP_KEYS = [
  'jobStepLoading',
  'jobStepDownloading',
  'jobStepUsing',
  'jobStepTranscribing',
  'jobStepAnalyzing',
  'jobStepFactChecking',
  'jobStepDone',
]

const STEP_MATCH_KEYS = [
  'Loading',
  'Downloading',
  'Using',
  'Transcribing',
  'Analyzing',
  'Fact-checking',
  'Done',
]

const BASE_INTERVAL = 2000
const MAX_INTERVAL = 15000
const MAX_RETRIES = 5

export default function JobStatusPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { t } = useLanguage()
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')
  const [errorType, setErrorType] = useState('')
  const timerRef = useRef(null)
  const failCountRef = useRef(0)
  const intervalRef = useRef(BASE_INTERVAL)

  const steps = useMemo(
    () => STEP_KEYS.map((key, i) => ({ key: STEP_MATCH_KEYS[i], label: t[key] })),
    [t],
  )

  useEffect(() => {
    poll()
    return () => clearTimeout(timerRef.current)
  }, [id])

  function scheduleNext() {
    timerRef.current = setTimeout(poll, intervalRef.current)
  }

  async function poll() {
    try {
      const data = await getJobStatus(id)
      setJob(data)
      setError('')
      setErrorType('')
      failCountRef.current = 0
      intervalRef.current = BASE_INTERVAL

      if (data.status === 'completed') {
        chimeOnceFor(id)   // zvočni signal — analiza je končana
        setTimeout(() => navigate(`/debate/${id}`), 1500)
        return
      }
      if (data.status === 'failed') {
        setError(data.error || t.jobFailedDefault)
        setErrorType('failed')
        return
      }

      scheduleNext()
    } catch (err) {
      if (err.status === 404) {
        setError(t.jobNotFound)
        setErrorType('failed')
        return
      }

      failCountRef.current += 1

      if (failCountRef.current >= MAX_RETRIES) {
        setError(t.jobNetworkError)
        setErrorType('network')
        return
      }

      intervalRef.current = Math.min(
        BASE_INTERVAL * Math.pow(2, failCountRef.current),
        MAX_INTERVAL,
      )
      scheduleNext()
    }
  }

  const currentStepIdx = job?.progress
    ? steps.findIndex((s) => job.progress.includes(s.key))
    : 0

  return (
    <div className="max-w-lg mx-auto mt-4 sm:mt-8 animate-fade-in">
      <h1 className="text-xl sm:text-2xl font-bold text-gradient mb-2">{t.jobStatusTitle}</h1>
      <p className="text-white/40 text-sm mb-6 sm:mb-8">{t.jobIdLabel}: {id}</p>

      <div className="stagger space-y-3 mb-8">
        {steps.map((step, i) => {
          const done = i < currentStepIdx || job?.status === 'completed'
          const active = i === currentStepIdx && job?.status === 'processing'

          return (
            <div key={step.key} className="flex items-center gap-3">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center
                flex-shrink-0 border-2 transition-all ${
                done
                  ? 'border-green-500 bg-green-500/20 text-green-400'
                  : active
                    ? 'step-ring border-accent-red/40 bg-accent-red/20 text-accent-red'
                    : 'border-white/10 text-white/20'
              }`}>
                {done ? (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  <span className="text-xs font-medium">{i + 1}</span>
                )}
              </div>
              <span className={`text-sm transition-colors ${
                done ? 'text-green-400' :
                active ? 'text-white font-medium' : 'text-white/30'
              }`}>
                {step.label}
              </span>
            </div>
          )
        })}
      </div>

      {job?.progress && job.status === 'processing' && (
        <div className="p-4 bg-dark-600/50 border border-white/5 rounded-xl backdrop-blur-sm">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-sm text-white/70">{job.progress}</span>
          </div>
          <div className="progress-glow" />
        </div>
      )}

      {job?.status === 'completed' && (
        <div className="p-4 bg-green-500/10 border border-green-500/30 rounded-lg text-green-400 text-sm">
          {t.jobCompletedRedirect}
        </div>
      )}

      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
          <p className="text-red-400 text-sm">{error}</p>
          <button
            type="button"
            onClick={() => {
              if (errorType === 'failed') {
                navigate('/analyze')
                return
              }
              failCountRef.current = 0
              intervalRef.current = BASE_INTERVAL
              setError('')
              setErrorType('')
              poll()
            }}
            className="mt-2 text-xs text-accent-red hover:underline"
          >
            {t.jobRetry}
          </button>
        </div>
      )}
    </div>
  )
}
