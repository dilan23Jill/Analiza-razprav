import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { submitAnalysis, submitUploadAnalysis, probeYoutube } from '../services/api'
import { useLanguage } from '../utils/LanguageContext'
import TimeRangeSlider from '../components/TimeRangeSlider'
import { useAuth } from '../services/auth'
import { useActiveJobs } from '../hooks/ActiveJobsContext'

// YouTube URL regex (matches the same patterns the backend accepts)
const YT_URL_RE = /^https?:\/\/((www\.)?youtube\.com\/watch\?v=[\w-]{11}|youtu\.be\/[\w-]{11}|(www\.)?youtube\.com\/shorts\/[\w-]{11})/

export default function AnalyzePage() {
  const navigate = useNavigate()
  const fileInputRef = useRef(null)
  const { t } = useLanguage()
  const { user } = useAuth()
  const { primaryActiveJob: runningJob, initialLoading: checkingJobs } = useActiveJobs()

  const [inputType, setInputType] = useState('youtube')  // 'youtube' or 'upload'
  const [url, setUrl] = useState('')
  const [file, setFile] = useState(null)
  const [title, setTitle] = useState('')
  const [mode, setMode] = useState('solo')
  const [language, setLanguage] = useState('sl')
  const [speakerNames, setSpeakerNames] = useState('')
  const [startTime, setStartTime] = useState('')
  const [endTime, setEndTime] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  // ── Auto-detected media duration & metadata (powers the trim slider) ──
  const [videoDuration, setVideoDuration] = useState(null)   // seconds or null
  const [videoTitle, setVideoTitle]       = useState('')
  const [probing, setProbing]             = useState(false)
  const [probeError, setProbeError]       = useState('')

  // ── Auto-probe YouTube URL (debounced) ────────────────────────────
  useEffect(() => {
    if (inputType !== 'youtube') return
    setProbeError('')
    if (!url || !YT_URL_RE.test(url.trim())) {
      setVideoDuration(null)
      setVideoTitle('')
      return
    }
    let cancelled = false
    setProbing(true)
    const timer = setTimeout(async () => {
      try {
        const meta = await probeYoutube(url.trim())
        if (cancelled) return
        if (meta.duration > 0) {
          setVideoDuration(meta.duration)
          if (meta.title && !title) setTitle(meta.title)
          if (meta.title) setVideoTitle(meta.title)
        }
      } catch (err) {
        if (cancelled) return
        // Probe failures are non-fatal — user can still submit, just without auto-sized slider
        setVideoDuration(null)
        setProbeError(err.message || 'Probe failed')
      } finally {
        if (!cancelled) setProbing(false)
      }
    }, 600)
    return () => { cancelled = true; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, inputType])

  // ── Read uploaded file duration via HTML5 audio (no upload needed) ─
  useEffect(() => {
    if (inputType !== 'upload' || !file) {
      if (inputType === 'upload') {
        setVideoDuration(null)
        setVideoTitle('')
      }
      return
    }
    const objectUrl = URL.createObjectURL(file)
    const audio = document.createElement('audio')
    audio.preload = 'metadata'
    audio.src = objectUrl
    let cleanedUp = false
    const cleanup = () => {
      if (cleanedUp) return
      cleanedUp = true
      URL.revokeObjectURL(objectUrl)
    }
    audio.onloadedmetadata = () => {
      const dur = audio.duration
      if (Number.isFinite(dur) && dur > 0) {
        setVideoDuration(Math.round(dur))
      }
      cleanup()
    }
    audio.onerror = () => {
      // Some video containers can't be probed by <audio>. Try <video> instead.
      const video = document.createElement('video')
      video.preload = 'metadata'
      video.src = objectUrl
      video.onloadedmetadata = () => {
        const dur = video.duration
        if (Number.isFinite(dur) && dur > 0) setVideoDuration(Math.round(dur))
        cleanup()
      }
      video.onerror = cleanup
    }
    return cleanup
  }, [file, inputType])

  // Reset probed metadata when switching input type
  useEffect(() => {
    setVideoDuration(null)
    setVideoTitle('')
    setProbeError('')
  }, [inputType])

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      let job
      if (inputType === 'upload' && file) {
        job = await submitUploadAnalysis(file, mode, language, speakerNames, title, startTime, endTime)
      } else {
        job = await submitAnalysis(url, mode, language, speakerNames, title, startTime, endTime)
      }
      navigate(`/job/${job.job_id}`)
    } catch (err) {
      if (err.status === 403) {
        setError(t.noCreditsError)
      } else if (err.status === 429) {
        setError(t.rateLimitError)
      } else {
        setError(err.message || t.submitError)
      }
    } finally {
      setLoading(false)
    }
  }

  function handleFileChange(e) {
    const selected = e.target.files[0]
    if (selected) {
      setFile(selected)
      if (!title) {
        const name = selected.name.replace(/\.[^/.]+$/, '')
        setTitle(name)
      }
    }
  }

  const canSubmit = inputType === 'youtube' ? !!url : !!file

  if (checkingJobs) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-accent-red border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-xl sm:text-2xl font-bold text-white mb-2">{t.newAnalysisTitle}</h1>
      <p className="text-white/40 text-sm mb-6 sm:mb-8">
        {t.newAnalysisSubtitle}
      </p>

      {user && (
        <div className="mb-6 p-3 rounded-lg bg-dark-600/50 border border-white/5 text-sm">
          {user.is_admin ? (
            <div>
              <span className="text-green-400 font-semibold">Admin</span>
              <span className="text-white/30 ml-1">({t.unlimited})</span>
            </div>
          ) : (
            <div>
              <span className="text-white/40">{t.credits}: </span>
              <span className={`font-semibold ${
                user.credits > 0 ? 'text-green-400' : 'text-red-400'
              }`}>
                {user.credits}
              </span>
            </div>
          )}
        </div>
      )}

      {runningJob ? (
        /* ── BLOCKING STATE — analysis in progress, form is hidden ──
             User must wait for current analysis to finish (or click "Open"
             to view it). Form returns automatically when poll detects the
             job is no longer in queued/processing state. */
        <div className="rounded-2xl bg-gradient-to-br from-accent-red/15 to-orange-500/10 border border-accent-red/40 p-6 sm:p-8">
          <div className="flex items-start gap-4">
            <span className="relative flex shrink-0 w-10 h-10 items-center justify-center mt-0.5">
              <span className="absolute inset-0 rounded-full bg-accent-red/30 animate-ping" />
              <span className="relative w-6 h-6 border-2 border-accent-red border-t-transparent rounded-full animate-spin" />
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-[10px] uppercase tracking-[0.2em] text-accent-red font-bold mb-1.5">
                {t.analysisInProgress}
              </div>
              <h2 className="text-white text-base sm:text-lg font-semibold mb-1.5">
                {t.waitForCurrentAnalysis}
              </h2>
              <p className="text-sm text-white/65 leading-relaxed mb-3">
                {t.blockingHint}
              </p>
              <div className="bg-dark-900/40 border border-white/10 rounded-lg px-3 py-2 mb-4">
                <div className="text-[10px] uppercase tracking-wider text-white/40 mb-0.5">
                  {t.currentStep}
                </div>
                <div className="text-sm text-white/85 truncate">
                  {runningJob.progress || t.jobProcessing}
                </div>
              </div>
              <button
                type="button"
                onClick={() => navigate(`/job/${runningJob.job_id}`)}
                className="px-4 py-2 bg-accent-red hover:bg-brand-600 text-pure-white text-sm font-semibold rounded-lg transition-colors"
              >
                {t.openCurrentAnalysis}
              </button>
            </div>
          </div>
        </div>
      ) : (
      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Debate title (optional) */}
        <div>
          <label className="block text-sm text-white/60 mb-2">
            {t.debateTitle} <span className="text-white/30">({t.optional})</span>
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t.debateTitlePlaceholder}
            className="w-full bg-dark-600 border border-white/10 rounded-lg px-4 py-3
                       text-white placeholder-white/30 focus:outline-none
                       focus:border-accent-red/50 transition-colors text-sm"
          />
          <p className="text-white/20 text-xs mt-1.5">
            {t.debateTitleHint}
          </p>
        </div>

        {/* Input type selector */}
        <div>
          <label className="block text-sm text-white/60 mb-2">{t.source}</label>
          <div className="grid grid-cols-2 gap-3">
            <ModeButton
              active={inputType === 'youtube'}
              onClick={() => setInputType('youtube')}
              title={t.youtubeUrl}
              desc={t.videoLink}
            />
            <ModeButton
              active={inputType === 'upload'}
              onClick={() => setInputType('upload')}
              title={t.uploadFile}
              desc={t.fileFormats}
            />
          </div>
        </div>

        {/* YouTube URL or File Upload */}
        {inputType === 'youtube' ? (
          <div>
            <label className="block text-sm text-white/60 mb-2">{t.youtubeUrl}</label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
              required={inputType === 'youtube'}
              className="w-full bg-dark-600 border border-white/10 rounded-lg px-4 py-3
                         text-white placeholder-white/30 focus:outline-none
                         focus:border-accent-red/50 transition-colors"
            />
            {/* Probe status */}
            {probing && (
              <p className="text-white/30 text-xs mt-1.5">
                {t.probingVideo}
              </p>
            )}
            {!probing && videoDuration && (
              <p className="text-white/40 text-xs mt-1.5">
                {videoTitle ? <span className="text-white/60">{videoTitle}</span> : null}
                {videoTitle ? ' · ' : ''}
                {Math.floor(videoDuration / 60)}:{String(videoDuration % 60).padStart(2, '0')} {t.minutesShort}
              </p>
            )}
            {!probing && probeError && (
              <p className="text-yellow-400/70 text-xs mt-1.5">
                {t.probeFailedHint}
              </p>
            )}
          </div>
        ) : (
          <div>
            <label className="block text-sm text-white/60 mb-2">{t.file}</label>
            <div
              onClick={() => fileInputRef.current?.click()}
              className={`w-full bg-dark-600 border border-dashed rounded-lg px-4 py-6
                         text-center cursor-pointer transition-colors ${
                file
                  ? 'border-accent-red/50 text-white'
                  : 'border-white/20 text-white/40 hover:border-white/40'
              }`}
            >
              {file ? (
                <div>
                  <div className="font-medium text-sm">{file.name}</div>
                  <div className="text-xs text-white/30 mt-1">
                    {(file.size / 1024 / 1024).toFixed(1)} MB
                    {videoDuration ? (
                      <> · {Math.floor(videoDuration / 60)}:{String(videoDuration % 60).padStart(2, '0')} {t.minutesShort}</>
                    ) : null}
                  </div>
                </div>
              ) : (
                <div>
                  <div className="text-2xl mb-1">+</div>
                  <div className="text-sm">{t.clickToSelect}</div>
                  <div className="text-xs text-white/20 mt-1">
                    MP3, MP4, M4A, WAV, WebM, OGG, FLAC, AAC, MKV, AVI (max 500MB)
                  </div>
                </div>
              )}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".mp3,.mp4,.m4a,.wav,.webm,.ogg,.flac,.aac,.mkv,.avi"
              onChange={handleFileChange}
              className="hidden"
            />
          </div>
        )}

        {/* Time range — show ONLY after a YouTube URL is pasted (and validated)
            or a file is selected. Avoids confusing empty slider on page load. */}
        {((inputType === 'youtube' && url && YT_URL_RE.test(url.trim())) ||
          (inputType === 'upload' && file)) && (
          <TimeRangeSlider
            startTime={startTime}
            endTime={endTime}
            onStartChange={setStartTime}
            onEndChange={setEndTime}
            videoDuration={videoDuration}
            videoTitle={videoTitle}
          />
        )}

        {/* Mode — solo (covers solo + reaction) or debate (strictly 1v1) */}
        <div>
          <label className="block text-sm text-white/60 mb-2">{t.analysisMode}</label>
          <div className="grid grid-cols-2 gap-3">
            <ModeButton
              active={mode === 'solo'}
              onClick={() => setMode('solo')}
              title={t.solo}
              desc={t.soloDesc}
            />
            <ModeButton
              active={mode === 'debate'}
              onClick={() => setMode('debate')}
              title={t.debate}
              desc={t.debateDesc}
            />
          </div>
        </div>

        {/* Speaker names (optional) */}
        <div>
          <label className="block text-sm text-white/60 mb-2">
            {t.speakerNames} <span className="text-white/30">({t.optional})</span>
          </label>
          <input
            type="text"
            value={speakerNames}
            onChange={(e) => setSpeakerNames(e.target.value)}
            placeholder={t.speakerNamesPlaceholder}
            className="w-full bg-dark-600 border border-white/10 rounded-lg px-4 py-3
                       text-white placeholder-white/30 focus:outline-none
                       focus:border-accent-red/50 transition-colors text-sm"
          />
          <p className="text-white/20 text-xs mt-1.5">
            {t.speakerNamesHint}
          </p>
        </div>

        {/* Language */}
        <div>
          <label className="block text-sm text-white/60 mb-2">{t.analysisLanguage}</label>
          <div className="grid grid-cols-2 gap-3">
            <ModeButton
              active={language === 'sl'}
              onClick={() => setLanguage('sl')}
              title={t.slovenian}
              desc={t.slovenianDesc}
            />
            <ModeButton
              active={language === 'en'}
              onClick={() => setLanguage('en')}
              title={t.english}
              desc={t.englishDesc}
            />
          </div>
        </div>

        {error && (
          <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading || !canSubmit}
          className="w-full py-3 bg-accent-red hover:bg-brand-600 disabled:bg-white/10
                     disabled:text-white/30 text-white font-semibold rounded-lg
                     transition-colors text-sm"
        >
          {loading ? t.submitting : t.startAnalysis}
        </button>
      </form>
      )}
    </div>
  )
}

function ModeButton({ active, onClick, title, desc }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`p-3 rounded-lg border text-left transition-all ${
        active
          ? 'border-accent-red bg-accent-red/10 text-white'
          : 'border-white/10 bg-dark-600/50 text-white/50 hover:border-white/20'
      }`}
    >
      <div className="font-medium text-sm">{title}</div>
      <div className="text-xs mt-0.5 opacity-60">{desc}</div>
    </button>
  )
}
