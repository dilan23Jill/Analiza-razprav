import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLanguage } from '../utils/LanguageContext'
import { useDebateEdit } from '../utils/DebateEditContext'
import { editDebate } from '../services/api'
import enumLabels from '../enumLabels.json'

// Fallacy names offered in the manual-entry dropdown, sorted by their
// translated label. Reading the shared label file means the dropdown can only
// produce names the closed vocabulary already knows, so a hand-added fallacy is
// shaped exactly like a detected one. The CATEGORY is not offered: the server
// derives it from the name, the same rule it applies to the model's output.
function fallacyOptions(tv) {
  return Object.keys(enumLabels.fallacy || {})
    .map(name => ({ name, label: tv('fallacy', name) }))
    .sort((a, b) => a.label.localeCompare(b.label, 'sl'))
}


// ── Preverjene trditve ob premisi ──────────────────────────────────────────
// Vsaka preverjena trditev nosi številko premise, iz katere je nastala, zato
// jo je mogoče prikazati tam, kjer je bila izrečena, in ne šele v ločenem
// seznamu pod argumentom. Trditev brez te številke ostane v spodnjem bloku.

const VERDICT_DOT = {
  TRUE: 'bg-green-400',
  PARTIALLY_TRUE: 'bg-yellow-400',
  MISLEADING: 'bg-orange-400',
  FALSE: 'bg-red-400',
  UNVERIFIABLE: 'bg-white/30',
}

const VERDICT_TEXT = {
  TRUE: 'text-green-400',
  PARTIALLY_TRUE: 'text-yellow-400',
  MISLEADING: 'text-orange-400',
  FALSE: 'text-red-400',
  UNVERIFIABLE: 'text-white/40',
}

const VERDICT_ORDER = ['TRUE', 'PARTIALLY_TRUE', 'MISLEADING', 'FALSE', 'UNVERIFIABLE']

function premiseIndexOf(claim) {
  const raw = claim?.premise_index
  const n = typeof raw === 'string' ? Number(raw) : raw
  return Number.isInteger(n) ? n : null
}

function claimsForPremise(claims, premiseIndex) {
  return (claims || []).filter(c => premiseIndexOf(c) === premiseIndex)
}

/** Pika v barvi razsodbe. Klik odpre okno z viri te trditve. */
function PremiseVerdictDots({ claims }) {
  const [openIdx, setOpenIdx] = useState(null)
  const { t } = useLanguage()
  if (!claims || claims.length === 0) return null

  return (
    <span className="flex flex-shrink-0 items-center gap-1 pt-1">
      {claims.map((claim, i) => {
        const verdict = claim.verdict || 'UNVERIFIABLE'
        return (
          <button
            key={i}
            type="button"
            onClick={() => setOpenIdx(i)}
            title={`${t[verdict] || verdict} — ${t.sources}`}
            aria-label={`${t[verdict] || verdict} — ${t.sources}`}
            className={`h-2.5 w-2.5 rounded-full transition-transform hover:scale-150 ${
              VERDICT_DOT[verdict] || VERDICT_DOT.UNVERIFIABLE
            }`}
          />
        )
      })}
      {openIdx !== null && createPortal(
        <ClaimSourcesModal claim={claims[openIdx]} onClose={() => setOpenIdx(null)} />,
        document.body,
      )}
    </span>
  )
}

/** Okno s trditvijo, razsodbo, seštevkom po petih kategorijah in viri. */
function ClaimSourcesModal({ claim, onClose }) {
  const { t } = useLanguage()
  const verdict = claim.verdict || 'UNVERIFIABLE'
  const sources = claim.sources || []
  const tally = claim.source_verdicts || {}
  const labelled = VERDICT_ORDER.filter(v => (tally[v] || 0) > 0)

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4">
      <button
        type="button"
        onClick={onClose}
        aria-label={t.argCloseLabel}
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
      />
      <div className="relative z-10 w-full max-w-2xl max-h-[80vh] overflow-y-auto rounded-2xl border border-white/10 bg-dark-900 p-5 shadow-2xl">
        <div className="flex items-start justify-between gap-3 mb-3">
          <p className="text-sm text-white/85 flex-1">
            {claim.exact_claim || claim.claim || ''}
          </p>
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold flex-shrink-0 bg-white/10 ${
            VERDICT_TEXT[verdict] || VERDICT_TEXT.UNVERIFIABLE
          }`}>
            {t[verdict] || verdict}
          </span>
        </div>

        {claim.explanation && (
          <p className="text-white/50 text-xs leading-relaxed mb-3">{claim.explanation}</p>
        )}

        {labelled.length > 0 && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 mb-3 text-[11px]">
            {labelled.map(v => (
              <span key={v} className={VERDICT_TEXT[v]}>
                {t[v] || v}: <span className="font-semibold">{tally[v]}</span>
              </span>
            ))}
          </div>
        )}

        <h6 className="text-white/40 text-[10px] uppercase tracking-wider mb-2">
          {t.sources} · {sources.length}
        </h6>
        <ol className="space-y-1.5">
          {sources.map((s, i) => {
            const sv = s.source_verdict
            return (
              <li key={i} className="flex items-start gap-2 text-xs">
                <span className="text-white/25 w-5 flex-shrink-0 text-right">{i + 1}.</span>
                <span className={`mt-1 h-2 w-2 flex-shrink-0 rounded-full ${
                  sv ? (VERDICT_DOT[sv] || VERDICT_DOT.UNVERIFIABLE) : 'bg-white/15'
                }`} />
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex-1 text-accent-blue hover:text-blue-300 underline underline-offset-2 break-all"
                >
                  {s.title || extractDomain(s.url || '')}
                </a>
                {sv && (
                  <span className={`flex-shrink-0 ${VERDICT_TEXT[sv]}`}>
                    {t[sv] || sv}
                  </span>
                )}
              </li>
            )
          })}
        </ol>
        {sources.length === 0 && (
          <p className="text-white/30 text-xs">{t.reportNotAvailable}</p>
        )}
      </div>
    </div>
  )
}

export default function ArgumentNode({
  index,
  speakerName,
  argument,
  relatedClaims,
  relatedFallacies,
  critique = null,
  rebuttals = [],
  isDebateMode = true,
  unsupportedClaims = [],
  userAdded = false,
  side = 'right',
  isOpen = false,
  onToggle,
  onClose,
}) {
  const premises = argument.premises || []
  const type = argument.type || 'factual'

  const falseClaims = relatedClaims.filter(claim =>
    ['FALSE', 'MISLEADING'].includes(claim.verdict)
  )

  const hasIssues =
    falseClaims.length > 0 ||
    relatedFallacies.length > 0 ||
    critique?.issues?.length > 0 ||
    (unsupportedClaims && unsupportedClaims.length > 0)

  useEffect(() => {
    if (!isOpen) return undefined

    function handleKeydown(event) {
      if (event.key === 'Escape') onClose?.()
    }

    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }, [isOpen, onClose])

  const nodeButton = (
    <div className="relative z-10 flex-shrink-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className={`w-10 h-10 rounded-full border-4 border-white/20
                   cursor-pointer hover:scale-125 transition-transform node-pulse
                   flex items-center justify-center shadow-lg
                   ${hasIssues
                     ? 'bg-orange-500 shadow-orange-500/30'
                     : 'bg-accent-red shadow-red-500/30'
                   }
                   ${isOpen ? 'scale-110 ring-4 ring-white/10' : ''}`}
        title={`Argument ${index + 1}: ${argument.argument}`}
      >
        <span className="text-white text-xs font-bold">{index + 1}</span>
      </button>
    </div>
  )

  return (
    <div className="relative w-full my-3 md:my-6 group">
      {/* Mobile layout: always node-left, text-right */}
      <div className="flex items-center gap-3 md:hidden">
        {nodeButton}
        <div className="flex-1 min-w-0">
          <ArgLabel argument={argument} onClick={onToggle} />
        </div>
      </div>

      {/* Desktop layout: alternating left/right */}
      <div className="hidden md:flex items-center w-full">
        {side === 'left' && (
          <div className="w-[47%] pr-6 text-right">
            <ArgLabel argument={argument} onClick={onToggle} />
          </div>
        )}
        {side === 'right' && <div className="w-[47%]" />}

        {nodeButton}

        {side === 'right' && (
          <div className="w-[47%] pl-6">
            <ArgLabel argument={argument} onClick={onToggle} />
          </div>
        )}
        {side === 'left' && <div className="w-[47%]" />}
      </div>

      {isOpen && typeof document !== 'undefined' && createPortal(
        <ArgumentModal
          index={index}
          speakerName={speakerName}
          argument={argument}
          premises={premises}
          critique={critique}
          rebuttals={rebuttals}
          falseClaims={falseClaims}
          relatedFallacies={relatedFallacies}
          relatedClaims={relatedClaims}
          isDebateMode={isDebateMode}
          unsupportedClaims={unsupportedClaims}
          userAdded={userAdded}
          onClose={onClose}
        />,
        document.body
      )}
    </div>
  )
}

function ArgumentModal({
  index,
  speakerName,
  argument,
  premises,
  critique,
  rebuttals,
  falseClaims,
  relatedFallacies,
  relatedClaims,
  isDebateMode = true,
  unsupportedClaims = [],
  userAdded = false,
  onClose,
}) {
  const { t } = useLanguage()
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 sm:p-6">
      <button
        type="button"
        onClick={onClose}
        aria-label={t.argCloseLabel}
        className="absolute inset-0 bg-black/75 backdrop-blur-sm"
      />

      <div className="relative w-full max-w-4xl max-h-[95vh] sm:max-h-[88vh] overflow-y-auto rounded-xl sm:rounded-2xl border border-white/10 bg-dark-600 shadow-2xl shadow-black/50 animate-fade-in">
        <div className="sticky top-0 z-10 border-b border-white/5 bg-dark-600/95 px-4 sm:px-5 py-3 sm:py-4 backdrop-blur">
          <div className="flex items-start justify-between gap-3 sm:gap-4">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.25em] text-accent-blue">
                {speakerName || t.argSpeaker}
              </p>
              <h4 className="mt-2 text-base font-semibold text-white">
                Argument {index + 1}
                {userAdded && (
                  <span className="ml-2 text-[10px] font-normal text-white/40 border border-white/10 rounded px-1.5 py-0.5">
                    {t.argUserAdded}
                  </span>
                )}
              </h4>
            </div>
          </div>
        </div>

        {/* Premises first — the argument is DERIVED from them below */}
        {premises.length > 0 && (
          <div className="px-5 py-4 border-b border-white/5">
            <h5 className="text-accent-blue text-xs font-semibold uppercase tracking-wider mb-3">
              {t.argPremises}
            </h5>
            <ol className="space-y-2">
              {premises.map((premise, premiseIndex) => (
                <li key={premiseIndex} className="flex items-start gap-3 text-sm text-white/70">
                  <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-accent-blue/15 text-[11px] font-semibold text-accent-blue">
                    {premiseIndex + 1}
                  </span>
                  <span className="flex-1">{premise}</span>
                  <PremiseVerdictDots
                    claims={claimsForPremise(relatedClaims, premiseIndex)}
                  />
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* Derived argument (conclusion) below the premises */}
        <div className="px-5 py-4 border-b border-white/5 bg-white/[0.02]">
          <h5 className="text-accent-blue text-xs font-semibold uppercase tracking-wider mb-2">
            {t.argDerived}
          </h5>
          <p className="text-sm leading-relaxed text-white/85">
            {argument.argument}
          </p>
        </div>

        <CritiqueSection
          critique={critique}
          rebuttals={rebuttals}
          falseClaims={falseClaims}
          relatedFallacies={relatedFallacies}
          relatedClaims={relatedClaims}
          isDebateMode={isDebateMode}
          speakerName={speakerName}
          argId={argument.arg_id || ''}
        />

        {unsupportedClaims && unsupportedClaims.length > 0 && (
          <div className="px-5 py-3 border-b border-white/5">
            <h5 className="text-yellow-400 text-xs font-semibold uppercase tracking-wider mb-2">
              {t.stUnsupportedClaims}
            </h5>
            <ul className="space-y-1">
              {unsupportedClaims.map((claim, claimIndex) => (
                <li key={claimIndex} className="text-white/60 text-xs leading-relaxed">- {claim}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="border-t border-white/5 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-medium text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          >
            {t.argClose}
          </button>
        </div>
      </div>
    </div>
  )
}

function CritiqueSection({ critique, rebuttals, falseClaims, relatedFallacies, relatedClaims = [], isDebateMode = true, speakerName = '', argId = '' }) {
  const { t, tv } = useLanguage()
  const { debateId, canEdit, refresh } = useDebateEdit()
  const [savingReview, setSavingReview] = useState(null)
  const [busyFallacy, setBusyFallacy] = useState(false)
  const [addingFallacy, setAddingFallacy] = useState(false)
  const [newFallacyType, setNewFallacyType] = useState('')
  const [newFallacyQuote, setNewFallacyQuote] = useState('')
  // ── Manual correction of fallacies ────────────────────────────────────
  // Fallacy detection errs in BOTH directions, so the reader must be able to
  // correct it in both: delete an invented one, add a missed one, fix a wrong
  // name. Without this the reference annotation for the evaluation would have
  // to be kept outside the application.
  async function runFallacyOp(op, payload) {
    if (!canEdit || !debateId || busyFallacy) return
    setBusyFallacy(true)
    try {
      await editDebate(debateId, [{ op, payload }])
      refresh?.()
    } catch (e) {
      alert(e.message || 'Save failed')
    } finally {
      setBusyFallacy(false)
    }
  }

  async function addFallacy() {
    const type = newFallacyType.trim()
    const evidence = newFallacyQuote.trim()
    if (!type || !evidence) return
    await runFallacyOp('add_fallacy', {
      fallacy: { speaker: speakerName, type, evidence, target_arg_id: argId },
    })
    setNewFallacyType('')
    setNewFallacyQuote('')
    setAddingFallacy(false)
  }

  // Reviewing a DETECTED fallacy does not delete it: detection and the verdict
  // on detection must survive as two separate records, otherwise there is
  // nothing left to compute precision from.
  async function reviewFallacy(fallacyIndex, verdict, current) {
    if (!canEdit || !debateId || savingReview !== null) return
    const next = current === verdict ? null : verdict // klik na isto oznako = razveljavi
    setSavingReview('f' + fallacyIndex)
    try {
      await editDebate(debateId, [{
        op: 'review_fallacy', payload: { index: fallacyIndex, verdict: next },
      }])
      refresh?.()
    } catch (e) {
      alert(e.message || 'Save failed')
    } finally {
      setSavingReview(null)
    }
  }

  const trueClaims = relatedClaims.filter(claim =>
    ['TRUE', 'PARTIALLY_TRUE', 'UNVERIFIABLE'].includes(claim.verdict)
  )
  const hasAnything =
    critique?.issues?.length > 0 || relatedFallacies.length > 0 ||
    relatedClaims.length > 0 ||
    (isDebateMode && (critique || rebuttals.length > 0 || falseClaims.length > 0))

  if (!hasAnything) return null

  // Izmenjava prihaja iz koraka, ki bere prepis. Starejše analize nosijo ista
  // polja na kritiki, zato se ta bere kot rezerva.
  const firstRebuttal = rebuttals[0] || null
  const wasRebutted = rebuttals.length > 0 || critique?.was_rebutted
  const rebuttalSummary = firstRebuttal?.rebuttal_content || critique?.rebuttal_summary || ''
  const defence = firstRebuttal?.response || critique?.counter_rebuttal || ''

  return (
    <div className="px-5 py-3 border-b border-white/5 bg-gradient-to-b from-red-500/5 to-orange-500/5">

      {/* Debate exchange flow — only in debate/reaction modes */}
      {isDebateMode && (wasRebutted || critique?.counter) && (
        <div className="mb-3">
          <h5 className="text-accent-blue text-xs font-semibold uppercase tracking-wider mb-2">
            {t.argExchangeFlow}
          </h5>
          <div className="space-y-2">
            {/* Step 1: Opponent's rebuttal */}
            {wasRebutted ? (
              <div className="p-2.5 bg-dark-800/60 rounded-lg border border-accent-blue/20">
                <div className="text-[10px] text-accent-blue/60 mb-1 font-semibold uppercase tracking-wider">
                  {t.argOpponentRebuttal}
                </div>
                <p className="text-white/70 text-xs leading-relaxed">
                  {rebuttalSummary || critique?.counter || ''}
                </p>
              </div>
            ) : critique?.counter ? (
              <div className="p-2.5 bg-dark-800/60 rounded-lg border border-white/5">
                <div className="text-[10px] text-white/30 mb-1">
                  {critique.counter.includes('Not addressed') ? t.argNotAddressed : t.argOpponentResponse}
                </div>
                <p className="text-white/60 text-xs leading-relaxed">{critique.counter}</p>
              </div>
            ) : null}

            {/* Step 2: Counter-rebuttal (speaker's defense) */}
            {defence && (
              <div className="p-2.5 bg-dark-800/60 rounded-lg border border-orange-500/20">
                <div className="text-[10px] text-orange-400/70 mb-1 font-semibold uppercase tracking-wider">
                  {t.argDefense}
                </div>
                <p className="text-white/70 text-xs leading-relaxed">
                  {defence}
                </p>
              </div>
            )}

          </div>
        </div>
      )}

      {/* Rebuttals from rebuttal_mapping pass — only in debate/reaction modes */}
      {isDebateMode && rebuttals.length > 0 && (
        <div className="mb-3">
          {rebuttals.map((rebuttal, rebuttalIndex) => (
            <div
              key={rebuttalIndex}
              className="p-2.5 bg-dark-800/60 rounded-lg border border-white/5 mb-2 last:mb-0"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] text-white/30 uppercase tracking-wider">
                  {t.argRebuttalBy} ({rebuttal.by})
                </span>
                {rebuttal.user_added && (
                  <span className="text-[10px] text-white/40 border border-white/10 rounded px-1.5 py-0.5">
                    {t.argUserAdded}
                  </span>
                )}
              </div>
              <p className="text-white/60 text-xs leading-relaxed">{rebuttal.rebuttal_content}</p>
              {rebuttal.response && (
                <p className="text-white/40 text-[11px] mt-1.5 italic">
                  {t.argResponse}: {rebuttal.response}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Legacy path: analyses stored before validity and fallacies were merged
          into one pass carry a free-text `issues` list. New analyses do not —
          what is wrong with an argument is now said once, as a named fallacy.
          Shown read-only so old analyses still render as they were saved. */}
      {critique?.issues?.length > 0 && (
        <div className="mb-3">
          <h5 className="text-xs font-semibold uppercase tracking-wider mb-2 text-orange-400">
            {t.argDebatablePoints}
          </h5>
          {critique.issues.map((issue, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs mb-1 text-orange-300/80">
              <span className="mt-px flex-shrink-0 text-orange-400">?</span>
              <span className="flex-1">{issue}</span>
            </div>
          ))}
        </div>
      )}

      {/* Zmote. Sistem jih samo poimenuje, ne stopnjuje. Bralec jih lahko
          potrdi, zavrne, popravi ime ali odstrani. */}
      {(() => {
        const v = { box: 'bg-purple-500/10', name: 'text-purple-300',
                    btn: 'border-purple-400/40 text-purple-300 hover:bg-purple-500/10' }

        const FallacyRow = ({ fallacy }) => {
          return (
          <div className={`mb-2 last:mb-0 p-2 ${v.box} rounded-lg`}>
            <div className="flex items-center gap-2 flex-wrap">
              {canEdit ? (
                <select
                  value={fallacy.type}
                  disabled={busyFallacy}
                  onChange={e => runFallacyOp('edit_fallacy', {
                    index: fallacy._index, fields: { type: e.target.value },
                  })}
                  title={t.fallacyRetypeTitle}
                  className={`${v.name} text-xs font-medium bg-transparent border border-white/10
                              rounded px-1 py-0.5 max-w-[16rem] hover:border-white/25 cursor-pointer`}
                >
                  {fallacyOptions(tv).map(o => (
                    <option key={o.name} value={o.name} className="bg-dark-700">{o.label}</option>
                  ))}
                  {!enumLabels.fallacy[fallacy.type] && (
                    <option value={fallacy.type} className="bg-dark-700">{tv('fallacy', fallacy.type)}</option>
                  )}
                </select>
              ) : (
                <span className={`${v.name} text-xs font-medium`}>{tv('fallacy', fallacy.type)}</span>
              )}
              {fallacy.category && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-white/40"
                  title={t[`fallacyCat_${fallacy.category}_desc`]}
                >
                  {tv('fallacy_category', fallacy.category)}
                </span>
              )}
              {fallacy.user_added && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-white/40">
                  {t.argUserAdded}
                </span>
              )}
              {canEdit && (
                <button
                  type="button"
                  disabled={busyFallacy}
                  title={t.fallacyDeleteTitle}
                  onClick={() => {
                    if (window.confirm(t.fallacyDeleteConfirm)) {
                      runFallacyOp('delete_fallacy', { index: fallacy._index })
                    }
                  }}
                  className="ml-auto w-5 h-5 rounded border border-white/15 text-white/40 text-[11px]
                             leading-none hover:text-red-300 hover:border-red-400/50 transition-colors"
                >×</button>
              )}
            </div>
            {fallacy.evidence && (
              <p className="text-white/40 text-[11px] mt-1 italic">„{fallacy.evidence}”</p>
            )}
            <p className="text-white/50 text-xs mt-1">{fallacy.explanation}</p>
            {canEdit && (
              <div className="flex items-center gap-1.5 mt-2">
                <span className="text-[10px] text-white/30 mr-0.5">{t.reviewPrompt}</span>
                {[['confirmed', t.reviewConfirm, 'text-green-300 border-green-400/50 bg-green-500/15'],
                  ['dismissed', t.reviewDismiss, 'text-red-300 border-red-400/50 bg-red-500/15']]
                  .map(([val, lbl, active]) => (
                  <button
                    key={val}
                    type="button"
                    disabled={savingReview !== null}
                    onClick={() => reviewFallacy(fallacy._index, val, fallacy.review || null)}
                    className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${
                      fallacy.review === val ? active
                        : 'border-white/10 text-white/35 hover:text-white/70 hover:border-white/25'}`}
                  >{lbl}</button>
                ))}
              </div>
            )}
          </div>
          )
        }

        return (
          <>
            {relatedFallacies.length > 0 && (
              <div className="mb-3">
                <h5 className="text-purple-400 text-xs font-semibold uppercase tracking-wider mb-2">
                  {t.argFallacies}
                </h5>
                {relatedFallacies.map(f => <FallacyRow key={f._index} fallacy={f} />)}
              </div>
            )}

            {/* Add a fallacy the system missed. Detection errs in both
                directions, so the reader needs both corrections. */}
            {canEdit && (
              <div className="mb-3">
                {!addingFallacy ? (
                  <button
                    type="button"
                    onClick={() => setAddingFallacy(true)}
                    className="text-[11px] text-white/40 hover:text-purple-300 border border-white/10
                               hover:border-purple-400/40 rounded px-2 py-1 transition-colors"
                  >+ {t.fallacyAdd}</button>
                ) : (
                  <div className="p-2 rounded-lg border border-white/10 bg-white/[0.03]">
                    <select
                      value={newFallacyType}
                      onChange={e => setNewFallacyType(e.target.value)}
                      className="w-full text-xs bg-dark-700 border border-white/10 rounded px-2 py-1 mb-2"
                    >
                      <option value="">{t.fallacyPickType}</option>
                      {fallacyOptions(tv).map(o => (
                        <option key={o.name} value={o.name}>{o.label}</option>
                      ))}
                    </select>
                    <textarea
                      value={newFallacyQuote}
                      onChange={e => setNewFallacyQuote(e.target.value)}
                      placeholder={t.fallacyQuotePlaceholder}
                      rows={2}
                      className="w-full text-xs bg-dark-700 border border-white/10 rounded px-2 py-1 mb-2"
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={busyFallacy || !newFallacyType || !newFallacyQuote.trim()}
                        onClick={addFallacy}
                        className="text-[11px] px-2 py-1 rounded border border-purple-400/40 text-purple-300
                                   disabled:opacity-40 hover:bg-purple-500/10 transition-colors"
                      >{t.fallacyAddSave}</button>
                      <button
                        type="button"
                        onClick={() => { setAddingFallacy(false); setNewFallacyType(''); setNewFallacyQuote('') }}
                        className="text-[11px] px-2 py-1 rounded border border-white/15 text-white/50
                                   hover:text-white/80 transition-colors"
                      >{t.rerunCancelBtn}</button>
                    </div>
                    <p className="text-[10px] text-white/30 mt-1.5">{t.fallacyAddHint}</p>
                  </div>
                )}
              </div>
            )}
          </>
        )
      })()}

      {/* Preverjene trditve, ki jih ni bilo mogoče pripeti na premiso.
          Tiste s premiso so vidne kot pika ob premisi sami. */}
      {(() => {
        const orphans = relatedClaims.filter(c => !Number.isInteger(premiseIndexOf(c)))
        if (orphans.length === 0) return null
        return (
          <div className="mb-2">
            <h5 className="text-accent-green text-xs font-semibold uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <TriangleIcon />
              {t.argFactCheck}
            </h5>
            {orphans.map((claim, claimIndex) => (
              <FactClaimMini key={claimIndex} claim={claim} />
            ))}
          </div>
        )
      })()}
    </div>
  )
}

function ArgLabel({ argument, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-left group/label cursor-pointer"
    >
      <p className="text-white/80 text-sm group-hover/label:text-white transition-colors leading-snug">
        {argument.argument}
      </p>
    </button>
  )
}

function FactClaimMini({ claim }) {
  const verdict = claim.verdict || 'UNVERIFIABLE'
  const verdictColors = {
    TRUE: 'text-green-400 bg-green-500/20',
    PARTIALLY_TRUE: 'text-yellow-400 bg-yellow-500/20',
    MISLEADING: 'text-orange-400 bg-orange-500/20',
    FALSE: 'text-red-400 bg-red-500/20',
    UNVERIFIABLE: 'text-white/40 bg-white/10',
  }

  const sources = claim.sources || claim.evidence?.sources || []

  return (
    <div className="mb-2 last:mb-0 p-2 bg-dark-800/50 rounded-lg">
      <div className="flex items-start justify-between gap-2">
        <p className="text-white/60 text-xs flex-1">
          {claim.exact_claim || claim.claim || ''}
        </p>
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold flex-shrink-0 ${
          verdictColors[verdict] || verdictColors.UNVERIFIABLE
        }`}>
          {verdict}
        </span>
      </div>
      {claim.explanation && (
        <p className="text-white/40 text-[11px] mt-1">{claim.explanation}</p>
      )}
      {sources.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {sources.slice(0, 3).map((source, sourceIndex) => (
            <a
              key={sourceIndex}
              href={source.url || source}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] text-accent-blue hover:text-blue-300 underline underline-offset-2"
            >
              {source.title || extractDomain(source.url || source)}
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

function TriangleIcon() {
  return (
    <svg className="w-3 h-3" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 1.5l6.5 13H1.5L8 1.5z" />
    </svg>
  )
}

function extractDomain(url) {
  try {
    return new URL(url).hostname.replace('www.', '')
  } catch {
    return url?.slice(0, 30) || ''
  }
}
