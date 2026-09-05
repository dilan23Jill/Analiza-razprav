import { useState, useRef, useCallback, useEffect } from 'react'
import { useLanguage } from '../utils/LanguageContext'

/**
 * Dual-handle range slider for trimming video.
 *
 * If `videoDuration` (seconds) is provided (from a YouTube probe or a
 * client-side audio element), the slider auto-sizes to it and the manual
 * "10m / 25m / 1h / 2h" picker is hidden — the user sees the real timeline.
 *
 * If duration is unknown, we fall back to a manual max-duration picker.
 */

function secondsToHMS(totalSec) {
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

const DEFAULT_MAX = 25 * 60 // 25 min — only used when duration is unknown

export default function TimeRangeSlider({
  startTime, endTime, onStartChange, onEndChange,
  videoDuration = null,
  videoTitle = '',
}) {
  const { t } = useLanguage()
  const trackRef = useRef(null)
  const [dragging, setDragging] = useState(null)
  const [manualMax, setManualMax] = useState(DEFAULT_MAX)
  const [startPct, setStartPct] = useState(0)
  const [endPct, setEndPct] = useState(100)

  // If we know the real duration, use it; otherwise the user-picked manual max.
  const knownDuration = Number.isFinite(videoDuration) && videoDuration > 0
  const maxSec = knownDuration ? Math.round(videoDuration) : manualMax

  const startSec = Math.round((startPct / 100) * maxSec)
  const endSec = Math.round((endPct / 100) * maxSec)
  const isFullRange = startPct === 0 && endPct === 100

  // When we learn the real duration, reset the slider so the user doesn't
  // have a stale percentage from the manual-max world.
  useEffect(() => {
    if (knownDuration) {
      setStartPct(0)
      setEndPct(100)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoDuration])

  // Sync outward
  useEffect(() => {
    onStartChange(startPct <= 0 ? '' : secondsToHMS(startSec))
  }, [startPct, maxSec])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    onEndChange(endPct >= 100 ? '' : secondsToHMS(endSec))
  }, [endPct, maxSec])  // eslint-disable-line react-hooks/exhaustive-deps

  const getPctFromX = useCallback((clientX) => {
    const rect = trackRef.current?.getBoundingClientRect()
    if (!rect) return 0
    return Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100))
  }, [])

  const handlePointerDown = useCallback((handle) => (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragging(handle)
  }, [])

  const handlePointerMove = useCallback((e) => {
    if (!dragging) return
    const pct = getPctFromX(e.clientX ?? e.touches?.[0]?.clientX)
    if (dragging === 'start') {
      setStartPct(Math.min(pct, endPct - 2))
    } else {
      setEndPct(Math.max(pct, startPct + 2))
    }
  }, [dragging, startPct, endPct, getPctFromX])

  const handlePointerUp = useCallback(() => setDragging(null), [])

  useEffect(() => {
    if (!dragging) return
    const move = (e) => handlePointerMove(e)
    const up = () => handlePointerUp()
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [dragging, handlePointerMove, handlePointerUp])

  // Click on track → move nearest handle
  const handleTrackClick = (e) => {
    if (dragging) return
    const pct = getPctFromX(e.clientX)
    const dStart = Math.abs(pct - startPct)
    const dEnd = Math.abs(pct - endPct)
    if (dStart < dEnd) {
      setStartPct(Math.min(pct, endPct - 2))
    } else {
      setEndPct(Math.max(pct, startPct + 2))
    }
  }

  function reset() {
    setStartPct(0)
    setEndPct(100)
  }

  // Markers: 5 evenly spaced
  const markers = [0, 25, 50, 75, 100]

  const totalLabel = knownDuration
    ? `${t.trimLength}: ${secondsToHMS(maxSec)}`
    : null

  return (
    <div className="select-none">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white/60">
          {t.trimClip}
          <span className="text-white/30 ml-1">({t.optional})</span>
        </span>
        <div className="flex items-center gap-2">
          {totalLabel && (
            <span className="text-[10px] text-white/40 font-mono">{totalLabel}</span>
          )}
          {!isFullRange && (
            <button type="button" onClick={reset}
              className="text-xs text-white/30 hover:text-white/60 transition-colors">
              {t.resetTrim}
            </button>
          )}
        </div>
      </div>

      {/* Time display */}
      <div className="flex items-center justify-center gap-3 mb-3">
        <div className="bg-dark-600 border border-white/10 rounded-lg px-3 py-1.5 min-w-[64px] text-center">
          <span className="text-white text-sm font-mono">{secondsToHMS(startSec)}</span>
        </div>
        <span className="text-white/30 text-xs">—</span>
        <div className="bg-dark-600 border border-white/10 rounded-lg px-3 py-1.5 min-w-[64px] text-center">
          <span className="text-white text-sm font-mono">
            {endPct >= 100 ? t.trimEnd : secondsToHMS(endSec)}
          </span>
        </div>
        {!isFullRange && (
          <span className="text-white/20 text-xs">({secondsToHMS(endSec - startSec)})</span>
        )}
      </div>

      {/* Slider */}
      <div
        ref={trackRef}
        className="relative h-10 flex items-center cursor-pointer touch-none"
        onPointerDown={handleTrackClick}
      >
        {/* Background */}
        <div className="absolute left-0 right-0 h-1.5 bg-white/10 rounded-full" />

        {/* Active range */}
        <div
          className="absolute h-1.5 rounded-full transition-colors"
          style={{
            left: `${startPct}%`,
            right: `${100 - endPct}%`,
            background: isFullRange ? 'rgba(255,255,255,0.15)' : 'rgba(239,68,68,0.5)',
          }}
        />

        {/* Markers */}
        <div className="absolute left-0 right-0 top-7">
          {markers.map(pct => (
            <span key={pct} className="absolute text-[10px] text-white/15 -translate-x-1/2"
              style={{ left: `${pct}%` }}>
              {secondsToHMS(Math.round((pct / 100) * maxSec))}
            </span>
          ))}
        </div>

        {/* Start handle */}
        <div
          className={`absolute w-5 h-5 rounded-full border-2 -translate-x-1/2 z-10 transition-all
            ${dragging === 'start'
              ? 'bg-red-500 border-white scale-110 shadow-lg shadow-red-500/40'
              : 'bg-dark-600 border-red-500 hover:bg-red-500/20'}`}
          style={{ left: `${startPct}%` }}
          onPointerDown={handlePointerDown('start')}
        />

        {/* End handle */}
        <div
          className={`absolute w-5 h-5 rounded-full border-2 -translate-x-1/2 z-10 transition-all
            ${dragging === 'end'
              ? 'bg-red-500 border-white scale-110 shadow-lg shadow-red-500/40'
              : 'bg-dark-600 border-red-500 hover:bg-red-500/20'}`}
          style={{ left: `${endPct}%` }}
          onPointerDown={handlePointerDown('end')}
        />
      </div>

      {/* Manual max-duration selector — only when duration is unknown */}
      {!knownDuration && (
        <div className="flex items-center justify-end mt-4 gap-1">
          <span className="text-[10px] text-white/20 mr-1">
            {t.trimLengthLabel}
          </span>
          {[
            { val: 10 * 60, label: '10m' },
            { val: 25 * 60, label: '25m' },
            { val: 60 * 60, label: '1h' },
            { val: 2 * 60 * 60, label: '2h' },
            { val: 4 * 60 * 60, label: '4h' },
          ].map(({ val, label }) => (
            <button key={val} type="button"
              onClick={() => { setManualMax(val); reset() }}
              className={`px-2 py-0.5 rounded text-[10px] transition-colors ${
                manualMax === val ? 'bg-red-500/20 text-red-400' : 'text-white/20 hover:text-white/40'
              }`}>
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
