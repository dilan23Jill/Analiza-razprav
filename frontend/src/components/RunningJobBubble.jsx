import { useNavigate, useLocation } from 'react-router-dom'
import { useActiveJobs } from '../hooks/ActiveJobsContext'
import { useAuth } from '../services/auth'
import { useLanguage } from '../utils/LanguageContext'

export default function RunningJobBubble() {
  const { user } = useAuth()
  const { t } = useLanguage()
  const { activeJobs, primaryActiveJob: activeJob } = useActiveJobs()
  const navigate = useNavigate()
  const location = useLocation()

  const onJobPage = location.pathname.startsWith('/job/')
  const currentJobId = onJobPage ? location.pathname.split('/job/')[1]?.split('/')[0] : null

  if (!user || !activeJob) return null
  if (currentJobId && currentJobId === activeJob.job_id) return null

  const progressText = activeJob.progress || (
    activeJob.status === 'queued' ? t.jobQueued : t.jobProcessing
  )

  function handleClick() {
    navigate(`/job/${activeJob.job_id}`)
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      title={t.clickToView}
      aria-label={t.clickToView}
      className="fixed bottom-4 right-4 sm:bottom-6 sm:right-6 z-40
                 max-w-[calc(100vw-2rem)] sm:max-w-sm
                 flex items-center gap-3 pl-3 pr-4 py-2.5 sm:py-3
                 bg-dark-600/95 hover:bg-dark-500
                 backdrop-blur-md border border-accent-red/40 hover:border-accent-red/70
                 rounded-full shadow-2xl shadow-accent-red/20
                 transition-all hover:scale-[1.02] active:scale-[0.98]
                 group cursor-pointer text-left"
    >
      <span className="relative flex shrink-0 w-6 h-6 items-center justify-center">
        <span className="absolute inset-0 rounded-full bg-accent-red/30 animate-ping" />
        <span className="relative w-4 h-4 border-2 border-accent-red border-t-transparent rounded-full animate-spin" />
      </span>

      <span className="flex-1 min-w-0">
        <span className="block text-[10px] uppercase tracking-wider text-accent-red font-semibold">
          {t.analysisRunning}
          {activeJobs.length > 1 && (
            <span className="ml-1 text-white/40 font-normal normal-case tracking-normal">
              ({activeJobs.length})
            </span>
          )}
        </span>
        <span className="block text-xs text-white/80 truncate">
          {progressText}
        </span>
      </span>

      <svg
        className="w-4 h-4 text-white/40 group-hover:text-white/80 transition-colors shrink-0"
        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
      </svg>
    </button>
  )
}
