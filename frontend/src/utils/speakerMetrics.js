
import { getFactCheckClaims } from './factCheck'

function getFactChecksForSpeaker(factCheck = {}, speakerName) {
  const claims = getFactCheckClaims(factCheck)
  return claims.filter(claim => {
    if (!speakerName) return false
    if (claim.speaker) return claim.speaker === speakerName
    return false
  })
}

export function buildSpeakerProfile({
  speakerName,
  speakerData = {},
  analysis = {},
  factCheck = {},
}) {
  const fallacies = (analysis.fallacies || [])
    .map((fallacy, i) => ({ ...fallacy, _index: i }))
    .filter(fallacy => fallacy.speaker === speakerName)
  const evasions = (analysis.evasions || []).filter(evasion => evasion.evading_speaker === speakerName)
  const rebuttals = (analysis.rebuttals || []).filter(
    rebuttal => rebuttal.by === speakerName || rebuttal.to === speakerName
  )
  return {
    speakerData,
    fallacies,
    evasions,
    rebuttals,
    factCheckClaims: getFactChecksForSpeaker(factCheck, speakerName),
  }
}
