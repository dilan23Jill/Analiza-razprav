// Povečava vmesnika. Ctrl + kolešček, razteg na sledilni ploščici in
// Ctrl + plus/minus/0 spremenijo korensko velikost pisave namesto povečave
// brskalnika. Vse velikosti v vmesniku so v enotah rem, zato se pisava,
// razmiki in okvirji povečajo skupaj, besedilo se prelomi v nove vrstice,
// postavitev strani (meje sm/md/lg) pa ostane enaka.

const STORAGE_KEY = 'uiZoom'
const MIN = 0.5
const MAX = 3
const KEY_STEP = 1.1

let zoom = 1
let badge = null
let badgeTimer = null

function clamp(z) {
  return Math.min(MAX, Math.max(MIN, z))
}

function showBadge() {
  if (!badge) {
    badge = document.createElement('div')
    badge.setAttribute('aria-live', 'polite')
    badge.style.cssText = [
      'position:fixed', 'right:16px', 'bottom:16px', 'z-index:9999',
      'padding:6px 12px', 'border-radius:8px', 'font:600 13px Inter,sans-serif',
      'background:rgba(17,21,29,0.88)', 'color:#fff', 'pointer-events:none',
      'transition:opacity .25s ease', 'opacity:0',
    ].join(';')
    document.body.appendChild(badge)
  }
  badge.textContent = `${Math.round(zoom * 100)} %`
  badge.style.opacity = '1'
  clearTimeout(badgeTimer)
  badgeTimer = setTimeout(() => { badge.style.opacity = '0' }, 900)
}

function apply(z, { silent = false } = {}) {
  zoom = Math.round(clamp(z) * 100) / 100
  if (Math.abs(zoom - 1) < 0.03) zoom = 1
  document.documentElement.style.fontSize = zoom === 1 ? '' : `${zoom * 100}%`
  try {
    localStorage.setItem(STORAGE_KEY, String(zoom))
  } catch {
  }
  if (!silent) showBadge()
}

function onWheel(event) {
  if (!event.ctrlKey) return
  event.preventDefault()
  // Razteg na sledilni plošči pošilja majhne korake, kolešček miške pa
  // velike (okoli 100), zato je korak omejen.
  const delta = Math.max(-50, Math.min(50, event.deltaY))
  apply(zoom * Math.exp(-delta * 0.004))
}

function onKey(event) {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return
  if (event.key === '+' || event.key === '=' || event.code === 'NumpadAdd') {
    event.preventDefault()
    apply(zoom * KEY_STEP)
  } else if (event.key === '-' || event.code === 'NumpadSubtract') {
    event.preventDefault()
    apply(zoom / KEY_STEP)
  } else if (event.key === '0' || event.code === 'Numpad0') {
    event.preventDefault()
    apply(1)
  }
}

export function installUiZoom() {
  let saved = 1
  try {
    saved = parseFloat(localStorage.getItem(STORAGE_KEY)) || 1
  } catch {
  }
  apply(saved, { silent: true })
  window.addEventListener('wheel', onWheel, { passive: false })
  window.addEventListener('keydown', onKey)
}
