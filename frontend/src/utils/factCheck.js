export function getFactCheckClaims(factCheck) {
  if (!factCheck) return []
  return factCheck.fact_checks || factCheck.claims || []
}
