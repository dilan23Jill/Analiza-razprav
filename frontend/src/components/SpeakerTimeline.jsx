import ArgumentNode from './ArgumentNode'
import { useLanguage } from '../utils/LanguageContext'

export default function SpeakerTimeline({
  speakerName,
  speakerProfile,
  isDebateMode,
  soloEvaluation = {},
  openArgumentId,
  onToggleArgument,
}) {
  const { t, tv } = useLanguage()
  const speakerData = speakerProfile?.speakerData || {}
  const args = speakerData.arguments || []
  const position = speakerData.position || ''
  const keyQuotes = speakerData.key_quotes || []
  const conclusions = speakerData.conclusions || []
  const fallacies = speakerProfile?.fallacies || []
  const evasions = speakerProfile?.evasions || []
  const rebuttals = speakerProfile?.rebuttals || []
  const factCheckClaims = speakerProfile?.factCheckClaims || []
  const argCritiques = speakerData.argument_critiques || []

  // Solo: instead of a separate "unsupported claims" box, attach each unsupported
  // claim to the argument it best matches (it then shows inside that argument).
  const unsupportedClaims = (!isDebateMode && Array.isArray(soloEvaluation.unsupported_claims))
    ? soloEvaluation.unsupported_claims : []
  const _tok = (s) => new Set(String(s || '').toLowerCase().split(/\s+/).filter(w => w.length > 4))
  const _argTok = args.map(a => _tok(a.argument))
  const unsupportedByArg = args.map(() => [])
  unsupportedClaims.forEach((claim) => {
    const ct = _tok(claim)
    let best = -1
    let bestScore = 1 // require >= 2 shared words to attach
    _argTok.forEach((at, idx) => {
      let overlap = 0
      at.forEach(w => { if (ct.has(w)) overlap++ })
      if (overlap > bestScore) { bestScore = overlap; best = idx }
    })
    if (best >= 0) unsupportedByArg[best].push(claim)
  })

  return (
    <div className="relative border border-accent-blue/30 rounded-2xl p-3 sm:p-6 bg-dark-800/30 animate-fade-in">
      <div className="flex justify-center mb-6 sm:mb-8">
        <div className="bg-accent-pink/90 rounded-xl px-4 sm:px-10 py-4 sm:py-5 text-center max-w-2xl w-full sm:w-auto">
          <h2 className="text-lg sm:text-xl font-bold text-slate-900 tracking-wide">
            {speakerName.toUpperCase()}
          </h2>
          {position && (
            <p className="text-slate-900/70 text-sm mt-2 font-medium">
              {t.stPosition}: {position}
            </p>
          )}
          {conclusions.length > 0 && (
            <div className="mt-3 text-left">
              <p className="text-slate-900/60 text-xs font-semibold mb-1">{t.stConclusions}:</p>
              <ul className="text-slate-900/80 text-xs space-y-0.5">
                {conclusions.map((conclusion, conclusionIndex) => (
                  <li key={conclusionIndex} className="flex gap-1.5">
                    <span className="text-slate-900/40">-</span>
                    <span>{conclusion}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      <div className="relative flex flex-col items-stretch md:items-center">
        {/* Timeline center line — only on md+ */}
        <div className="hidden md:block absolute left-1/2 -translate-x-px top-0 bottom-0 w-0.5 bg-accent-red/60" />
        {/* Timeline left line — only on mobile */}
        <div className="md:hidden absolute left-5 top-0 bottom-0 w-0.5 bg-accent-red/60" />

        {args.map((arg, index) => {
          const argumentId = `${speakerName}:${index}`
          // Stable cross-pass link id (resolved server-side). Falls back to the
          // same speaker#index scheme the backend uses, for older analyses.
          const linkId = arg.arg_id || `${speakerName}#${index}`
          const relatedClaims = findRelatedClaims(arg, factCheckClaims)

          // Prefer the stable arg_id link; fall back to text matching only when
          // no id-linked items exist (older data or an unresolved link).
          const fallaciesById = fallacies.filter(f => f.target_arg_id && f.target_arg_id === linkId)
          // Text fallback ONLY for fallacies with no id link — never steal one
          // that is already linked to a different argument.
          const relatedFallacies = fallaciesById.length ? fallaciesById : fallacies.filter(fallacy =>
            !fallacy.target_arg_id && arg.argument && fallacy.evidence &&
            fallacy.evidence.toLowerCase().includes(arg.argument.toLowerCase().slice(0, 30))
          )

          const critiqueById = argCritiques.find(c => c.arg_id && c.arg_id === linkId)
          // Text fallback only among critiques that have no id link.
          const critique = critiqueById || findCritique(arg, argCritiques.filter(c => !c.arg_id))

          const rebuttalsById = rebuttals.filter(r => r.target_arg_id && r.target_arg_id === linkId)
          const targetingRebuttals = rebuttalsById.length ? rebuttalsById : rebuttals.filter(rebuttal =>
            !rebuttal.target_arg_id && arg.argument && rebuttal.target_claim &&
            (rebuttal.target_claim.toLowerCase().includes(arg.argument.toLowerCase().slice(0, 40)) ||
             arg.argument.toLowerCase().includes(rebuttal.target_claim.toLowerCase().slice(0, 40)))
          )

          const userAdded = !!arg.user_added
          return (
            <ArgumentNode
              key={argumentId}
              index={index}
              speakerName={speakerName}
              argument={arg}
              relatedClaims={userAdded ? [] : relatedClaims}
              relatedFallacies={userAdded ? [] : relatedFallacies}
              critique={userAdded ? null : critique}
              rebuttals={userAdded ? [] : targetingRebuttals}
              isDebateMode={isDebateMode}
              unsupportedClaims={userAdded ? [] : (unsupportedByArg[index] || [])}
              userAdded={userAdded}
              side={index % 2 === 0 ? 'right' : 'left'}
              isOpen={openArgumentId === argumentId}
              onToggle={() => onToggleArgument(argumentId)}
              onClose={() => onToggleArgument(null)}
            />
          )
        })}
      </div>

      {isDebateMode && evasions.length > 0 && (
        <div className="mt-8 p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-xl">
          <h4 className="text-yellow-400 font-semibold text-sm mb-2">
            {t.stEvasions} ({evasions.length})
          </h4>
          {evasions.map((evasion, evasionIndex) => (
            <div key={evasionIndex} className="text-sm text-white/60 mb-2">
              <span className="text-yellow-300 font-medium">{tv('evasion_type', evasion.evasion_type)}</span>
              : <span>{evasion.explanation}</span>
            </div>
          ))}
        </div>
      )}

    </div>
  )
}

function findRelatedClaims(argument, allClaims) {
  if (!allClaims.length) return []

  // Claims are now extracted from the arguments themselves, so each one names
  // the argument it belongs to. Use that: it is exact, and a claim that says
  // nothing about this argument is not shown under it by accident.
  if (argument.arg_id) {
    const byId = allClaims.filter(claim => claim.arg_id === argument.arg_id)
    if (byId.length) return byId
  }

  // Analyses stored before that change have no arg_id on their claims, so fall
  // back to the old word-overlap guess rather than showing them nothing.
  if (!argument.argument) return []
  const argWords = new Set(
    argument.argument.toLowerCase().split(/\s+/).filter(word => word.length > 4)
  )
  return allClaims.filter(claim => {
    if (claim.arg_id) return false      // belongs to some other argument
    const claimText = (claim.exact_claim || claim.claim || '').toLowerCase()
    const overlap = [...argWords].filter(word => claimText.includes(word))
    return overlap.length >= 3
  })
}

function findCritique(argument, critiques) {
  if (!argument.argument || !critiques.length) return null

  const argumentLower = argument.argument.toLowerCase()
  return critiques.find(critique => {
    const ref = (critique.argument_ref || '').toLowerCase()
    return ref && (
      argumentLower.includes(ref.slice(0, 40)) ||
      ref.includes(argumentLower.slice(0, 40))
    )
  }) || null
}

