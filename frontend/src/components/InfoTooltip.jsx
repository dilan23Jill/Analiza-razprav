import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

/**
 * InfoTooltip — a small (?) icon that shows a tooltip on hover/click.
 * Uses a portal so it's never clipped by parent overflow.
 * Prefers LEFT placement; auto-flips right/below if no space.
 */
export default function InfoTooltip({ text, className = '' }) {
  const [show, setShow] = useState(false)
  const iconRef = useRef(null)
  const tooltipRef = useRef(null)
  const [pos, setPos] = useState({ top: 0, left: 0, arrow: 'right' })

  const reposition = useCallback(() => {
    if (!iconRef.current || !tooltipRef.current) return
    const icon = iconRef.current.getBoundingClientRect()
    const tip = tooltipRef.current.getBoundingClientRect()
    const gap = 10
    let top, left, arrow

    // Try LEFT first
    if (icon.left - tip.width - gap >= 4) {
      left = icon.left - tip.width - gap
      arrow = 'right'
    } else {
      // Flip RIGHT
      left = icon.right + gap
      arrow = 'left'
    }

    // Vertical: center on icon
    top = icon.top + icon.height / 2 - tip.height / 2

    // Clamp vertical so tooltip stays in viewport
    if (top < 8) top = 8
    if (top + tip.height > window.innerHeight - 8) {
      top = window.innerHeight - 8 - tip.height
    }

    setPos({ top, left, arrow })
  }, [])

  // Position on show & on scroll/resize
  useEffect(() => {
    if (!show) return
    // Initial position after first render
    requestAnimationFrame(reposition)

    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
    return () => {
      window.removeEventListener('scroll', reposition, true)
      window.removeEventListener('resize', reposition)
    }
  }, [show, reposition])

  // Close on click outside
  useEffect(() => {
    if (!show) return
    function handleClick(e) {
      if (iconRef.current && !iconRef.current.contains(e.target) &&
          tooltipRef.current && !tooltipRef.current.contains(e.target)) {
        setShow(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [show])

  const arrowBase = 'absolute w-0 h-0 border-t-[6px] border-b-[6px] border-t-transparent border-b-transparent'

  return (
    <span
      ref={iconRef}
      className={`relative inline-flex items-center ${className}`}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onClick={(e) => { e.stopPropagation(); setShow(s => !s) }}
    >
      <span className="cursor-help inline-flex items-center justify-center w-3.5 h-3.5 rounded-full bg-white/15 text-white/50 text-[9px] font-bold leading-none hover:bg-white/25 hover:text-white/70 transition-colors">
        ?
      </span>

      {show && createPortal(
        <span
          ref={tooltipRef}
          style={{
            position: 'fixed',
            top: pos.top,
            left: pos.left,
            zIndex: 99999,
            boxShadow: '0 8px 30px rgba(0,0,0,0.18), 0 2px 8px rgba(0,0,0,0.10)',
          }}
          className="px-3 py-2 rounded-lg bg-pure-white text-gray-800 text-[11px] leading-relaxed whitespace-normal min-w-[180px] max-w-[260px] border border-gray-200 pointer-events-none"
        >
          {text}
          {/* Arrow */}
          {pos.arrow === 'right' && (
            <span
              className={`${arrowBase} border-l-[6px] border-l-white`}
              style={{ position: 'absolute', top: '50%', left: '100%', transform: 'translateY(-50%)' }}
            />
          )}
          {pos.arrow === 'left' && (
            <span
              className={`${arrowBase} border-r-[6px] border-r-white`}
              style={{ position: 'absolute', top: '50%', right: '100%', transform: 'translateY(-50%)' }}
            />
          )}
        </span>,
        document.body
      )}
    </span>
  )
}
