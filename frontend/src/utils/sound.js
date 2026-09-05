/**
 * Completion chime — Web Audio API, no audio files needed.
 *
 * chimeOnceFor(jobId) plays the chime at most ONCE per job id, no matter how
 * many components detect the completion (JobStatusPage + global ActiveJobs
 * poller can both see it).
 */

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
    // Browsers may suspend audio without a user gesture — try to resume;
    // if it stays suspended the chime silently does nothing (no crash).
    if (ctx.state === 'suspended') ctx.resume().catch(() => {})

    // Pleasant two-tone "ding-ding": E5 → A5
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
    // Close the context once the sound is done (free the audio resource)
    setTimeout(() => ctx.close().catch(() => {}), 1200)
  } catch {
    // Sound is a nice-to-have — never break the app because of it
  }
}
