
const _chimedJobs = new Set()

export function chimeOnceFor(jobId) {
  if (!jobId || _chimedJobs.has(jobId)) return
  _chimedJobs.add(jobId)
  playCompletionChime()
}

function playCompletionChime() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    if (ctx.state === 'suspended') ctx.resume().catch(() => {})

    const notes = [
      { freq: 659.25, delay: 0 },
      { freq: 880.0, delay: 0.18 },
    ]
    for (const { freq, delay } of notes) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      osc.connect(gain)
      gain.connect(ctx.destination)
      const t = ctx.currentTime + delay
      gain.gain.setValueAtTime(0.0001, t)
      gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.6)
      osc.start(t)
      osc.stop(t + 0.65)
    }
    setTimeout(() => ctx.close().catch(() => {}), 1200)
  } catch {
  }
}
