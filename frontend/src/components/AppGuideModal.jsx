import { useEffect } from 'react'
import { useLanguage } from '../utils/LanguageContext'

/**
 * "How to read this analysis" — onboarding / help modal.
 *
 * In-app guide explaining what each part of the analysis means.
 * with content that matches what the app actually surfaces today: argument cards,
 * fact-check verdicts, balanced source perspectives, edit capability, and modes.
 */

export default function AppGuideModal({ open, onClose }) {
  const { t } = useLanguage()

  useEffect(() => {
    if (!open) return undefined
    function handleKeydown(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[75] flex items-center justify-center p-3 sm:p-6">
      <button
        type="button"
        aria-label={t.guideCloseLabel}
        onClick={onClose}
        className="absolute inset-0 bg-black/80 backdrop-blur-md"
      />

      <div className="relative w-full max-w-3xl max-h-[92vh] overflow-y-auto rounded-2xl border border-white/10 bg-dark-700 shadow-2xl shadow-black/60 animate-fade-in">
        {/* ── Sticky header ───────────────────────────────────────── */}
        <div className="sticky top-0 z-10 border-b border-white/10 bg-dark-700/95 px-5 sm:px-7 py-4 backdrop-blur-md">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.3em] text-accent-red">
                {t.guideHowItWorks}
              </p>
              <h2 className="mt-1.5 text-lg sm:text-xl font-semibold text-white">
                {t.guideTitle}
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white/70 transition-all hover:border-white/30 hover:bg-white/10 hover:text-white"
            >
              {t.guideClose}
            </button>
          </div>
        </div>

        <div className="px-5 sm:px-7 py-6 space-y-5 text-sm text-white/75">

          {/* ── 1. What the app does ────────────────────────────── */}
          <Step n={1} accent="red" title={t.guideWhatAppDoes}>
            <p className="leading-relaxed">{t.guideWhatAppDoesText}</p>
          </Step>

          {/* ── 2. How to read arguments ────────────────────────── */}
          <Step n={2} accent="blue" title={t.guideHowToReadNodes}>
            <ul className="space-y-2 leading-relaxed">
              <BulletItem>{t.guideNodeLine1}</BulletItem>
              <BulletItem>{t.guideNodeLine2}</BulletItem>
              <BulletItem>{t.guideNodeLine3}</BulletItem>
            </ul>
          </Step>

          {/* ── 3. Fact-check verdicts ──────────────────────────── */}
          <Step n={3} accent="emerald" title={t.guideVerdictsTitle}>
            <div className="space-y-2">
              <VerdictRow color="emerald" label={t.guideVerdictTrue}        desc={t.guideVerdictTrueDesc} />
              <VerdictRow color="lime"    label={t.guideVerdictPartial}     desc={t.guideVerdictPartialDesc} />
              <VerdictRow color="amber"   label={t.guideVerdictMisleading}  desc={t.guideVerdictMisleadingDesc} />
              <VerdictRow color="red"     label={t.guideVerdictFalse}       desc={t.guideVerdictFalseDesc} />
              <VerdictRow color="slate"   label={t.guideVerdictUnverifiable} desc={t.guideVerdictUnverifiableDesc} />
            </div>
          </Step>

          {/* ── 4. Source perspectives ──────────────────────────── */}
          <Step n={4} accent="violet" title={t.guideSourcesTitle}>
            <p className="leading-relaxed mb-3">{t.guideSourcesDesc}</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <PerspectiveTag color="violet" label={t.guideSourceAligned} />
              <PerspectiveTag color="sky"    label={t.guideSourceNeutral} />
              <PerspectiveTag color="orange" label={t.guideSourceOpposing} />
            </div>
          </Step>

          {/* ── 5. Modes ────────────────────────────────────────── */}
          <Step n={5} accent="cyan" title={t.guideModesTitle}>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <ModeCard color="cyan"   title={t.guideModeSoloTitle}   desc={t.guideModeSoloDesc} />
              <ModeCard color="purple" title={t.guideModeDebateTitle} desc={t.guideModeDebateDesc} />
            </div>
          </Step>

          {/* ── 6. Edit ─────────────────────────────────────────── */}
          <Step n={6} accent="rose" title={t.guideEditTitle}>
            <p className="leading-relaxed">{t.guideEditDesc}</p>
          </Step>

          {/* ── Footer disclaimer ───────────────────────────────── */}
          <div className="mt-2 rounded-2xl border border-amber-500/25 bg-amber-500/[0.06] p-4">
            <div className="flex items-start gap-3">
              <span className="shrink-0 text-base">⚠️</span>
              <div>
                <h3 className="text-sm font-semibold text-amber-200">{t.guideImportantNote}</h3>
                <p className="mt-1.5 text-xs leading-relaxed text-amber-100/80">
                  {t.guideImportantNoteText}
                </p>
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}

/* ── Components ─────────────────────────────────────────────── */

const ACCENTS = {
  red:     { ring: 'ring-accent-red/40',     bg: 'bg-accent-red/10',     text: 'text-accent-red'     },
  blue:    { ring: 'ring-blue-400/40',       bg: 'bg-blue-500/10',       text: 'text-blue-300'       },
  emerald: { ring: 'ring-emerald-400/40',    bg: 'bg-emerald-500/10',    text: 'text-emerald-300'    },
  violet:  { ring: 'ring-violet-400/40',     bg: 'bg-violet-500/10',     text: 'text-violet-300'     },
  cyan:    { ring: 'ring-cyan-400/40',       bg: 'bg-cyan-500/10',       text: 'text-cyan-300'       },
  rose:    { ring: 'ring-rose-400/40',       bg: 'bg-rose-500/10',       text: 'text-rose-300'       },
  amber:   { ring: 'ring-amber-400/40',      bg: 'bg-amber-500/10',      text: 'text-amber-300'      },
}

function Step({ n, accent = 'red', title, children }) {
  const a = ACCENTS[accent] || ACCENTS.red
  return (
    <section className="rounded-2xl border border-white/10 bg-dark-600/40 p-4 sm:p-5">
      <div className="flex items-center gap-3 mb-3">
        <span className={`flex shrink-0 w-7 h-7 items-center justify-center rounded-full ring-1 ${a.ring} ${a.bg} ${a.text} text-xs font-bold`}>
          {n}
        </span>
        <h3 className="text-sm sm:text-[15px] font-semibold text-white">{title}</h3>
      </div>
      <div className="pl-10 text-white/70">{children}</div>
    </section>
  )
}

function BulletItem({ children }) {
  return (
    <li className="flex items-start gap-2">
      <span className="mt-2 block w-1 h-1 rounded-full bg-white/40 shrink-0" />
      <span>{children}</span>
    </li>
  )
}

const VERDICT_COLORS = {
  emerald: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  lime:    'bg-lime-500/15    text-lime-300    border-lime-500/30',
  amber:   'bg-amber-500/15   text-amber-300   border-amber-500/30',
  red:     'bg-red-500/15     text-red-300     border-red-500/30',
  slate:   'bg-slate-500/15   text-slate-300   border-slate-500/30',
}

function VerdictRow({ color = 'slate', label, desc }) {
  return (
    <div className="flex items-start gap-3 rounded-lg bg-white/[0.02] border border-white/5 p-2.5">
      <span className={`shrink-0 px-2 py-0.5 rounded text-[10px] uppercase tracking-wider font-semibold border ${VERDICT_COLORS[color]}`}>
        {label}
      </span>
      <span className="text-xs text-white/65 leading-relaxed">{desc}</span>
    </div>
  )
}

const PERSP_COLORS = {
  violet: 'border-violet-400/40 text-violet-200 bg-violet-500/5',
  sky:    'border-sky-400/40    text-sky-200    bg-sky-500/5',
  orange: 'border-orange-400/40 text-orange-200 bg-orange-500/5',
}

function PerspectiveTag({ color = 'sky', label }) {
  return (
    <div className={`rounded-lg border px-3 py-2 text-xs font-medium text-center ${PERSP_COLORS[color]}`}>
      {label}
    </div>
  )
}

const MODE_COLORS = {
  cyan:   { border: 'border-cyan-400/30',   bg: 'bg-cyan-500/5',   title: 'text-cyan-300' },
  purple: { border: 'border-purple-400/30', bg: 'bg-purple-500/5', title: 'text-purple-300' },
}

function ModeCard({ color = 'cyan', title, desc }) {
  const c = MODE_COLORS[color] || MODE_COLORS.cyan
  return (
    <div className={`rounded-xl border ${c.border} ${c.bg} p-3.5`}>
      <div className={`text-xs font-bold uppercase tracking-wider ${c.title} mb-1.5`}>{title}</div>
      <div className="text-xs leading-relaxed text-white/70">{desc}</div>
    </div>
  )
}
