import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { listDebates, deleteDebate } from '../services/api'
import { useLanguage } from '../utils/LanguageContext'

export default function HomePage() {
  const { t } = useLanguage()
  const [debates, setDebates] = useState([])
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState('')
  const [modeFilter, setModeFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const [tutorialOpen, setTutorialOpen] = useState(false)

  useEffect(() => {
    loadDebates()
  }, [modeFilter])

  async function loadDebates(query = '') {
    setLoading(true)
    setLoadError('')
    try {
      const data = await listDebates(20, 0, query, modeFilter)
      setDebates(data.debates)
      setTotal(data.total)
    } catch (err) {
      console.error('Failed to load debates:', err)
      setLoadError(t.loadDebatesError)
    } finally {
      setLoading(false)
    }
  }

  function handleSearch(e) {
    e.preventDefault()
    loadDebates(search)
  }

  async function handleDelete(debateId, e) {
    e.preventDefault()
    e.stopPropagation()
    if (!window.confirm(t.deleteConfirm)) return
    setDeleteError('')
    try {
      await deleteDebate(debateId)
      setDebates(prev => prev.filter(d => d.id !== debateId))
      setTotal(prev => prev - 1)
    } catch (err) {
      console.error('Failed to delete:', err)
      setDeleteError(t.deleteDebateError)
    }
  }

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4 mb-6 sm:mb-8">
        <div className="animate-fade-in">
          <h1 className="text-xl sm:text-2xl font-bold text-gradient">{t.completedAnalyses}</h1>
          <p className="text-white/40 mt-1 text-sm">
            {t.analysisCount(total)}
          </p>
        </div>
        <div className="flex items-center gap-2 sm:gap-3">
          <button
            type="button"
            onClick={() => setTutorialOpen(true)}
            className="px-3 sm:px-4 py-2 sm:py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/70
                       hover:border-white/20 hover:bg-white/10 hover:text-white transition-colors text-sm font-medium"
          >
            {t.tutorialButton}
          </button>
          <Link
            to="/analyze"
            className="btn-glow px-4 sm:px-5 py-2 sm:py-2.5 bg-accent-red hover:bg-brand-600 text-pure-white font-medium
                       rounded-lg text-sm whitespace-nowrap shadow-soft"
          >
            + {t.newAnalysis}
          </Link>
        </div>
      </div>

      {(loadError || deleteError) && (
        <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
          {loadError || deleteError}
        </div>
      )}

      {/* Mode filter */}
      <div className="flex gap-1 mb-4 bg-dark-600/30 rounded-lg p-1 w-fit overflow-x-auto">
        {[
          { key: '', label: t.all },
          { key: 'solo', label: t.soloOnly },
          { key: 'debate', label: t.debateOnly },
        ].map(opt => (
          <button
            key={opt.key}
            type="button"
            onClick={() => setModeFilter(opt.key)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
              modeFilter === opt.key
                ? 'chip-active text-white'
                : 'text-white/50 hover:text-white/80'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} className="mb-6">
        <div className="relative">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t.searchPlaceholder}
            className="w-full bg-dark-600 border border-white/10 rounded-lg px-4 py-3
                       text-white placeholder-white/30 focus:outline-none focus:border-accent-red/50
                       transition-colors text-sm"
          />
          <button
            type="submit"
            className="absolute right-2 top-1/2 -translate-y-1/2 px-4 py-1.5
                       bg-white/10 hover:bg-white/20 rounded-md text-white/60
                       text-xs transition-colors"
          >
            {t.search}
          </button>
        </div>
      </form>

      {/* Debate list */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="loader-dots"><span /><span /><span /></div>
        </div>
      ) : debates.length === 0 ? (
        <div className="text-center py-20 animate-fade-in">
          <p className="text-white/40 text-lg">{t.noAnalyses}</p>
          <Link to="/analyze" className="text-accent-red hover:underline mt-2 inline-block text-sm">
            {t.startFirst}
          </Link>
        </div>
      ) : (
        <div className="stagger grid gap-3 sm:gap-4 max-w-full">
          {debates.map((d) => (
            <DebateCard key={d.id} debate={d} onDelete={handleDelete} t={t} />
          ))}
        </div>
      )}

      {/* Tutorial modal */}
      {tutorialOpen && <TutorialModal t={t} onClose={() => setTutorialOpen(false)} />}
    </div>
  )
}

function TutorialModal({ t, onClose }) {
  return (
    <div className="fixed inset-0 z-[75] flex items-center justify-center p-4 sm:p-6">
      <button
        type="button"
        aria-label={t.tutorialClose}
        onClick={onClose}
        className="absolute inset-0 bg-black/75 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-2xl border border-white/10 bg-dark-600 shadow-2xl shadow-black/50 animate-fade-in">
        <div className="sticky top-0 z-10 border-b border-white/10 bg-dark-600/95 px-6 py-4 backdrop-blur flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{t.tutorialTitle}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-white/10 px-3 py-1 text-xs text-white/60 transition-colors hover:border-white/20 hover:text-white"
          >
            {t.tutorialClose}
          </button>
        </div>
        <div className="space-y-5 px-6 py-6">
          <TutorialStep
            title={t.tutorialStep1Title}
            text={t.tutorialStep1Text}
            color="bg-accent-red"
          />
          <TutorialStep
            title={t.tutorialStep2Title}
            text={t.tutorialStep2Text}
            color="bg-accent-blue"
          />
          <TutorialStep
            title={t.tutorialStep3Title}
            text={t.tutorialStep3Text}
            color="bg-green-500"
          />
        </div>
      </div>
    </div>
  )
}

function TutorialStep({ title, text, color }) {
  return (
    <div className="flex gap-4 items-start">
      <div className={`w-3 h-3 rounded-full ${color} flex-shrink-0 mt-1.5`} />
      <div>
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <p className="text-white/60 text-sm mt-1 leading-relaxed">{text}</p>
      </div>
    </div>
  )
}

function DebateCard({ debate, onDelete, t }) {
  const date = new Date(debate.created_at).toLocaleDateString('sl-SI', {
    day: 'numeric', month: 'short', year: 'numeric',
  })

  return (
    <Link
      to={`/debate/${debate.id}`}
      className="card-glass block rounded-2xl p-4 sm:p-5 group relative max-w-full"
    >
      <div className="flex items-start justify-between gap-3 max-w-full">
        <div className="flex-1 min-w-0 overflow-hidden">
          <h3 className="text-sm sm:text-base text-white font-semibold truncate group-hover:text-accent-red transition-colors">
            {debate.title || debate.topic || t.noTopic}
          </h3>
          {debate.title && debate.topic && (
            <p className="text-white/30 text-xs mt-0.5 truncate">{debate.topic}</p>
          )}
          <div className="flex flex-wrap items-center gap-2 sm:gap-3 mt-2 text-xs text-white/40">
            <span className={`px-2 py-0.5 rounded-full font-medium whitespace-nowrap ${
              debate.mode === 'debate_1v1' || debate.mode === 'debate'
                ? 'bg-accent-blue/20 text-accent-blue'
                : 'bg-accent-purple/20 text-purple-300'
            }`}>
              {debate.mode === 'debate_1v1' || debate.mode === 'debate' ? t.debateOnly : t.soloOnly}
            </span>
            <span className="whitespace-nowrap">{date}</span>
            {(debate.speaker_names || debate.speakers) && (
              <span className="truncate max-w-[120px] sm:max-w-none">{t.speakers}: {debate.speaker_names || debate.speakers}</span>
            )}
            {debate.duration_sec && (
              <span className="whitespace-nowrap">{formatDuration(debate.duration_sec)} {t.processing}</span>
            )}
          </div>
          {debate.summary && (
            <p className="text-white/50 text-sm mt-2 line-clamp-2 break-words">
              {debate.summary}
            </p>
          )}
        </div>

        <div className="ml-2 sm:ml-4 flex-shrink-0 flex flex-col items-end gap-2">
          {/* Accuracy badge */}

          {/* Delete button */}
          <button
            type="button"
            onClick={(e) => onDelete(debate.id, e)}
            className="text-white/20 hover:text-red-400 transition-colors text-xs px-2 py-1 rounded
                       hover:bg-red-500/10 whitespace-nowrap"
            title={t.delete}
          >
            ✕ {t.delete}
          </button>
        </div>
      </div>
    </Link>
  )
}

/** Processing time: seconds are unreadable past a minute — 3077 → "51 min". */
function formatDuration(seconds) {
  const total = Math.round(Number(seconds) || 0)
  if (total < 60) return `${total} s`
  const minutes = Math.round(total / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}
