import { useEffect, useMemo, useState } from 'react'
import { editDebate } from '../services/api'

/**
 * Modal editor for a debate's analysis.
 *
 * Layout — grouped by SIDE:
 *   • Solo (1 speaker): one column, all arguments listed, no side concept
 *   • Debate (2 debaters): two side panels, one per debater, with a speaker
 *     badge per argument. The user can reassign an argument to the other
 *     debater via the badge dropdown.
 *
 * The app supports one-on-one debates only, so each debater is their own side;
 * there is no team concept. A moderator is never a debater and never appears
 * here — they are reported separately, without scoring.
 */

function btn(variant = 'subtle') {
  const base = 'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors'
  if (variant === 'primary') return `${base} bg-accent-red hover:bg-brand-600 text-pure-white`
  if (variant === 'danger')  return `${base} text-red-400 hover:bg-red-500/10 border border-red-500/30`
  return `${base} text-white/60 hover:text-white hover:bg-white/10 border border-white/10`
}

/** Compute "sides" from analysis. Returns an array of `{label, speakers, color}`. */
function computeSides(analysis, draftSpeakers) {
  const speakerNames = Object.keys(draftSpeakers || {})

  if (speakerNames.length === 0) return []
  if (speakerNames.length === 1) {
    return [{ label: speakerNames[0], speakers: speakerNames, color: 'cyan', single: true }]
  }

  // 1v1: each debater is their own side, labelled with their own name.
  // Older analyses may still carry more than two speakers — keep them visible
  // (split in half) rather than hiding data the user may need to edit.
  if (speakerNames.length === 2) {
    return [
      { label: speakerNames[0], speakers: [speakerNames[0]], color: 'blue' },
      { label: speakerNames[1], speakers: [speakerNames[1]], color: 'rose' },
    ]
  }

  const half = Math.ceil(speakerNames.length / 2)
  return [
    { label: 'Stran A', speakers: speakerNames.slice(0, half), color: 'blue' },
    { label: 'Stran B', speakers: speakerNames.slice(half),    color: 'rose' },
  ]
}

const SIDE_COLORS = {
  blue:    { border: 'border-blue-400/30',    bg: 'bg-blue-500/5',    badge: 'bg-blue-500/15 text-blue-300 border-blue-500/30' },
  rose:    { border: 'border-rose-400/30',    bg: 'bg-rose-500/5',    badge: 'bg-rose-500/15 text-rose-300 border-rose-500/30' },
  emerald: { border: 'border-emerald-400/30', bg: 'bg-emerald-500/5', badge: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' },
  amber:   { border: 'border-amber-400/30',   bg: 'bg-amber-500/5',   badge: 'bg-amber-500/15 text-amber-300 border-amber-500/30' },
  cyan:    { border: 'border-cyan-400/30',    bg: 'bg-cyan-500/5',    badge: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30' },
}

/* Speaker name field — keeps its own local value and commits the rename only on
   blur / Enter. Renaming on every keystroke used to rewrite the speakers object
   key, which (because the list is keyed by name) remounted the input and lost
   focus after each character. */
function SpeakerNameInput({ name, onRename, t }) {
  const [val, setVal] = useState(name)
  useEffect(() => { setVal(name) }, [name])
  function commit() {
    const v = (val || '').trim()
    if (v && v !== name) onRename(name, v)
    else if (!v) setVal(name)   // don't allow empty — revert
  }
  return (
    <input
      type="text"
      value={val}
      onChange={(e) => setVal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
      className="flex-1 bg-dark-900/60 border border-white/10 rounded-lg px-3 py-2 text-base font-semibold text-white focus:border-amber-400/40 focus:outline-none"
      placeholder={t('Ime govorca', 'Speaker name')}
    />
  )
}

export default function DebateEditor({ debateId, analysis, debateTitle = '', onClose, onSaved, lang = 'sl' }) {
  const t = (sl, en) => (lang === 'sl' ? sl : en)

  // Deep-clone the analysis so edits don't mutate the page's state until saved.
  const [draft, setDraft] = useState(() => JSON.parse(JSON.stringify(analysis || {})))
  const [titleDraft, setTitleDraft] = useState(debateTitle || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Track speaker rename pairs so we can submit them as ops at save time.
  const [renames, setRenames] = useState({})
  // Track speaker reassignment so we emit move_argument ops correctly
  // Map: "currentSpeaker:currentIndex" -> originalSpeaker (only for moved args)
  const [moves, setMoves] = useState([])  // array of {from, to, originalIndex}
  // Local rebuttal state — modifications here become add/edit/delete_rebuttal ops at save
  const [rebuttals, setRebuttals] = useState(() => Array.isArray(draft.rebuttals) ? [...draft.rebuttals] : [])

  const speakers = draft.speakers || {}
  const sides = useMemo(() => computeSides(analysis, speakers), [analysis, speakers])
  const isSolo = sides.length === 1 && sides[0].single

  // For mobile: which side tab is active
  const [activeSideIdx, setActiveSideIdx] = useState(0)

  // Esc key closes the modal
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // --- Mutators --------------------------------------------------------

  function renameSpeaker(oldName, newName) {
    newName = (newName || '').trim()
    if (!newName || newName === oldName || speakers[newName]) return
    const nextSpeakers = {}
    for (const k of Object.keys(speakers)) {
      nextSpeakers[k === oldName ? newName : k] = speakers[k]
    }
    setDraft({ ...draft, speakers: nextSpeakers })
    setRenames(prev => {
      const original = Object.keys(prev).find(k => prev[k] === oldName) || oldName
      return { ...prev, [original]: newName }
    })
  }

  function updateSpeakerMeta(speaker, field, value) {
    const next = { ...draft }
    next.speakers = { ...next.speakers, [speaker]: { ...(next.speakers[speaker] || {}), [field]: value } }
    setDraft(next)
  }

  function updateArgument(speaker, idx, field, value) {
    const next = { ...draft }
    const sp = { ...(next.speakers[speaker] || {}) }
    const args = [...(sp.arguments || [])]
    args[idx] = { ...args[idx], [field]: value }
    sp.arguments = args
    next.speakers = { ...next.speakers, [speaker]: sp }
    setDraft(next)
  }

  function deleteArgument(speaker, idx) {
    if (!confirm(t('Izbriši argument?', 'Delete argument?'))) return
    const next = { ...draft }
    const sp = { ...(next.speakers[speaker] || {}) }
    sp.arguments = (sp.arguments || []).filter((_, i) => i !== idx)
    next.speakers = { ...next.speakers, [speaker]: sp }
    setDraft(next)
  }

  function addArgumentToSide(side) {
    // Pick the speaker on the side with the FEWEST args (so additions spread evenly)
    const counts = side.speakers.map(sp => ({ sp, n: (speakers[sp]?.arguments || []).length }))
    counts.sort((a, b) => a.n - b.n)
    const targetSpeaker = counts[0]?.sp
    if (!targetSpeaker) return

    const next = { ...draft }
    const sp = { ...(next.speakers[targetSpeaker] || {}) }
    sp.arguments = [...(sp.arguments || []), {
      argument: t('Nov argument...', 'New argument...'),
      type: 'factual',
      premises: [],
      user_added: true,
    }]
    next.speakers = { ...next.speakers, [targetSpeaker]: sp }
    setDraft(next)
  }

  function moveArgumentToSpeaker(fromSpeaker, idx, toSpeaker) {
    if (toSpeaker === fromSpeaker) return
    const next = { ...draft }
    const fromSp = { ...(next.speakers[fromSpeaker] || {}) }
    const toSp = { ...(next.speakers[toSpeaker] || {}) }
    const fromArgs = [...(fromSp.arguments || [])]
    const arg = fromArgs[idx]
    if (!arg) return
    fromArgs.splice(idx, 1)
    fromSp.arguments = fromArgs
    toSp.arguments = [...(toSp.arguments || []), arg]
    next.speakers = { ...next.speakers, [fromSpeaker]: fromSp, [toSpeaker]: toSp }
    setDraft(next)
    setMoves(prev => [...prev, { from: fromSpeaker, to: toSpeaker, originalIndex: idx }])
  }

  function updatePremise(speaker, argIdx, premIdx, value) {
    const next = { ...draft }
    const sp = { ...(next.speakers[speaker] || {}) }
    const args = [...(sp.arguments || [])]
    const a = { ...args[argIdx] }
    const prem = [...(a.premises || [])]
    prem[premIdx] = value
    a.premises = prem
    args[argIdx] = a
    sp.arguments = args
    next.speakers = { ...next.speakers, [speaker]: sp }
    setDraft(next)
  }

  function addPremise(speaker, argIdx) {
    const next = { ...draft }
    const sp = { ...(next.speakers[speaker] || {}) }
    const args = [...(sp.arguments || [])]
    args[argIdx] = { ...args[argIdx], premises: [...(args[argIdx].premises || []), ''] }
    sp.arguments = args
    next.speakers = { ...next.speakers, [speaker]: sp }
    setDraft(next)
  }

  function removePremise(speaker, argIdx, premIdx) {
    const next = { ...draft }
    const sp = { ...(next.speakers[speaker] || {}) }
    const args = [...(sp.arguments || [])]
    args[argIdx] = {
      ...args[argIdx],
      premises: (args[argIdx].premises || []).filter((_, i) => i !== premIdx),
    }
    sp.arguments = args
    next.speakers = { ...next.speakers, [speaker]: sp }
    setDraft(next)
  }

  // ── Rebuttal mutators ────────────────────────────────────────────────

  function addRebuttal({ targetSpeaker, targetClaim, by }) {
    // Pick "by" speaker — the one most likely to rebut: any other speaker.
    // If the user picked a side-mate, allow it (sometimes self-critique happens).
    const fallbackBy = by || Object.keys(speakers).find(s => s !== targetSpeaker) || targetSpeaker
    setRebuttals(prev => [...prev, {
      by: fallbackBy,
      to: targetSpeaker,
      target_claim: targetClaim || '',
      rebuttal_content: '',
      rebuttal_type: 'direct_contradiction',
      response: '',
      user_added: true,
      _new: true,    // local marker for diff tracking
    }])
  }

  function updateRebuttal(idx, field, value) {
    setRebuttals(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r))
  }

  function deleteRebuttal(idx) {
    if (!confirm(t('Izbriši rebuttal?', 'Delete rebuttal?'))) return
    setRebuttals(prev => prev.filter((_, i) => i !== idx))
  }

  // --- Compute operations diff ----------------------------------------

  const operations = useMemo(() => {
    const ops = []

    // 1. Renames first
    for (const [from_name, to_name] of Object.entries(renames)) {
      if (from_name !== to_name) ops.push({ op: 'rename_speaker', payload: { from_name, to_name } })
    }

    // 2. For each speaker — diff meta + arguments against original
    const origSpeakers = analysis?.speakers || {}
    for (const sp of Object.keys(speakers)) {
      const original = Object.keys(renames).find(k => renames[k] === sp) || sp
      const orig = origSpeakers[original] || {}
      const cur  = speakers[sp] || {}

      // Speaker meta diff
      const metaChanges = {}
      for (const k of ['position', 'conclusions']) {
        if (JSON.stringify(orig[k]) !== JSON.stringify(cur[k])) metaChanges[k] = cur[k]
      }
      if (Object.keys(metaChanges).length) {
        ops.push({ op: 'edit_speaker_meta', payload: { speaker: sp, fields: metaChanges } })
      }

      // Arguments diff (simple length + per-index compare)
      const origArgs = orig.arguments || []
      const curArgs  = cur.arguments  || []
      const common = Math.min(origArgs.length, curArgs.length)
      for (let i = 0; i < common; i++) {
        const fields = {}
        for (const k of ['argument', 'type', 'premises']) {
          if (JSON.stringify(origArgs[i]?.[k]) !== JSON.stringify(curArgs[i]?.[k])) fields[k] = curArgs[i][k]
        }
        if (Object.keys(fields).length) {
          ops.push({ op: 'edit_argument', payload: { speaker: sp, index: i, fields } })
        }
      }
      for (let i = origArgs.length - 1; i >= curArgs.length; i--) {
        ops.push({ op: 'delete_argument', payload: { speaker: sp, index: i } })
      }
      for (let i = origArgs.length; i < curArgs.length; i++) {
        ops.push({ op: 'add_argument', payload: { speaker: sp, argument: curArgs[i] } })
      }
    }

    // 3. Title (top-level DB column, separate from analysis JSON)
    if ((debateTitle || '').trim() !== (titleDraft || '').trim()) {
      ops.push({ op: 'edit_title', payload: { title: titleDraft || '' } })
    }
    if ((analysis?.summary || '') !== (draft.summary || '')) {
      ops.push({ op: 'edit_summary', payload: { summary: draft.summary || '' } })
    }
    const origMeta = analysis?.metadata || {}
    const curMeta  = draft.metadata  || {}
    const metaFields = {}
    for (const k of ['topic', 'format']) {
      if ((origMeta[k] || '') !== (curMeta[k] || '')) metaFields[k] = curMeta[k] || ''
    }
    if (Object.keys(metaFields).length) ops.push({ op: 'edit_metadata', payload: { fields: metaFields } })

    // 4. Rebuttals diff — same strategy as arguments (edit common, delete extra orig, add extra new)
    const origRebuts = (Array.isArray(analysis?.rebuttals) ? analysis.rebuttals : [])
    const curRebuts  = rebuttals
    const REB_FIELDS = ['by', 'to', 'target_claim', 'rebuttal_content', 'rebuttal_type', 'response']
    const commonRb = Math.min(origRebuts.length, curRebuts.length)
    for (let i = 0; i < commonRb; i++) {
      const fields = {}
      for (const k of REB_FIELDS) {
        if ((origRebuts[i]?.[k] || '') !== (curRebuts[i]?.[k] || '')) fields[k] = curRebuts[i][k]
      }
      if (Object.keys(fields).length) {
        ops.push({ op: 'edit_rebuttal', payload: { index: i, fields } })
      }
    }
    for (let i = origRebuts.length - 1; i >= curRebuts.length; i--) {
      ops.push({ op: 'delete_rebuttal', payload: { index: i } })
    }
    for (let i = origRebuts.length; i < curRebuts.length; i++) {
      const r = curRebuts[i]
      // Only emit add if it has actual content
      if ((r.rebuttal_content || '').trim() && (r.by || '').trim()) {
        ops.push({ op: 'add_rebuttal', payload: { rebuttal: {
          by: r.by, to: r.to, target_claim: r.target_claim,
          rebuttal_content: r.rebuttal_content,
          rebuttal_type: r.rebuttal_type,
          response: r.response,
        }}})
      }
    }

    return ops
  }, [draft, renames, speakers, analysis, rebuttals, titleDraft, debateTitle])

  async function save() {
    if (!operations.length) {
      onClose()
      return
    }
    setSaving(true)
    setError('')
    try {
      await editDebate(debateId, operations)
      if (onSaved) await onSaved()
      onClose()
    } catch (e) {
      setError(e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-stretch sm:items-center justify-center bg-black/75 backdrop-blur-md p-0 sm:p-6">
      <div className="bg-dark-800 border border-white/10 rounded-none sm:rounded-2xl w-full sm:max-w-6xl max-h-screen sm:max-h-[94vh] flex flex-col overflow-hidden shadow-2xl shadow-black/60">

        {/* Header — minimal, X-only close */}
        <div className="flex items-center justify-between gap-3 px-5 sm:px-7 py-4 border-b border-white/10 bg-dark-700/50 shrink-0">
          <div className="min-w-0">
            <h2 className="text-white text-base sm:text-lg font-semibold truncate">
              {t('Uredi analizo', 'Edit analysis')}
            </h2>
            <span className="text-[11px] text-white/40">
              {operations.length > 0
                ? t(`${operations.length} sprememb pripravljenih`, `${operations.length} pending changes`)
                : t('Brez sprememb', 'No changes')}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={save}
              disabled={saving || operations.length === 0}
              className={btn('primary') + ' disabled:opacity-40 disabled:cursor-not-allowed'}
            >
              {saving ? t('Shranjujem...', 'Saving...') : t('Shrani', 'Save')}
            </button>
            <button
              onClick={onClose}
              aria-label={t('Zapri', 'Close')}
              title={t('Zapri (Esc)', 'Close (Esc)')}
              className="flex items-center justify-center w-9 h-9 rounded-full text-white/50 hover:text-white hover:bg-white/10 transition-colors"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {error && (
          <div className="bg-red-500/10 border-b border-red-500/30 text-red-300 text-sm px-4 sm:px-6 py-2">
            {error}
          </div>
        )}

        {/* ── Mobile: side switcher tabs (only when 2+ sides) ── */}
        {!isSolo && sides.length >= 2 && (
          <div className="sm:hidden flex border-b border-white/10 shrink-0">
            {sides.map((s, i) => {
              const c = SIDE_COLORS[s.color] || SIDE_COLORS.blue
              return (
                <button
                  key={i}
                  onClick={() => setActiveSideIdx(i)}
                  className={`flex-1 px-3 py-2.5 text-xs font-medium transition-colors ${
                    activeSideIdx === i
                      ? `${c.badge} border-b-2`
                      : 'text-white/50 hover:text-white/70'
                  }`}
                  style={activeSideIdx === i ? { borderBottomColor: 'currentColor' } : {}}
                >
                  {s.label}
                </button>
              )
            })}
          </div>
        )}

        {/* ── Body ───────────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto">
          {sides.length === 0 ? (
            <div className="text-white/40 text-sm p-8 text-center">
              {t('Ni govorcev v analizi.', 'No speakers in analysis.')}
            </div>
          ) : (
            <div className={
              isSolo
                ? ''
                : (sides.length >= 2
                    ? 'sm:grid sm:grid-cols-2 sm:gap-6 sm:p-4'
                    : '')
            }>
              {sides.map((side, i) => (
                <div
                  key={i}
                  className={`${
                    !isSolo && sides.length >= 2 ? `sm:block ${activeSideIdx === i ? 'block' : 'hidden'}` : 'block'
                  }`}
                >
                  <SidePanel
                    side={side}
                    speakers={speakers}
                    rebuttals={rebuttals}
                    onRenameSpeaker={renameSpeaker}
                    onUpdateSpeakerMeta={updateSpeakerMeta}
                    onUpdateArgument={updateArgument}
                    onDeleteArgument={deleteArgument}
                    onAddArgumentToSide={() => addArgumentToSide(side)}
                    onMoveArgument={moveArgumentToSpeaker}
                    onUpdatePremise={updatePremise}
                    onAddPremise={addPremise}
                    onRemovePremise={removePremise}
                    onAddRebuttal={addRebuttal}
                    onUpdateRebuttal={updateRebuttal}
                    onDeleteRebuttal={deleteRebuttal}
                    isSolo={isSolo}
                    t={t}
                  />
                </div>
              ))}
            </div>
          )}

          {/* ── Title + Summary + Topic — full-width at bottom, distinct emerald-tinted band ─ */}
          <div className="px-5 sm:px-7 py-5 border-t border-white/10 space-y-4 bg-emerald-500/[0.02]">
            <div>
              <label className="block text-[11px] uppercase tracking-wider text-emerald-300/70 font-semibold mb-2">
                {t('Naslov analize', 'Analysis title')}
              </label>
              <input
                type="text"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                placeholder={t('npr. Janez vs. Ana — Pogovor o gospodarstvu', 'e.g. Janez vs. Ana — Discussion on economy')}
                className="w-full bg-dark-900/60 border border-white/10 rounded-lg px-3 py-2.5 text-base text-white focus:border-emerald-400/40 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-[11px] uppercase tracking-wider text-emerald-300/70 font-semibold mb-2">
                {t('Tema debate', 'Debate topic')}
              </label>
              <input
                type="text"
                value={(draft.metadata && draft.metadata.topic) || ''}
                onChange={(e) => setDraft({ ...draft, metadata: { ...(draft.metadata || {}), topic: e.target.value } })}
                className="w-full bg-dark-900/60 border border-white/10 rounded-lg px-3 py-2.5 text-base text-white focus:border-emerald-400/40 focus:outline-none"
                placeholder={t('npr. Ali je sintetično meso etično sprejemljivo', 'e.g. Is synthetic meat ethically acceptable')}
              />
            </div>
            <div>
              <label className="block text-[11px] uppercase tracking-wider text-emerald-300/70 font-semibold mb-2">
                {t('Skupni povzetek analize', 'Overall summary')}
              </label>
              <textarea
                rows={6}
                value={draft.summary || ''}
                onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
                className="w-full bg-dark-900/60 border border-white/10 rounded-lg px-3 py-2.5 text-sm leading-relaxed text-white focus:border-emerald-400/40 focus:outline-none resize-y"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Single side panel (one column) ─────────────────────────────────── */

function SidePanel({
  side, speakers, rebuttals, onRenameSpeaker, onUpdateSpeakerMeta,
  onUpdateArgument, onDeleteArgument, onAddArgumentToSide, onMoveArgument,
  onUpdatePremise, onAddPremise, onRemovePremise,
  onAddRebuttal, onUpdateRebuttal, onDeleteRebuttal,
  isSolo, t,
}) {
  const c = SIDE_COLORS[side.color] || SIDE_COLORS.blue
  const allSpeakerNames = Object.keys(speakers)

  // Build flat list of (speaker, argIdx, arg) for all speakers on this side
  const items = []
  for (const sp of side.speakers) {
    const args = (speakers[sp]?.arguments || [])
    for (let i = 0; i < args.length; i++) {
      items.push({ speaker: sp, idx: i, arg: args[i] })
    }
  }

  return (
    <div className={`p-5 sm:p-6 ${!isSolo ? `${c.bg} sm:rounded-xl sm:border ${c.border}` : ''}`}>
      {/* Side header */}
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-3">
          {!isSolo && (
            <span className={`px-2.5 py-1 rounded-md text-[11px] uppercase tracking-wider font-bold border ${c.badge}`}>
              {side.label}
            </span>
          )}
          <span className="text-[11px] text-white/40">
            {items.length} {t('argumentov', 'arguments')}
          </span>
        </div>

        {/* Speakers on this side — rename + position editing
            (subtle warm-tinted card to differentiate from arguments) */}
        <div className="space-y-2.5">
          {side.speakers.map(sp => {
            const data = speakers[sp] || {}
            return (
              <div key={sp} className="rounded-xl border border-amber-500/15 bg-amber-500/[0.03] p-3.5 space-y-2.5">
                <div className="flex items-center gap-3">
                  <span className="text-[10px] uppercase tracking-wider text-amber-300/70 font-semibold shrink-0">
                    {t('Govorec', 'Speaker')}
                  </span>
                  <SpeakerNameInput name={sp} onRename={onRenameSpeaker} t={t} />
                  <span className="text-[11px] text-white/30 shrink-0">
                    {(data.arguments || []).length} {t('arg.', 'args')}
                  </span>
                </div>
                <textarea
                  rows={3}
                  value={data.position || ''}
                  onChange={(e) => onUpdateSpeakerMeta(sp, 'position', e.target.value)}
                  placeholder={t('Pozicija (kratek povzetek stališča)', 'Position (brief stance summary)')}
                  className="w-full bg-dark-900/60 border border-white/10 rounded-lg px-3 py-2 text-sm text-white/85 focus:border-amber-400/40 focus:outline-none resize-y"
                />
              </div>
            )
          })}
        </div>
      </div>

      {/* Arguments list (mixed across all speakers on this side) */}
      <div className="space-y-3">
        <div className="flex items-center justify-between mb-1">
          <label className="text-[11px] uppercase tracking-wider text-white/50 font-semibold">
            {t('Argumenti', 'Arguments')}
          </label>
          <button onClick={onAddArgumentToSide} className="text-xs text-white/70 hover:text-white px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/15 transition-colors">
            + {t('Dodaj argument', 'Add argument')}
          </button>
        </div>

        {items.length === 0 && (
          <div className="text-xs text-white/30 italic py-6 text-center border border-dashed border-white/10 rounded-lg">
            {t('Ni argumentov. Klikni "Dodaj argument" za prvega.', 'No arguments. Click "Add argument" to start.')}
          </div>
        )}

        {items.map(({ speaker, idx, arg }, listIdx) => {
          // Find rebuttals targeting this argument (by exact target_claim text match,
          // OR by matching to=speaker if target_claim is empty/no longer matches)
          const argText = (arg.argument || '').trim()
          const linkedRebuttalIndices = rebuttals
            .map((r, ri) => ({ r, ri }))
            .filter(({ r }) => {
              const tc = (r.target_claim || '').trim()
              if (tc && tc === argText) return true
              if (tc && argText && (tc.startsWith(argText.slice(0, 80)) || argText.startsWith(tc.slice(0, 80)))) return true
              return false
            })
          return (
            <ArgumentCard
              key={`${speaker}-${idx}`}
              arg={arg}
              speaker={speaker}
              sideSpeakers={side.speakers}
              allSpeakerNames={allSpeakerNames}
              displayIndex={listIdx + 1}
              onChangeField={(field, value) => onUpdateArgument(speaker, idx, field, value)}
              onDelete={() => onDeleteArgument(speaker, idx)}
              onMove={(toSp) => onMoveArgument(speaker, idx, toSp)}
              onUpdatePremise={(pi, v) => onUpdatePremise(speaker, idx, pi, v)}
              onAddPremise={() => onAddPremise(speaker, idx)}
              onRemovePremise={(pi) => onRemovePremise(speaker, idx, pi)}
              speakerBadgeColor={c.badge}
              linkedRebuttals={linkedRebuttalIndices}
              onAddRebuttal={() => onAddRebuttal({ targetSpeaker: speaker, targetClaim: argText })}
              onUpdateRebuttal={onUpdateRebuttal}
              onDeleteRebuttal={onDeleteRebuttal}
              allSpeakerNamesForRebut={allSpeakerNames}
              t={t}
            />
          )
        })}
      </div>
    </div>
  )
}

/* ── Argument editor card ───────────────────────────────────────────── */

function ArgumentCard({
  arg, speaker, sideSpeakers, allSpeakerNames, displayIndex,
  onChangeField, onDelete, onMove,
  onUpdatePremise, onAddPremise, onRemovePremise,
  speakerBadgeColor,
  linkedRebuttals = [], onAddRebuttal, onUpdateRebuttal, onDeleteRebuttal,
  allSpeakerNamesForRebut = [],
  t,
}) {
  return (
    <div className="bg-dark-600/50 border border-white/10 rounded-xl p-4 space-y-3 hover:border-white/20 transition-colors">
      {/* Top row: index + speaker badge + type + delete */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-white/40 font-mono">#{displayIndex}</span>

        {/* Speaker badge — clickable dropdown to reassign */}
        <select
          value={speaker}
          onChange={(e) => onMove(e.target.value)}
          className={`px-2.5 py-1 rounded-md text-[11px] uppercase tracking-wider font-bold border ${speakerBadgeColor} bg-transparent appearance-none cursor-pointer hover:brightness-125`}
          title={t('Pripiši drugemu govorcu', 'Reassign to another speaker')}
        >
          {allSpeakerNames.map(sp => (
            <option key={sp} value={sp} className="bg-dark-800 text-white normal-case tracking-normal">
              {sp}
            </option>
          ))}
        </select>

        <div className="flex gap-1.5 ml-auto">
          <select
            value={arg.type || 'factual'}
            onChange={(e) => onChangeField('type', e.target.value)}
            className="bg-dark-900/60 border border-white/10 rounded px-2 py-1 text-[11px] text-white/80 hover:border-white/30"
          >
            <option value="factual">factual</option>
            <option value="normative">normative</option>
            <option value="causal">causal</option>
            <option value="definitional">definitional</option>
            <option value="debatable">debatable</option>
          </select>
          <button
            onClick={onDelete}
            title={t('Izbriši argument', 'Delete argument')}
            className="text-red-400/80 hover:text-red-300 hover:bg-red-500/10 border border-red-500/30 hover:border-red-500/50 px-2 py-1 rounded transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3" />
            </svg>
          </button>
        </div>
      </div>

      {/* Argument text — bigger, easier to edit */}
      <textarea
        rows={5}
        value={arg.argument || ''}
        onChange={(e) => onChangeField('argument', e.target.value)}
        className="w-full bg-dark-900/70 border border-white/10 rounded-lg px-3 py-2.5 text-[15px] leading-relaxed text-white focus:border-accent-red/40 focus:outline-none resize-y"
        placeholder={t('Argument — popolna veriga sklepanja...', 'Argument — complete chain of reasoning...')}
      />

      {/* Premises — visually distinct, bluish tint */}
      <div className="rounded-lg border border-blue-500/10 bg-blue-500/[0.03] p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] uppercase tracking-wider text-blue-300/70 font-semibold">
            {t('Premise', 'Premises')} ({(arg.premises || []).length})
          </span>
          <button onClick={onAddPremise} className="text-[11px] text-white/50 hover:text-white px-2 py-0.5 rounded hover:bg-white/5">
            + {t('premisa', 'premise')}
          </button>
        </div>
        {(arg.premises || []).length === 0 && (
          <div className="text-[11px] text-white/30 italic py-1">
            {t('Brez premis', 'No premises')}
          </div>
        )}
        <div className="space-y-1.5">
          {(arg.premises || []).map((p, pi) => (
            <div key={pi} className="flex gap-2">
              <input
                value={p}
                onChange={(e) => onUpdatePremise(pi, e.target.value)}
                className="flex-1 bg-dark-900/70 border border-white/10 rounded px-2.5 py-1.5 text-[13px] text-white/85 focus:border-blue-400/40 focus:outline-none"
              />
              <button
                onClick={() => onRemovePremise(pi)}
                className="text-red-400/50 hover:text-red-400 text-base px-2 hover:bg-red-500/10 rounded"
                title={t('Odstrani', 'Remove')}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Rebuttals — visually distinct, orange/red tint to signal "challenge" */}
      <div className="rounded-lg border border-orange-500/15 bg-orange-500/[0.03] p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] uppercase tracking-wider text-orange-300/70 font-semibold">
            {t('Odbitja na ta argument', 'Rebuttals to this argument')} ({linkedRebuttals.length})
          </span>
          <button
            onClick={onAddRebuttal}
            className="text-[11px] text-orange-200/80 hover:text-white px-2.5 py-1 rounded bg-orange-500/10 hover:bg-orange-500/20 border border-orange-500/30"
          >
            + {t('Napiši rebuttal', 'Write rebuttal')}
          </button>
        </div>
        {linkedRebuttals.length === 0 && (
          <div className="text-[11px] text-white/30 italic py-1">
            {t('Brez rebuttal-a. Klikni "Napiši rebuttal" za nov vnos.', 'No rebuttals. Click "Write rebuttal" to add one.')}
          </div>
        )}
        <div className="space-y-2">
          {linkedRebuttals.map(({ r, ri }) => (
            <RebuttalCard
              key={ri}
              rebuttal={r}
              allSpeakers={allSpeakerNamesForRebut}
              originalArgumentSpeaker={speaker}
              onChangeField={(field, value) => onUpdateRebuttal(ri, field, value)}
              onDelete={() => onDeleteRebuttal(ri)}
              t={t}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

/* ── Rebuttal editor (one card) ──────────────────────────────────────── */
// Visualizes one link in the chain: argument → REBUTTAL → counter-rebuttal.
// The counter-rebuttal field is a proper textarea (not a tiny one-liner) so
// the user can capture how the argument's original speaker responded back.

function RebuttalCard({ rebuttal, allSpeakers, originalArgumentSpeaker, onChangeField, onDelete, t }) {
  return (
    <div className="bg-dark-900/40 border border-orange-500/20 rounded-lg p-3 space-y-3">
      {/* Header: who is rebutting + type + delete */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wider text-orange-300/70 font-semibold">
          {t('Rebuttal pisca:', 'Rebuttal by:')}
        </span>
        <select
          value={rebuttal.by || ''}
          onChange={(e) => onChangeField('by', e.target.value)}
          className="bg-dark-800 border border-orange-500/30 rounded px-2 py-0.5 text-[11px] text-orange-200 font-semibold uppercase tracking-wide"
        >
          {allSpeakers.map(sp => (
            <option key={sp} value={sp} className="bg-dark-800 text-white normal-case tracking-normal">
              {sp}
            </option>
          ))}
        </select>
        <select
          value={rebuttal.rebuttal_type || 'direct_contradiction'}
          onChange={(e) => onChangeField('rebuttal_type', e.target.value)}
          className="bg-dark-900/60 border border-white/10 rounded px-2 py-0.5 text-[11px] text-white/70"
          title={t('Tip rebuttala', 'Rebuttal type')}
        >
          <option value="direct_contradiction">direct contradiction</option>
          <option value="undermining_premise">undermining premise</option>
          <option value="alternative_explanation">alternative explanation</option>
          <option value="questioning_warrant">questioning warrant</option>
        </select>
        <button
          onClick={onDelete}
          className="ml-auto text-red-400/70 hover:text-red-300 hover:bg-red-500/10 px-2 py-0.5 rounded text-[11px]"
          title={t('Izbriši', 'Delete')}
        >
          ×
        </button>
      </div>

      {/* Rebuttal content */}
      <textarea
        rows={3}
        value={rebuttal.rebuttal_content || ''}
        onChange={(e) => onChangeField('rebuttal_content', e.target.value)}
        placeholder={t('Vsebina rebuttala...', 'Rebuttal content...')}
        className="w-full bg-dark-900/60 border border-white/10 rounded-lg px-2.5 py-1.5 text-[13px] text-white/90 focus:border-orange-400/40 focus:outline-none resize-y"
      />

      {/* ── Counter-rebuttal (4th link in the chain) ─────────────────
          Visually nested inside the rebuttal card with arrow indicator and
          purple tint to show it's a separate level (response from the
          original argument speaker back to this rebuttal). ─────────── */}
      <div className="rounded-lg border border-purple-400/15 bg-purple-500/[0.04] p-2.5">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-purple-300/60 text-base leading-none">↳</span>
          <span className="text-[10px] uppercase tracking-wider text-purple-300/70 font-semibold">
            {t('Counter-rebuttal', 'Counter-rebuttal')}
          </span>
          {originalArgumentSpeaker && (
            <span className="text-[10px] text-white/40">
              ({t('odgovor', 'response from')} <span className="text-purple-200/80 font-medium">{originalArgumentSpeaker}</span>)
            </span>
          )}
        </div>
        <textarea
          rows={2}
          value={rebuttal.response || ''}
          onChange={(e) => onChangeField('response', e.target.value)}
          placeholder={t('Kako se je originalni govorec odzval na ta rebuttal? (opcijsko)', "How did the original speaker respond to this rebuttal? (optional)")}
          className="w-full bg-dark-900/60 border border-white/10 rounded px-2.5 py-1.5 text-[12px] text-white/85 focus:border-purple-400/40 focus:outline-none resize-y"
        />
      </div>
    </div>
  )
}
