import { useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { getDebate, downloadDebatePdf, rerunDebate, recheckDebate, submitAnalysis } from '../services/api'
import { useAuth } from '../services/auth'
import { useLanguage } from '../utils/LanguageContext'
import SpeakerTimeline from '../components/SpeakerTimeline'
import FactCheckPanel from '../components/FactCheckPanel'
import AppGuideModal from '../components/AppGuideModal'
import DebateEditor from '../components/DebateEditor'
import { buildSpeakerProfile } from '../utils/speakerMetrics'
import { getFactCheckClaims } from '../utils/factCheck'
import { DebateEditContext } from '../utils/DebateEditContext'

export default function DebateViewPage() {
  const { id } = useParams()
  const { t, lang, tv } = useLanguage()
  const { user } = useAuth()
  const [debate, setDebate] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('timeline')
  const [openArgumentId, setOpenArgumentId] = useState(null)
  const [guideOpen, setGuideOpen] = useState(false)
  const [editorOpen, setEditorOpen] = useState(false)
  const [pdfLoading, setPdfLoading] = useState(false)
  const [rerunLoading, setRerunLoading] = useState(false)
  const [recheckLoading, setRecheckLoading] = useState(false)
  const [rerunDialogOpen, setRerunDialogOpen] = useState(false)
  const [rerunLang, setRerunLang] = useState('sl')
  const navigate = useNavigate()

  // Ponovno preverjanje dejstev nad ISTO analizo. Argumenti, zmote in
  // zavrnitve ostanejo, osvežijo se samo viri in razsodbe, zato ni novega
  // vnosa in ni ponovnega izluščanja.
  async function handleRecheck() {
    if (recheckLoading || rerunLoading || !debate) return
    if (!window.confirm(t.recheckConfirm)) return
    setRecheckLoading(true)
    try {
      const job = await recheckDebate(debate.id)
      navigate(`/job/${job.job_id}`)
    } catch (e) {
      alert(e.message || t.recheckFailed)
    } finally {
      setRecheckLoading(false)
    }
  }

  function handleRerun() {
    if (rerunLoading || !debate) return
    setRerunLang(debate.language || 'sl')
    setRerunDialogOpen(true)
  }

  async function startRerun() {
    if (rerunLoading || !debate) return
    setRerunDialogOpen(false)
    setRerunLoading(true)
    try {
      const job = await rerunDebate(debate.id, '', rerunLang)
      navigate(`/job/${job.job_id}`)
    } catch (e) {
      // 409 = transcript no longer on disk → offer a FULL analysis from the
      // saved YouTube URL instead (download + transcription + analysis).
      if (e.status === 409 && debate.youtube_url) {
        if (window.confirm(t.rerunFullFallbackConfirm)) {
          try {
            const job = await submitAnalysis(
              debate.youtube_url,
              debate.mode || 'solo',
              rerunLang || debate.language || 'sl',
              debate.speaker_names || '',
              debate.title || '',
            )
            navigate(`/job/${job.job_id}`)
            return
          } catch (e2) {
            alert(e2.message || t.rerunFailed)
          }
        }
      } else if (e.status === 409) {
        alert(t.rerunNoUrl)
      } else {
        alert(e.message || t.rerunFailed)
      }
    } finally {
      setRerunLoading(false)
    }
  }

  async function handleExportPdf() {
    if (pdfLoading || !debate) return
    setPdfLoading(true)
    try {
      const base = debate.title || debate.topic || 'debate'
      await downloadDebatePdf(debate.id, `${base}.pdf`)
    } catch (e) {
      alert(e.message || 'PDF export failed')
    } finally {
      setPdfLoading(false)
    }
  }

  useEffect(() => {
    loadDebate()
  }, [id])

  useEffect(() => {
    setOpenArgumentId(null)
    setGuideOpen(false)
  }, [id])

  async function loadDebate() {
    setLoading(true)
    try {
      const data = await getDebate(id)
      setDebate(data)
    } catch (err) {
      setError(t.analysisNotFound)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-accent-red border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error || !debate) {
    return (
      <div className="text-center py-20">
        <p className="text-red-400 text-lg">{error}</p>
        <Link to="/" className="text-accent-red hover:underline mt-2 inline-block text-sm">
          {t.backToList}
        </Link>
      </div>
    )
  }

  const analysis = debate.analysis_json || {}
  const factCheck = debate.fact_check_json || {}
  const speakers = analysis.speakers || {}
  const meta = analysis.metadata || {}
  const participantRoles = meta.participants || {}
  // Solo shows exactly 1 primary speaker; debate shows exactly 2 debaters
  // (the app supports one-on-one debates only). A moderator is never a speaker
  // — they are reported separately by ModeratorPanel.
  // Legacy stored modes: 'debate_1v1' → debate, 'reaction' → solo.
  const isDebate = debate.mode === 'debate' || debate.mode === 'debate_1v1'
  const isSolo = !isDebate

  function getRolePriority(name) {
    const role = String(participantRoles[name] || '').toLowerCase()
    if (/(moderator|host|interviewer|audience)/.test(role)) return -1
    if (/(primary_speaker|debater|speaker)/.test(role)) return 2
    return 1
  }

  const sortedSpeakers = Object.keys(speakers)
    .filter(name => getRolePriority(name) >= 0)
    .sort((a, b) => {
      const roleDelta = getRolePriority(b) - getRolePriority(a)
      if (roleDelta !== 0) return roleDelta
      return (speakers[b]?.arguments || []).length - (speakers[a]?.arguments || []).length
    })
  const speakerNames = isSolo ? sortedSpeakers.slice(0, 1) : sortedSpeakers

  const speakerProfiles = Object.fromEntries(
    speakerNames.map(name => [
      name,
      buildSpeakerProfile({
        speakerName: name,
        speakerData: speakers[name] || {},
        analysis,
        factCheck,
      }),
    ]),
  )

  const claims = getFactCheckClaims(factCheck)

  const editCtx = {
    debateId: debate.id,
    canEdit: !!(user && debate.user_id === user.id),
    refresh: loadDebate,
  }

  return (
    <DebateEditContext.Provider value={editCtx}>
    <div>
      <div className="mb-6 sm:mb-8">
        <Link to="/" className="text-white/40 hover:text-white/60 text-sm mb-3 inline-block">
          &larr; {t.back}
        </Link>

        <div className="flex flex-col sm:flex-row sm:flex-wrap items-start justify-between gap-3 sm:gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl sm:text-2xl font-bold text-white break-words">
              {debate.title || meta.topic || t.analysis}
            </h1>
            {debate.title && meta.topic && (
              <p className="text-white/40 text-sm mt-1">{meta.topic}</p>
            )}
          </div>

          <div className="flex items-center gap-2">
            {user && debate.user_id === user.id && (
              <>
                <button
                  type="button"
                  onClick={() => setEditorOpen(true)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-white/70 transition-colors hover:border-accent-red/40 hover:bg-accent-red/10 hover:text-white"
                  title={t.editDebateTitle}
                >
                  {t.editDebate}
                </button>
                <button
                  type="button"
                  onClick={handleRerun}
                  disabled={rerunLoading}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-white/70 transition-colors hover:border-accent-blue/40 hover:bg-accent-blue/10 hover:text-white disabled:opacity-50"
                  title={t.rerunTitle}
                >
                  {rerunLoading ? t.rerunRunning : t.rerun}
                </button>
                <button
                  type="button"
                  onClick={handleRecheck}
                  disabled={recheckLoading || rerunLoading}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-white/70 transition-colors hover:border-accent-blue/40 hover:bg-accent-blue/10 hover:text-white disabled:opacity-50"
                  title={t.recheckTitle}
                >
                  {recheckLoading ? t.recheckRunning : t.recheck}
                </button>
              </>
            )}
            <button
              type="button"
              onClick={handleExportPdf}
              disabled={pdfLoading}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-white/70 transition-colors hover:border-white/20 hover:bg-white/10 hover:text-white disabled:opacity-50"
            >
              {pdfLoading ? t.exporting : t.exportPdf}
            </button>
            <button
              type="button"
              onClick={() => setGuideOpen(true)}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-white/70 transition-colors hover:border-white/20 hover:bg-white/10 hover:text-white"
            >
              {t.whatDoesAnalysisMean}
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 sm:gap-4 mt-3 text-xs text-white/40 flex-wrap">
          <span className={`px-2 py-0.5 rounded-full font-medium ${
            isDebate
              ? 'bg-accent-blue/20 text-accent-blue'
              : 'bg-accent-purple/20 text-purple-300'
          }`}>
            {isDebate ? t.debateOnly : t.soloOnly}
          </span>
          {debate.speaker_names && (
            <span className="text-white/50">
              {debate.speaker_names.split(',').map(n => n.trim()).join(' vs ')}
            </span>
          )}
          <span>{new Date(debate.created_at).toLocaleDateString('sl-SI')}</span>
          {debate.youtube_url && (
            <a
              href={debate.youtube_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent-red hover:underline"
            >
              {t.youtubeVideo}
            </a>
          )}
        </div>
      </div>

      <div className="flex gap-1 mb-6 sm:mb-8 bg-dark-600/30 rounded-lg p-1 w-full sm:w-fit overflow-x-auto">
        {[
          { key: 'timeline', label: t.timeline },
          { key: 'factcheck', label: t.factCheck },
          { key: 'report', label: t.report },
        ].map(tab => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={`px-3 sm:px-4 py-2 rounded-md text-sm font-medium transition-colors whitespace-nowrap flex-1 sm:flex-none ${
              activeTab === tab.key
                ? 'bg-accent-red text-pure-white'
                : 'text-white/50 hover:text-white/80'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'timeline' && (
        <div className="space-y-12">
          {speakerNames.length >= 2 && (
            <p className="text-white/30 text-xs -mt-2">{t.diarizationNote}</p>
          )}
          <ModeratorPanel moderator={analysis.moderator} t={t} tv={tv} />
          {speakerNames.map(name => (
            <SpeakerTimeline
              key={name}
              speakerName={name}
              speakerProfile={speakerProfiles[name]}
              isDebateMode={isDebate}
              soloEvaluation={analysis.solo_evaluation || {}}
              openArgumentId={openArgumentId}
              onToggleArgument={nextId => {
                setOpenArgumentId(currentId => currentId === nextId ? null : nextId)
              }}
            />
          ))}
        </div>
      )}

      {activeTab === 'factcheck' && (
        <FactCheckPanel factCheck={factCheck} />
      )}

      {activeTab === 'report' && (
        <ReportPanel claims={claims} factCheck={factCheck} t={t} />
      )}


      <AppGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} />

      {editorOpen && user && debate.user_id === user.id && (
        <DebateEditor
          debateId={debate.id}
          analysis={analysis}
          debateTitle={debate.title || ''}
          lang={lang}
          onClose={() => setEditorOpen(false)}
          onSaved={async () => { await loadDebate() }}
        />
      )}

      {rerunDialogOpen && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center p-4">
          <button
            type="button"
            aria-label={t.rerunCancelBtn}
            onClick={() => setRerunDialogOpen(false)}
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
          />
          <div className="relative w-full max-w-sm rounded-2xl border border-white/10 bg-dark-600 p-5 shadow-2xl">
            <h3 className="text-white font-semibold text-base mb-2">{t.rerun}</h3>
            <p className="text-white/50 text-xs leading-relaxed mb-4">{t.rerunConfirm}</p>
            <p className="text-white/70 text-sm font-medium mb-2">{t.rerunLangTitle}</p>
            <div className="flex gap-2 mb-5">
              {[['sl', 'Slovenščina'], ['en', 'English']].map(([code, label]) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => setRerunLang(code)}
                  className={`flex-1 rounded-xl border px-3 py-2 text-sm transition-colors ${
                    rerunLang === code
                      ? 'border-accent-blue bg-accent-blue/15 text-white'
                      : 'border-white/10 bg-white/5 text-white/60 hover:bg-white/10'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setRerunDialogOpen(false)}
                className="flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm text-white/70 hover:bg-white/10"
              >
                {t.rerunCancelBtn}
              </button>
              <button
                type="button"
                onClick={startRerun}
                className="flex-1 rounded-xl border border-accent-blue/40 bg-accent-blue/20 px-4 py-2 text-sm font-medium text-white hover:bg-accent-blue/30"
              >
                {t.rerunStartBtn}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </DebateEditContext.Provider>
  )
}

/* ── Report Panel ─────────────────────────────────────── */
function ReportPanel({ claims, factCheck, t }) {
  const summary = factCheck.summary || {}
  const verdictBreakdown = summary.verdict_breakdown || {}

  if (!claims || claims.length === 0) {
    return (
      <div className="bg-dark-600/50 border border-white/5 rounded-xl p-6 text-center">
        <p className="text-white/40">{t.reportNotAvailable}</p>
      </div>
    )
  }

  const verdictColor = {
    TRUE: 'bg-green-500/20 text-green-400 border-green-500/30',
    PARTIALLY_TRUE: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    MISLEADING: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
    FALSE: 'bg-red-500/20 text-red-400 border-red-500/30',
    UNVERIFIABLE: 'bg-white/10 text-white/40 border-white/10',
  }

  const verdictIcon = {
    TRUE: '✓',
    PARTIALLY_TRUE: '~',
    MISLEADING: '⚠',
    FALSE: '✕',
    UNVERIFIABLE: '?',
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Overview stats */}
      <div className="bg-dark-600/50 border border-white/5 rounded-xl p-5">
        <h3 className="text-sm font-semibold text-white/60 mb-4 uppercase tracking-wider">{t.reportOverview}</h3>
        <div className="grid grid-cols-3 sm:grid-cols-5 gap-2 sm:gap-3">
          <MiniStat label={t.totalClaims} value={claims.length} />
          <MiniStat label={t.trueClaims} value={verdictBreakdown.TRUE || 0} color="text-green-400" />
          <MiniStat label={t.falseClaims} value={verdictBreakdown.FALSE || 0} color="text-red-400" />
          <MiniStat label={t.partiallyTrue} value={verdictBreakdown.PARTIALLY_TRUE || 0} color="text-yellow-400" />
          <MiniStat label={t.misleading} value={(verdictBreakdown.MISLEADING || 0) + (verdictBreakdown.UNVERIFIABLE || 0)} color="text-orange-400" />
        </div>
      </div>

      {/* Claims list */}
      <div className="space-y-3">
        {claims.map((claim, i) => {
          const verdict = claim.verdict || claim.verdict_label || 'UNVERIFIABLE'
          const colors = verdictColor[verdict] || verdictColor.UNVERIFIABLE
          const icon = verdictIcon[verdict] || '?'
          const translatedVerdict = t[verdict] || verdict
          const allSources = _getAllSources(claim)

          return (
            <div
              key={i}
              className={`border rounded-xl p-4 ${colors.split(' ').find(c => c.startsWith('border-'))} bg-dark-600/40`}
            >
              <div className="flex items-start gap-3">
                {/* Verdict icon */}
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 text-sm font-bold ${colors}`}>
                  {icon}
                </div>

                <div className="flex-1 min-w-0">
                  {/* Claim text */}
                  <p className="text-white/90 text-sm font-medium leading-relaxed">
                    {claim.exact_claim || claim.claim}
                  </p>

                  {/* Speaker and verdict */}
                  <div className="flex flex-wrap items-center gap-2 sm:gap-3 mt-2">
                    {claim.speaker && (
                      <span className="text-white/40 text-xs">
                        {t.claimSpeaker}: {claim.speaker}
                      </span>
                    )}
                    <span className={`text-xs px-2 py-0.5 rounded-full font-bold ${colors}`}>
                      {translatedVerdict}
                    </span>
                  </div>

                  {/* Explanation */}
                  {claim.explanation && (
                    <p className="text-white/50 text-xs mt-2 leading-relaxed">{claim.explanation}</p>
                  )}

                  {/* Sources (merged from sources + perplexity citations) */}
                  {allSources.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {allSources.map((src, j) => (
                        <a
                          key={j}
                          href={src.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[11px] text-accent-blue hover:underline"
                        >
                          [{j + 1}] {src.title || _domainFromUrl(src.url)}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function MiniStat({ label, value, color = 'text-white' }) {
  return (
    <div className="text-center">
      <div className={`text-xl font-bold ${color}`}>{value}</div>
      <div className="text-[10px] text-white/30 mt-0.5 uppercase tracking-wider">{label}</div>
    </div>
  )
}

/** Zberi vire brez podvojenih povezav. perplexity_data je tu zaradi analiz,
 *  shranjenih pred prehodom na en razsojevalni korak. */
function _getAllSources(claim) {
  const seen = new Set()
  const result = []
  for (const src of claim.sources || []) {
    const url = src.url || src
    if (url && !seen.has(url)) {
      seen.add(url)
      result.push(typeof src === 'string' ? { url: src } : src)
    }
  }
  for (const url of ((claim.perplexity_data || {}).citations || [])) {
    if (url && !seen.has(url)) {
      seen.add(url)
      result.push({ url })
    }
  }
  return result
}

function _domainFromUrl(url) {
  try { return new URL(url).hostname.replace('www.', '') }
  catch { return url }
}

/**
 * Moderator panel — descriptive only.
 *
 * A moderator is never one of the two debaters: they are not scored and their
 * questions are not rebuttals. This panel exists
 * so the reader can still see how much the moderator shaped the exchange —
 * how many questions they asked, which ones, and whether they pressed one
 * debater harder than the other.
 */
function ModeratorPanel({ moderator, t, tv }) {
  if (!moderator || !moderator.present) return null

  const questions = Array.isArray(moderator.questions) ? moderator.questions : []
  const count = moderator.question_count || questions.length
  // pressed_more is either a speaker name (leave it) or a special value.
  const pressed = ['balanced', 'n/a'].includes(moderator.pressed_more)
    ? tv('pressed_more', moderator.pressed_more)
    : moderator.pressed_more

  return (
    <div className="rounded-lg border border-white/10 bg-dark-600/30 p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
        <span className="text-sm font-semibold text-white/80">{t.moderatorTitle}</span>
        {moderator.name && <span className="text-sm text-white/60">{moderator.name}</span>}
        <span className="text-xs text-white/40">{count} {t.moderatorQuestions}</span>
        {moderator.pressed_more && (
          <span className="text-xs text-white/40">
            {t.moderatorPressed}: {pressed}
          </span>
        )}
      </div>

      {moderator.notes && (
        <p className="text-sm text-white/50 mb-2">{moderator.notes}</p>
      )}

      {questions.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-white/50 hover:text-white/80">
            {t.moderatorShowQuestions}
          </summary>
          <ul className="mt-2 space-y-1 list-disc list-inside text-white/60">
            {questions.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
        </details>
      )}

      <p className="text-[11px] text-white/25 mt-2">{t.moderatorNotScored}</p>
    </div>
  )
}
