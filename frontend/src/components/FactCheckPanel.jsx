import { useState } from 'react'
import { useLanguage } from '../utils/LanguageContext'
import { getFactCheckClaims } from '../utils/factCheck'
import InfoTooltip from './InfoTooltip'

/**
 * Zberi vire v en seznam, brez podvojenih povezav.
 * Novejše analize imajo vse vire že v claim.sources; perplexity_data se prebere
 * zato, da starejše shranjene analize prikažejo enak seznam kot prej.
 */
function getAllSources(claim) {
  const seen = new Set()
  const result = []

  // Glavni seznam virov
  for (const src of claim.sources || []) {
    const url = src.url || src
    if (url && !seen.has(url)) {
      seen.add(url)
      result.push(typeof src === 'string' ? { url: src } : src)
    }
  }

  // Perplexityjevi navedki iz starejših analiz (gole povezave)
  const citations = (claim.perplexity_data || {}).citations || []
  for (const url of citations) {
    if (url && !seen.has(url)) {
      seen.add(url)
      result.push({ url, source_type: 'perplexity' })
    }
  }

  return result
}


// Vsak vir dobi od razsojevalnega koraka eno od istih petih razsodb: kar ta
// vir sam po sebi pove o trditvi. Vir brez oznake je tisti, ki ga razsodnik ni
// omenil, in ostane siv, namesto da bi ga šteli za pritrdilnega.
const SOURCE_DOT = {
  TRUE: 'bg-green-400',
  PARTIALLY_TRUE: 'bg-yellow-400',
  MISLEADING: 'bg-orange-400',
  FALSE: 'bg-red-400',
  UNVERIFIABLE: 'bg-white/30',
}

const SOURCE_TEXT = {
  TRUE: 'text-green-400',
  PARTIALLY_TRUE: 'text-yellow-400',
  MISLEADING: 'text-orange-400',
  FALSE: 'text-red-400',
  UNVERIFIABLE: 'text-white/40',
}

const VERDICT_ORDER = ['TRUE', 'PARTIALLY_TRUE', 'MISLEADING', 'FALSE', 'UNVERIFIABLE']

/** Koliko virov pove kaj. Seštevek, ne glasovanje: razsodba se od večine lahko razlikuje. */
function SourceTally({ tally, t }) {
  if (!tally) return null
  const shown = VERDICT_ORDER.filter(v => (tally[v] || 0) > 0)
  if (shown.length === 0) return null
  return (
    <div className="mb-3">
      <p className="text-xs text-white/40 font-semibold mb-1">{t.sourceVerdicts}</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {shown.map(v => (
          <span key={v} className={SOURCE_TEXT[v]}>
            {t[v] || v}: <span className="font-semibold">{tally[v]}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

export default function FactCheckPanel({ factCheck }) {
  const [expandedIdx, setExpandedIdx] = useState(null)
  const { t, tv } = useLanguage()

  if (!factCheck) return null

  const claims = getFactCheckClaims(factCheck)
  const summary = factCheck.summary || {}
  const verdictBreakdown = summary.verdict_breakdown || {}
  // Razsodbe po govorcih. Prej je tu stal delež točnosti, torej ena številka
  // iz utežene vsote razsodb. Uteži so bile izbrane in ne izmerjene, iz
  // imenovalca pa so izpadle nepreverljive trditve, zato je odstotek lahko
  // stal na peščici trditev in bil videti enako kot tisti, ki stoji na vseh.
  // Zdaj so prikazane same razsodbe, preštete.
  const VERDICTS = ['TRUE', 'PARTIALLY_TRUE', 'MISLEADING', 'FALSE', 'UNVERIFIABLE']
  const bySpeaker = Object.entries(summary.verdicts_by_speaker || {})

  return (
    <div className="space-y-6 animate-fade-in">
      {/* ── Summary bar ──────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-2 sm:gap-3">
        <StatCard label={t.checked} value={summary.total_checked || 0} />
      </div>

      {/* ── Vseh pet razsodb ─────────────────────────────────
          Prej sta bili prikazani samo skrajni vrednosti, zaradi česar je
          pri tridesetih preverjenih trditvah ostalo triindvajset nevidnih.
          Vmesne razsodbe so pri govorjenih trditvah najpogostejše, zato
          morajo biti v pregledu vidne. */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 sm:gap-3">
        <StatCard label={t.true_}            value={verdictBreakdown.TRUE || 0}           color="text-green-400" />
        <StatCard label={t.partiallyTrue}    value={verdictBreakdown.PARTIALLY_TRUE || 0} color="text-yellow-400" />
        <StatCard label={t.misleading}       value={verdictBreakdown.MISLEADING || 0}     color="text-orange-400" />
        <StatCard label={t.false_}           value={verdictBreakdown.FALSE || 0}          color="text-red-400" />
        <StatCard label={t.unverifiableShort} value={verdictBreakdown.UNVERIFIABLE || 0}  color="text-white/50" />
      </div>

      {/* ── Razsodbe po govorcih ── */}
      {bySpeaker.length > 1 && (
        <div className="rounded-lg border border-white/10 bg-dark-600/30 p-3">
          <p className="text-xs text-white/40 mb-2">{t.verdictsBySpeaker}</p>
          <div className="space-y-2">
            {bySpeaker.map(([speaker, counts]) => (
              <div key={speaker} className="flex items-baseline gap-3 flex-wrap">
                <span className="text-sm text-white/70 w-32 shrink-0 truncate" title={speaker}>{speaker}</span>
                {VERDICTS.filter(v => (counts?.[v] || 0) > 0).map(v => (
                  <span key={v} className="text-xs text-white/50">
                    {t[v] || v}: <span className="font-semibold text-white/80">{counts[v]}</span>
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Verdict distribution bar ─────────────────────── */}
      {Object.keys(verdictBreakdown).length > 0 && (() => {
        // Vrstni red je vsebinski (od drži do ne drži, nepreverljivo na koncu)
        // in ne vrstni red ključev, ki ga vrne strežnik.
        const VRSTNI_RED = ['TRUE', 'PARTIALLY_TRUE', 'MISLEADING', 'FALSE', 'UNVERIFIABLE']
        const BARVA = {
          TRUE: 'bg-green-500',
          PARTIALLY_TRUE: 'bg-yellow-500',
          MISLEADING: 'bg-orange-500',
          FALSE: 'bg-red-500',
          UNVERIFIABLE: 'bg-gray-500',
        }
        const total = Object.values(verdictBreakdown).reduce((a, b) => a + b, 0)
        const prisotne = VRSTNI_RED.filter((v) => (verdictBreakdown[v] || 0) > 0)

        return (
          <div className="space-y-2">
            <div className="flex h-3 rounded-full overflow-hidden bg-dark-600">
              {prisotne.map((verdict) => {
                const count = verdictBreakdown[verdict]
                const pct = total > 0 ? (count / total) * 100 : 0
                return (
                  <div
                    key={verdict}
                    className={`${BARVA[verdict]} transition-all`}
                    style={{ width: `${pct}%` }}
                    title={`${t[verdict] || verdict}: ${count}`}
                  />
                )
              })}
            </div>

            {/* Legenda: brez nje barv v pasu ni mogoče brati brez miške,
                na dotičnih napravah pa sploh ne. */}
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {prisotne.map((verdict) => (
                <span key={verdict} className="flex items-center gap-1.5 text-xs text-white/60">
                  <span className={`h-2.5 w-2.5 rounded-full ${BARVA[verdict]}`} />
                  {t[verdict] || verdict}
                  <span className="text-white/40">
                    {verdictBreakdown[verdict]} · {Math.round((verdictBreakdown[verdict] / total) * 100)} %
                  </span>
                </span>
              ))}
            </div>
          </div>
        )
      })()}

      {/* ── Claims list ──────────────────────────────────── */}
      <div className="space-y-2">
        {claims.map((claim, i) => {
          const isExpanded = expandedIdx === i
          const verdict = claim.verdict || claim.verdict_label || 'UNVERIFIABLE'
          const allSources = getAllSources(claim)

          return (
            <div
              key={i}
              className={`border rounded-xl transition-all overflow-hidden ${
                isExpanded ? 'border-white/15 bg-dark-600/80' : 'border-white/5 bg-dark-600/30'
              }`}
            >
              {/* Claim header */}
              <button
                onClick={() => setExpandedIdx(isExpanded ? null : i)}
                className="w-full px-3 sm:px-5 py-3 flex items-center gap-2 sm:gap-3 text-left
                           hover:bg-white/5 transition-colors"
              >
                <VerdictDot verdict={verdict} />
                <div className="flex-1 min-w-0">
                  <p className="text-white/80 text-sm truncate">
                    {claim.exact_claim || claim.claim}
                  </p>
                  <p className="text-white/30 text-xs mt-0.5">{claim.speaker}</p>
                </div>
                <VerdictBadge verdict={verdict} t={t} />
              </button>

              {/* Expanded details */}
              {isExpanded && (
                <div className="px-3 sm:px-5 pb-4 pt-1 border-t border-white/5 animate-fade-in">
                  {/* Explanation */}
                  {claim.explanation && (
                    <div className="mb-3">
                      <p className="text-xs text-white/40 font-semibold mb-1">{t.explanation}</p>
                      <p className="text-white/70 text-sm">{claim.explanation}</p>
                    </div>
                  )}

                  {/* Context */}
                  {claim.context && (
                    <div className="mb-3">
                      <p className="text-xs text-white/40 font-semibold mb-1">{t.context}</p>
                      <p className="text-white/50 text-sm italic">{claim.context}</p>
                    </div>
                  )}

                  {/* Kaj pove posamezen vir, po istih petih razsodbah */}
                  <SourceTally tally={claim.source_verdicts} t={t} />

                  {/* Sources (merged from sources + perplexity citations) */}
                  {allSources.length > 0 && (
                    <div className="mb-3">
                      <p className="text-xs text-white/40 font-semibold mb-1">{t.sources} ({allSources.length})</p>
                      <div className="space-y-1.5">
                        {allSources.map((src, j) => {
                          const url = src.url || src
                          const displayTitle = src.title || _domainFromUrl(url)
                          return (
                            <a
                              key={j}
                              href={url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex items-center gap-2 text-sm text-accent-blue
                                         hover:text-blue-300 hover:underline"
                            >
                              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                                SOURCE_DOT[src.source_verdict] || 'bg-white/20'
                              }`} />
                              <span className="truncate">{displayTitle}</span>
                              {src.source_verdict && (
                                <span className={`text-[10px] flex-shrink-0 ${
                                  SOURCE_TEXT[src.source_verdict] || 'text-white/40'
                                }`}>
                                  {t[src.source_verdict] || src.source_verdict}
                                </span>
                              )}
                            </a>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {/* Evidence metrics */}
                  {claim.evidence_metrics && (
                    <div className="flex flex-wrap gap-2 sm:gap-4 text-xs text-white/40">
                      {claim.evidence_metrics.source_count != null && (
                        <span>{t.sourceCount}: {claim.evidence_metrics.source_count}</span>
                      )}
                      {claim.evidence_metrics.independent_domain_count != null && (
                        <span>{t.independentDomains}: {claim.evidence_metrics.independent_domain_count}</span>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** Extract domain from URL for display fallback */
function _domainFromUrl(url) {
  try {
    return new URL(url).hostname.replace('www.', '')
  } catch {
    return url
  }
}

function StatCard({ label, value, color = 'text-white', tip }) {
  return (
    <div className="bg-dark-600/50 border border-white/5 rounded-xl px-4 py-3 text-center">
      <div className={`text-xl font-bold ${color}`}>{value}</div>
      <div className="text-[10px] text-white/30 mt-0.5 uppercase tracking-wider inline-flex items-center gap-2">
        {tip && <InfoTooltip text={tip} />}
        <span>{label}</span>
      </div>
    </div>
  )
}

function VerdictDot({ verdict }) {
  const color = {
    TRUE: 'bg-green-500',
    PARTIALLY_TRUE: 'bg-yellow-500',
    MISLEADING: 'bg-orange-500',
    FALSE: 'bg-red-500',
    UNVERIFIABLE: 'bg-gray-500',
  }[verdict] || 'bg-gray-500'

  return <div className={`w-2.5 h-2.5 rounded-full ${color} flex-shrink-0`} />
}

function VerdictBadge({ verdict, t }) {
  const colors = {
    TRUE: 'bg-green-500/20 text-green-400',
    PARTIALLY_TRUE: 'bg-yellow-500/20 text-yellow-400',
    MISLEADING: 'bg-orange-500/20 text-orange-400',
    FALSE: 'bg-red-500/20 text-red-400',
    UNVERIFIABLE: 'bg-white/10 text-white/40',
  }

  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${
      colors[verdict] || colors.UNVERIFIABLE
    }`}>
      {t[verdict] || verdict}
    </span>
  )
}

