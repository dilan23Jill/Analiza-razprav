"""
Fact-checker v3 — Enhanced verification pipeline.

Improvements over v2:
  - Wikidata SPARQL API (structured facts, dates, numbers)
  - CrossRef API (130M+ academic works via DOI)
  - Claim decomposition (break complex claims into atomic sub-claims)
  - Cross-source weighted agreement scoring (formal consensus mechanism)
  + All v2 features: parallel workers, OpenAlex, Google FactCheck,
    Perplexity, disk cache, retry logic, i18n
"""

import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

from cache import get_cache
from config_loader import get as cfg, sampling_kwargs
from translations import t, get_verdict_label

logger = logging.getLogger(__name__)
load_dotenv()

# ── MERILA ZA RAZSODBO ──────────────────────────────────────────────────────
# Ta pravila prejme en sam klic: razsojevalni korak v _judge_claim. Zbiralci
# jih ne dobijo, ker ne razsojajo — vrnejo gradivo, ne mnenja. Ker razsodbo
# izreče eno mesto po enih merilih, se merila ne morejo razhajati med seboj.
VERDICT_RULES = """VERDICT DEFINITIONS — apply consistently:
  • TRUE            — core assertion accurate as stated (minor rounding tolerated)
  • PARTIALLY_TRUE  — right direction, but numbers/details are off, or important
                      qualifying context is missing
  • MISLEADING      — contains technically true elements but creates a FALSE overall
                      impression (framing, cherry-picked baseline, critical omission)
  • FALSE           — core assertion contradicted by reliable sources
  • UNVERIFIABLE    — no adequate sources either way (do NOT guess)

HOW TO JUDGE — three rules, applied in this order:
  1. IMPRESSION, NOT WORDING: judge by the impression the claim leaves on a listener,
     not only its literal phrasing.
  2. TEMPORAL: verify the claim AS OF THE TIME IT WAS MADE where context allows. A claim
     that was true when stated but is outdated now is explained as outdated, NOT called FALSE.
  3. QUALIFIERS BIND: dates, numbers, named entities and scope words (all / most / only /
     first / never) are part of the claim. If a qualifier is wrong, the claim is wrong even
     when the rest is right — an event misdated by a year is FALSE, not PARTIALLY_TRUE."""


# ── retry decorator (no external deps) ─────────────────────────────────────

def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect OpenAI / generic 429 rate-limit errors across SDK versions."""
    msg = str(exc).lower()
    if "rate_limit" in msg or "rate limit" in msg or "429" in msg:
        return True
    # OpenAI SDK >= 1.x exposes status_code on RateLimitError
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429 or status == "rate_limit_exceeded":
        return True
    return False


def _parse_retry_after_seconds(exc: Exception) -> Optional[float]:
    """Extract a Retry-After hint from the exception, if present."""
    # Some OpenAI errors include "Please try again in 12.3s" in the message
    import re as _re
    m = _re.search(r"try again in (\d+(?:\.\d+)?)\s*s", str(exc))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # Or as a header
    headers = getattr(exc, "response", None)
    if headers is not None:
        try:
            ra = headers.headers.get("retry-after")
            if ra:
                return float(ra)
        except Exception:
            pass
    return None


def _retry(max_attempts: int = 3, base_wait: float = 1.0, max_wait: float = 60.0):
    """Simple retry with exponential backoff.

    Special-cases 429 rate-limit errors: TPM windows are 60s, so on a 429 we
    wait at least until the window rolls over (or until Retry-After tells us)
    rather than the short exponential backoff used for transient network errors.
    """
    import time, functools, random

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    if _is_rate_limit_error(e):
                        # TPM windows are 60s. Use Retry-After if provided, else wait
                        # 65s (60s window + 5s buffer) on first 429, double thereafter.
                        hint = _parse_retry_after_seconds(e)
                        wait = hint if hint else min(65 * attempt, 180)
                        logger.warning(
                            "      Rate limited (429). Waiting %.1fs before retry %d/%d: %s",
                            wait, attempt, max_attempts, str(e)[:200],
                        )
                    else:
                        wait = min(base_wait * (2 ** (attempt - 1)) + random.uniform(0, 1), max_wait)
                        logger.warning("      Retry %d/%d after %.1fs: %s", attempt, max_attempts, wait, e)
                    time.sleep(wait)
        return wrapper
    return decorator


class FactChecker:
    """
    Enhanced fact-check pipeline with verification mechanisms.
    """

    SCIENTIFIC_CLAIM_TYPES = {"scientific", "health", "medical", "statistic"}

    # Postane True ob prvi napaki 429 pri Semantic Scholar in ga za preostanek
    # zagona izloči. Ponastavi se ob vsakem novem preverjanju dejstev.
    _scholar_rate_limited = False

    # ── Engine routing by claim type ─────────────────────────────────────
    # Grok išče tudi po omrežju X, kar pri dolgo uveljavljenih dejstvih dodaja
    # predvsem šum.
    GROK_SKIP_TYPES = {"scientific", "health", "medical", "historical", "geographic"}

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment")
        self.client = OpenAI(api_key=api_key)
        self.cache = get_cache()

        # Optional: Perplexity client
        self._perplexity_client: Optional[OpenAI] = None
        pplx_key = os.getenv("PERPLEXITY_API_KEY")
        if pplx_key and cfg("fact_checking.engines.perplexity", False):
            self._perplexity_client = OpenAI(api_key=pplx_key, base_url="https://api.perplexity.ai")

        # Optional: Grok (xAI) client — web search + X/Twitter search + rhetoric detection
        self._grok_client: Optional[OpenAI] = None
        grok_key = os.getenv("XAI_API_KEY")
        if grok_key and cfg("fact_checking.engines.grok", False):
            self._grok_client = OpenAI(api_key=grok_key, base_url="https://api.x.ai/v1")

    # ── VERDICT HELPERS ────────────────────────────────────────────────────

    @classmethod
    def _get_verdict_meta(cls, verdict: str) -> dict:
        """Get translated verdict metadata."""
        return get_verdict_label(verdict or "UNVERIFIABLE")

    # ── UTILITY ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_domain(url: str) -> str:
        if "://" in url:
            domain = url.split("://", 1)[1].split("/", 1)[0].lower()
            return domain[4:] if domain.startswith("www.") else domain
        return ""

    @staticmethod
    def _extract_numbers(text: str) -> List[float]:
        pattern = r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*(?:million|billion|thousand))?\b'
        suffix_pattern = r'\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*([kmb])\b'
        numbers = []

        # First handle explicit suffixes like "5k", "3.2m", "1b"
        for num_str, suffix in re.findall(suffix_pattern, text.lower()):
            try:
                cleaned = num_str.replace(',', '')
                multiplier = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000}[suffix]
                numbers.append(float(cleaned) * multiplier)
            except Exception:
                continue

        # Then handle spelled-out multipliers and plain numbers
        for match in re.findall(pattern, text.lower()):
            try:
                cleaned = match.replace(',', '')
                parts = cleaned.split()
                if not parts:
                    continue
                num_part = parts[0]
                if 'million' in match:
                    numbers.append(float(num_part) * 1_000_000)
                elif 'billion' in match:
                    numbers.append(float(num_part) * 1_000_000_000)
                elif 'thousand' in match:
                    numbers.append(float(num_part) * 1_000)
                else:
                    numbers.append(float(cleaned))
            except Exception:
                continue
        return numbers

    # ── CLAIM DECOMPOSITION (break complex claims into atomic parts) ────────

    def decompose_claims(self, claims: List[Dict]) -> List[Dict]:
        """Break complex multi-part claims into atomic verifiable sub-claims.
        E.g. 'Slovenia joined EU and NATO in 2004' → two separate claims.
        Returns the expanded list with parent references."""
        if not cfg("fact_checking.claim_decomposition", True):
            return claims

        logger.info("   Decomposing complex claims...")

        cache_key = f"decompose:{hashlib.sha256(str([c['exact_claim'] for c in claims]).encode()).hexdigest()[:16]}"
        cached = self.cache.get(cache_key)
        if cached:
            logger.info("   Decomposition cache hit (%d claims)", len(cached))
            return cached

        # Build a batch of claims for the LLM
        claims_text = "\n".join(
            f"{i+1}. [{c.get('claim_type', 'unknown')}] \"{c.get('exact_claim', '')}\""
            for i, c in enumerate(claims)
        )

        lang_instruction = t("llm.language_instruction")

        prompt = f"""Analyze each claim below and determine if it contains MULTIPLE independent
verifiable facts. If a claim makes 2+ distinct factual assertions, decompose it into atomic
sub-claims that can each be verified independently.

RULES:
- Only decompose claims that CLEARLY contain multiple independent facts
- Each sub-claim must be self-contained and verifiable on its own
- Keep simple claims unchanged (output them as-is)
- Preserve the speaker, claim_type, and context from the original
- Add "parent_claim" field referencing the original text when decomposing

Return ONLY valid JSON:
{{"decomposed": [
  {{"original_index": 1, "sub_claims": [
    {{"exact_claim": "atomic claim 1", "claim_type": "...", "context": "..."}},
    {{"exact_claim": "atomic claim 2", "claim_type": "...", "context": "..."}}
  ]}},
  {{"original_index": 2, "sub_claims": [
    {{"exact_claim": "unchanged simple claim", "claim_type": "...", "context": "..."}}
  ]}}
]}}
{lang_instruction}"""

        try:
            decomp_model = cfg("fact_checking.decompose_model", "gpt-4o-mini")
            response = self.client.chat.completions.create(
                model=decomp_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"CLAIMS:\n{claims_text}"},
                ],
                **sampling_kwargs(decomp_model, 0.0),
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            decomposed_list = result.get("decomposed", [])

            expanded: List[Dict] = []
            original_count = len(claims)

            for entry in decomposed_list:
                orig_idx = entry.get("original_index", 1) - 1  # 1-indexed → 0-indexed
                if orig_idx < 0 or orig_idx >= len(claims):
                    continue

                original = claims[orig_idx]
                sub_claims = entry.get("sub_claims", [])

                if len(sub_claims) <= 1:
                    # Not decomposed — keep original
                    expanded.append(original)
                else:
                    # Decomposed — create sub-claims with parent reference
                    for sc in sub_claims:
                        sub = {
                            **original,
                            "exact_claim": sc.get("exact_claim", original["exact_claim"]),
                            "claim_type": sc.get("claim_type", original.get("claim_type", "unknown")),
                            "context": sc.get("context", original.get("context", "")),
                            "parent_claim": original["exact_claim"],
                            "_decomposed": True,
                        }
                        expanded.append(sub)

            # Ensure we didn't lose any claims
            seen_indices = {(e.get("original_index", 1) - 1) for e in decomposed_list}
            for i, claim in enumerate(claims):
                if i not in seen_indices:
                    expanded.append(claim)

            new_count = len(expanded)
            if new_count > original_count:
                logger.info("   Decomposed: %d claims → %d atomic sub-claims (+%d)",
                            original_count, new_count, new_count - original_count)
            else:
                logger.info("   No complex claims found — all claims are atomic")

            self.cache.set(cache_key, expanded)
            return expanded

        except Exception as e:
            logger.warning("   Claim decomposition failed: %s — using original claims", e)
            return claims

    # ── SCIENTIFIC LITERATURE SEARCH ────────────────────────────────────────

    @staticmethod
    def _pubmed_search(query: str, max_results: int = 5) -> List[Dict]:
        results = []
        try:
            params = urllib.parse.urlencode({
                "db": "pubmed", "term": query, "retmax": max_results,
                "retmode": "json", "sort": "relevance",
            })
            url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}"
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            ids = data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                return []

            fetch_params = urllib.parse.urlencode({
                "db": "pubmed", "id": ",".join(ids), "retmode": "xml", "rettype": "abstract",
            })
            fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{fetch_params}"
            with urllib.request.urlopen(fetch_url, timeout=15) as r:
                xml_data = r.read()

            root = ET.fromstring(xml_data)
            for article in root.findall(".//PubmedArticle"):
                try:
                    pmid = article.findtext(".//PMID") or ""
                    title = article.findtext(".//ArticleTitle") or "Unknown"
                    journal = article.findtext(".//Journal/Title") or ""
                    year = article.findtext(".//PubDate/Year") or article.findtext(".//PubDate/MedlineDate", "")[:4]
                    abstract_parts = article.findall(".//AbstractText")
                    abstract = " ".join((t_el.text or "") for t_el in abstract_parts if t_el.text).strip()
                    results.append({
                        "pmid": pmid, "title": title, "journal": journal, "year": year,
                        "abstract": abstract[:600] + ("..." if len(abstract) > 600 else ""),
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        "source_type": "peer_reviewed",
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.warning("PubMed search failed: %s", e)
        return results

    @staticmethod
    def _semantic_scholar_search(query: str, max_results: int = 5) -> List[Dict]:
        """Semantic Scholar without a key is rate limited hard.

        On one run it answered 429 to nearly every request and contributed no
        papers at all, while each attempt still cost a second of waiting on a
        claim that had four other academic sources. Once it has refused for rate
        limiting, it stays out for the rest of the run and is recorded among the
        collectors that did not run.
        """
        if FactChecker._scholar_rate_limited:
            return []
        results = []
        try:
            params = urllib.parse.urlencode({
                "query": query, "limit": max_results,
                "fields": "title,year,citationCount,influentialCitationCount,journal,abstract,externalIds,openAccessPdf",
            })
            url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "FactChecker/2.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            for paper in data.get("data", []):
                doi = (paper.get("externalIds") or {}).get("DOI", "")
                pdf_url = (paper.get("openAccessPdf") or {}).get("url", "")
                paper_url = (
                    f"https://doi.org/{doi}" if doi
                    else pdf_url or f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}"
                )
                citations = paper.get("citationCount", 0)
                journal_name = (paper.get("journal") or {}).get("name", "")
                abstract = (paper.get("abstract") or "")[:600]
                results.append({
                    "title": paper.get("title", "Unknown"), "year": str(paper.get("year") or ""),
                    "journal": journal_name, "citations": citations,
                    "influential_citations": paper.get("influentialCitationCount", 0),
                    "abstract": abstract + ("..." if len(paper.get("abstract") or "") > 600 else ""),
                    "url": paper_url, "source_type": "peer_reviewed",
                })
        except Exception as e:
            if "429" in str(e):
                FactChecker._scholar_rate_limited = True
                logger.warning("Semantic Scholar is rate limiting, skipping it for the rest of the run")
            else:
                logger.warning("Semantic Scholar search failed: %s", e)
        return results

    @staticmethod
    def _openalex_search(query: str, max_results: int = 5) -> List[Dict]:
        """Search OpenAlex — completely free, no API key required."""
        results = []
        try:
            params = urllib.parse.urlencode({
                "search": query,
                "per_page": max_results,
                "sort": "relevance_score:desc",
                "filter": "type:article",
                "select": "id,doi,title,publication_year,cited_by_count,"
                          "primary_location,authorships,abstract_inverted_index",
            })
            email = os.getenv("OPENALEX_EMAIL", "")
            mailto = f"&mailto={email}" if email else ""
            url = f"https://api.openalex.org/works?{params}{mailto}"
            req = urllib.request.Request(url, headers={"User-Agent": "FactChecker/2.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            for work in data.get("results", []):
                doi = (work.get("doi") or "").replace("https://doi.org/", "")
                title = work.get("title", "Unknown")
                year = str(work.get("publication_year") or "")
                citations = work.get("cited_by_count", 0)

                abstract = ""
                inv_idx = work.get("abstract_inverted_index") or {}
                if inv_idx:
                    word_positions = []
                    for word, positions in inv_idx.items():
                        for pos in positions:
                            word_positions.append((pos, word))
                    word_positions.sort()
                    abstract = " ".join(w for _, w in word_positions)[:600]

                loc = work.get("primary_location") or {}
                source = loc.get("source") or {}
                journal = source.get("display_name", "")

                paper_url = f"https://doi.org/{doi}" if doi else work.get("id", "")

                results.append({
                    "title": title, "year": year, "journal": journal,
                    "citations": citations,
                    "abstract": abstract + ("..." if len(abstract) >= 600 else ""),
                    "url": paper_url, "source_type": "peer_reviewed",
                    "database": "openalex",
                })
        except Exception as e:
            logger.warning("OpenAlex search failed: %s", e)
        return results

    # ── WIKIDATA SPARQL SEARCH ─────────────────────────────────────────────

    @staticmethod
    def _wikidata_search(claim: str, max_results: int = 5) -> List[Dict]:
        """Search Wikidata for structured facts — dates, numbers, relationships.
        Uses the wbsearchentities API + SPARQL for property lookup.
        Free, no API key required."""
        results = []
        if not cfg("fact_checking.engines.wikidata", True):
            return results

        # Step 1: Extract key entities from the claim for search
        # Heuristic: capitalized words (supports Unicode for non-English text) and numbers
        import re as _re
        # Match capitalized sequences (Unicode-aware) — works for English, Slovenian, etc.
        entities = _re.findall(r'\b[A-ZÀ-ŽА-Я][a-zà-žа-я]+(?:\s+[A-ZÀ-ŽА-Я][a-zà-žа-я]+)*\b', claim)

        if not entities:
            return results

        for entity_name in entities[:3]:  # Search top 3 entities
            try:
                params = urllib.parse.urlencode({
                    "action": "wbsearchentities",
                    "search": entity_name,
                    "language": "en",
                    "limit": 3,
                    "format": "json",
                })
                url = f"https://www.wikidata.org/w/api.php?{params}"
                req = urllib.request.Request(url, headers={"User-Agent": "FactChecker/3.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read())

                for item in data.get("search", [])[:2]:
                    entity_id = item.get("id", "")
                    label = item.get("label", "")
                    description = item.get("description", "")

                    if not entity_id:
                        continue

                    # Step 2: Fetch key properties via SPARQL
                    sparql_query = f"""
                    SELECT ?propLabel ?valLabel WHERE {{
                      wd:{entity_id} ?prop ?val .
                      ?property wikibase:directClaim ?prop .
                      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,sl" . }}
                    }} LIMIT 20
                    """
                    sparql_url = "https://query.wikidata.org/sparql"
                    sparql_params = urllib.parse.urlencode({
                        "query": sparql_query,
                        "format": "json",
                    })
                    try:
                        sparql_req = urllib.request.Request(
                            f"{sparql_url}?{sparql_params}",
                            headers={"User-Agent": "FactChecker/3.0", "Accept": "application/json"},
                        )
                        with urllib.request.urlopen(sparql_req, timeout=15) as sr:
                            sparql_data = json.loads(sr.read())

                        properties = {}
                        for binding in sparql_data.get("results", {}).get("bindings", []):
                            prop_label = binding.get("propLabel", {}).get("value", "")
                            val_label = binding.get("valLabel", {}).get("value", "")
                            if prop_label and val_label and not val_label.startswith("http"):
                                properties[prop_label] = val_label

                        if properties:
                            # Build a fact summary
                            fact_lines = [f"{k}: {v}" for k, v in list(properties.items())[:10]]
                            results.append({
                                "entity_id": entity_id,
                                "entity": label,
                                "description": description,
                                "properties": properties,
                                "fact_summary": "; ".join(fact_lines),
                                "url": f"https://www.wikidata.org/wiki/{entity_id}",
                                "source_type": "structured_knowledge",
                                "database": "wikidata",
                            })
                    except Exception:
                        # SPARQL failed but entity search worked
                        results.append({
                            "entity_id": entity_id,
                            "entity": label,
                            "description": description,
                            "properties": {},
                            "fact_summary": description,
                            "url": f"https://www.wikidata.org/wiki/{entity_id}",
                            "source_type": "structured_knowledge",
                            "database": "wikidata",
                        })

                    if len(results) >= max_results:
                        break
            except Exception as e:
                logger.warning("Wikidata search failed for '%s': %s", entity_name, e)
                continue

            if len(results) >= max_results:
                break

        return results[:max_results]

    # ── CROSSREF API SEARCH ──────────────────────────────────────────────────

    @staticmethod
    def _crossref_search(query: str, max_results: int = 5) -> List[Dict]:
        """Search CrossRef for academic works by DOI — 130M+ records, free, no key."""
        results = []
        if not cfg("fact_checking.engines.crossref", True):
            return results

        try:
            params = urllib.parse.urlencode({
                "query": query,
                "rows": max_results,
                "sort": "relevance",
                "order": "desc",
                "select": "DOI,title,author,published-print,published-online,"
                          "container-title,is-referenced-by-count,abstract,URL,type",
            })
            email = os.getenv("OPENALEX_EMAIL", "")
            mailto = f"&mailto={email}" if email else ""
            url = f"https://api.crossref.org/works?{params}{mailto}"
            req = urllib.request.Request(url, headers={"User-Agent": "FactChecker/3.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())

            for item in data.get("message", {}).get("items", []):
                doi = item.get("DOI", "")
                title_list = item.get("title", [])
                title = title_list[0] if title_list else "Unknown"

                # Get year from published-print or published-online
                date_parts = (
                    item.get("published-print", {}).get("date-parts", [[]])
                    or item.get("published-online", {}).get("date-parts", [[]])
                )
                year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""

                journal_list = item.get("container-title", [])
                journal = journal_list[0] if journal_list else ""
                citations = item.get("is-referenced-by-count", 0)

                abstract = item.get("abstract", "")
                # CrossRef abstracts often have JATS XML tags — strip them
                if abstract:
                    abstract = re.sub(r"<[^>]+>", "", abstract)[:600]

                authors = []
                for author in (item.get("author") or [])[:3]:
                    name = f"{author.get('given', '')} {author.get('family', '')}".strip()
                    if name:
                        authors.append(name)

                results.append({
                    "title": title,
                    "year": year,
                    "journal": journal,
                    "citations": citations,
                    "authors": authors,
                    "abstract": abstract + ("..." if len(abstract) >= 600 else ""),
                    "url": f"https://doi.org/{doi}" if doi else item.get("URL", ""),
                    "doi": doi,
                    "source_type": "peer_reviewed",
                    "database": "crossref",
                })
        except Exception as e:
            logger.warning("CrossRef search failed: %s", e)

        return results

    @staticmethod
    def _google_factcheck_search(query: str, max_results: int = 5) -> List[Dict]:
        """Search Google Fact Check Tools API for existing fact-checks."""
        results = []
        if not cfg("fact_checking.engines.google_factcheck", True):
            return results

        try:
            api_key = os.getenv("GOOGLE_FACTCHECK_API_KEY", "")
            params = urllib.parse.urlencode({
                "query": query,
                "languageCode": "en",
                "pageSize": max_results,
                **({"key": api_key} if api_key else {}),
            })
            url = f"https://factchecktools.googleapis.com/v1alpha1/claims:search?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "FactChecker/2.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            for claim in data.get("claims", []):
                for review in claim.get("claimReview", []):
                    results.append({
                        "claim_reviewed": claim.get("text", ""),
                        "claimant": claim.get("claimant", ""),
                        "publisher": review.get("publisher", {}).get("name", ""),
                        "rating": review.get("textualRating", ""),
                        "url": review.get("url", ""),
                        "title": review.get("title", ""),
                        "date": review.get("reviewDate", "Unknown"),
                        "source_type": "fact_checker",
                    })
        except Exception as e:
            logger.warning("Google Fact Check API failed: %s", e)
        return results

    @_retry(max_attempts=2)
    def _perplexity_find_batch(self, claims_batch: List[Dict]) -> Dict[str, Optional[Dict]]:
        """Search for up to five claims in a single Perplexity call.

        Perplexity is a FINDER, not a judge. It is asked for findings and
        citations, never for a verdict, because only one step in this pipeline
        decides — `_judge_claim`. Keeping the finders silent on the verdict is
        what makes their material combinable instead of competing.
        """
        if not self._perplexity_client or not claims_batch:
            return {}

        batch_size = len(claims_batch)
        logger.info("      [Perplexity] Searching for %d claims...", batch_size)

        claims_text = "\n".join(
            f"{i+1}. [{c.get('claim_type', 'unknown')}] \"{c.get('exact_claim', '')}\""
            for i, c in enumerate(claims_batch)
        )

        try:
            response = self._perplexity_client.chat.completions.create(
                model="sonar-pro",
                messages=[{
                    "role": "user",
                    "content": (
                        f"Research each of these {batch_size} claims with web search. "
                        "For EACH claim report ONLY what the sources say:\n"
                        "- the relevant figures, dates and statements you found\n"
                        "- who published them\n"
                        "- where the sources disagree with each other\n\n"
                        "Do NOT state a verdict and do NOT say whether the claim is "
                        "true or false. Report findings only.\n\n"
                        f"Claims:\n{claims_text}\n\n"
                        "Respond with a numbered list matching the claims above."
                    ),
                }],
            )
            text = response.choices[0].message.content
            citations = list(getattr(response, "citations", []) or [])

            # Odgovor pokriva ves paket, zato ga je treba razrezati po trditvah.
            # Sicer bi razsodnik sodil o tujem gradivu.
            sections = self._split_numbered_sections(text, batch_size)
            if sections is None:
                logger.warning(
                    "      [Perplexity] Could not split the answer into %d sections "
                    "- discarding it rather than filing it under the wrong claim",
                    batch_size)

            results = {}
            for i, c in enumerate(claims_batch):
                claim_text = c.get("exact_claim", "")
                results[claim_text] = {
                    "findings": sections[i] if sections else "",
                    "citations": citations,
                    "search_method": "perplexity_sonar_pro_batch",
                } if sections else None
            return results
        except Exception as e:
            logger.warning("      [Perplexity] Batch failed: %s", e)
            return {}

    @_retry(max_attempts=2)
    def _perplexity_find(self, claim: str, claim_type: str,
                         _prefetched: Optional[Dict] = None) -> Optional[Dict]:
        """Perplexity for one claim. Returns findings and citations, no verdict."""
        if _prefetched and claim in _prefetched:
            return _prefetched[claim]

        if not self._perplexity_client:
            return None

        logger.info("      [Perplexity] Running grounded search...")
        try:
            response = self._perplexity_client.chat.completions.create(
                model="sonar-pro",
                messages=[{
                    "role": "user",
                    "content": (
                        "Research this claim with web search and report ONLY what the "
                        "sources say: relevant figures, dates and statements, who "
                        "published them, and where sources disagree. Do NOT state a "
                        "verdict.\n\n"
                        f"Claim: \"{claim}\"\nType: {claim_type}"
                    ),
                }],
            )
            return {
                "findings": response.choices[0].message.content or "",
                "citations": list(getattr(response, "citations", []) or []),
                "search_method": "perplexity_sonar_pro",
            }
        except Exception as e:
            logger.warning("      [Perplexity] Failed: %s", e)
            return None

    # ── PRIPIS BESEDILA PRAVI TRDITVI ────────────────────────────────────

    @staticmethod
    def _split_numbered_sections(text: str, count: int) -> Optional[List[str]]:
        """Cut a numbered batch answer into one section per claim.

        The batch prompt asks for "a numbered list matching the claims above",
        so item i belongs to claim i. The markers 1..count must appear in that
        order; anything else (a missing item, a renumbered list, a stray "2." in
        prose) returns None, and the caller then casts no verdicts at all. A
        section assigned to the wrong claim is worse than no section: it does
        not merely lose a vote, it invents one.
        """
        if not text or count <= 0:
            return None
        starts: List[int] = []
        expected = 1
        for m in re.finditer(r"^[ \t]*\**[ \t]*(\d+)[ \t]*[.)]", text, re.M):
            if int(m.group(1)) == expected:
                starts.append(m.start())
                expected += 1
                if expected > count:
                    break
        if len(starts) != count:
            return None
        bounds = starts + [len(text)]
        return [text[bounds[i]:bounds[i + 1]].strip() for i in range(count)]

    # ── GROK (xAI) — WEB + X/TWITTER SEARCH ──────────────────────────────

    @_retry(max_attempts=2)
    def _grok_find(self, claim: str, claim_type: str, claim_context: str = "") -> Optional[Dict]:
        """Search the web and X for material on a claim. Returns findings, no verdict.

        Args:
            claim_context: a one-sentence note on what was being argued when the
                claim was made. Used so the search ALSO samples sources from the
                speaker's own tradition, not only opposing ones. It is keyed on
                the claim's subject matter, never on who said it.
        """
        if not self._grok_client:
            return None

        logger.info("      [Grok] Searching web and X...")
        lang_instruction = t("llm.language_instruction")

        stance_block = (
            f"\nCLAIM CONTEXT: {claim_context}\n"
            "→ If the claim belongs to a particular tradition (religious, ideological, "
            "professional), you MUST also look for sources from within that tradition, not "
            "only mainstream or opposing ones. Example: a claim defending the Catholic Church "
            "should be checked against Catholic theological and historical sources (Vatican "
            "documents, Catholic encyclopedias, Catholic scholars) AS WELL AS secular and "
            "opposing sources. Goal: a BALANCED evidence base.\n"
            if claim_context else ""
        )

        prompt = f"""You are a researcher with access to live web search and X (Twitter).
Gather material on this claim. You do NOT judge it — another step does that.

CLAIM: "{claim}"
TYPE: {claim_type}{stance_block}
INSTRUCTIONS:
1. Search the web for authoritative sources about this claim.
2. SOURCE BALANCE: if the claim is associated with a particular tradition (religious,
   ideological, professional), include sources FROM that tradition AS WELL AS
   independent and opposing ones.
3. Search X for public discourse and expert commentary on the claim.
4. Report the figures, dates and statements the sources give, who published them,
   and where the sources contradict each other.
5. Note whether a source describes the situation at the time the claim was made or
   at some later date.
6. Do NOT say whether the claim is true or false, and do NOT give a verdict.

Return ONLY valid JSON:
{{
  "findings": "4-6 sentences: what the web sources and X discourse actually say, with figures and dates",
  "web_sources": [
    {{
      "title": "...",
      "url": "https://...",
      "perspective": "speaker_aligned|neutral|opposing",
      "relevant_quote": "..."
    }}
  ]
}}
{lang_instruction}"""

        try:
            response = self._grok_client.chat.completions.create(
                model=cfg("fact_checking.grok_model", "grok-4.3"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                extra_body={"reasoning_effort": cfg("fact_checking.grok_reasoning_effort", "low")},
            )
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
            result["search_method"] = "grok_web_x_search"
            logger.info("      [Grok] Found %d sources", len(result.get("web_sources") or []))
            return result
        except Exception as e:
            logger.warning("      [Grok] Search failed: %s", e)
            return None

    def _build_science_query(self, claim: str) -> str:
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "has", "have", "had",
            "that", "this", "with", "from", "for", "and", "but", "or", "not",
            "can", "could", "would", "should", "will", "be", "been", "being",
            "it", "its", "their", "they", "them", "we", "you", "he", "she",
            "said", "claim", "claimed", "says", "according", "to", "of", "in",
            "at", "by", "as", "on", "up", "so", "if", "than", "then", "also",
        }
        words = re.findall(r"[a-zA-Z]{4,}", claim.lower())
        keywords = [w for w in words if w not in stop_words]
        return " ".join(keywords[:6])

    def _verify_scientific_claim(self, claim: str, claim_type: str) -> Dict:
        """Run PubMed + Semantic Scholar + OpenAlex and return combined evidence."""
        query = self._build_science_query(claim)
        logger.info("      [SciSearch] query: %s", query)

        cache_key = f"sci:{hashlib.sha256(query.encode()).hexdigest()[:16]}"
        cached = self.cache.get(cache_key)
        if cached:
            logger.info("      [SciSearch] cache hit (%d papers)", cached.get("papers_found", 0))
            return cached

        pubmed_papers = (
            self._pubmed_search(query, max_results=5)
            if cfg("fact_checking.engines.pubmed", True)
            else []
        )
        scholar_papers = (
            self._semantic_scholar_search(query, max_results=5)
            if cfg("fact_checking.engines.semantic_scholar", True)
            else []
        )
        openalex_papers = (
            self._openalex_search(query, max_results=5)
            if cfg("fact_checking.engines.openalex", True)
            else []
        )
        crossref_papers = (
            self._crossref_search(query, max_results=5)
            if cfg("fact_checking.engines.crossref", True)
            else []
        )

        seen: set = set()
        combined: List[Dict] = []
        for p in pubmed_papers + scholar_papers + openalex_papers + crossref_papers:
            key = p["title"].lower()[:60]
            if key not in seen:
                seen.add(key)
                combined.append(p)

        def _score(p: Dict) -> float:
            c = p.get("citations", 0)
            y = int(p.get("year") or 0)
            return c * 0.7 + max(0, y - 2000) * 0.3

        combined.sort(key=_score, reverse=True)
        top = combined[:6]

        evidence_summary = ""
        for i, p in enumerate(top, 1):
            line = f"{i}. {p['title']} ({p.get('year', '')}) - {p.get('journal', '')}"
            if p.get("citations"):
                line += f" [{p['citations']} citations]"
            if p.get("abstract"):
                line += "\n   Abstract: " + p["abstract"]
            evidence_summary += line + "\n\n"

        logger.info(
            "      [SciSearch] %d papers (PubMed: %d, Scholar: %d, OpenAlex: %d, CrossRef: %d)",
            len(top), len(pubmed_papers), len(scholar_papers), len(openalex_papers),
            len(crossref_papers),
        )

        result = {
            "papers_found": len(top),
            "pubmed_count": len(pubmed_papers),
            "scholar_count": len(scholar_papers),
            "openalex_count": len(openalex_papers),
            "crossref_count": len(crossref_papers),
            "top_papers": top,
            "evidence_summary": evidence_summary.strip(),
            "search_query": query,
        }
        self.cache.set(cache_key, result)
        return result

    # ── SPLETNO ISKANJE ─────────────────────────────────────────────────────

    @_retry()
    def _web_search_find(self, claim: str, claim_type: str,
                         claim_context: str = "") -> Optional[Dict]:
        """Search the live web for material on a claim. Returns findings, no verdict.

        This used to be the step that produced the verdict, and everything the
        other collectors had found was pasted into its prompt so that it could
        weigh that material too. It no longer judges: it searches, reports what
        it found, and hands the result to `_judge_claim` alongside every other
        collector's material. Nothing a collector finds is now weighed by
        another collector.
        """
        ctx_hash = hashlib.sha256((claim_context or "").encode()).hexdigest()[:6]
        cache_key = f"web:find:{hashlib.sha256(claim.encode()).hexdigest()[:16]}:{ctx_hash}"
        cached = self.cache.get(cache_key)
        if cached:
            logger.info("      [WebSearch] cache hit")
            return cached

        if not cfg("fact_checking.engines.web_search", True):
            logger.info("      [WebSearch] disabled in config — skipped")
            return None

        stance_block = ""
        if claim_context:
            stance_block = (
                f"\nCLAIM CONTEXT: {claim_context}\n"
                "→ Aim for a BALANCED source set: if the claim belongs to a particular "
                "tradition, include sources from within it AS WELL AS independent and "
                "opposing sources. Tag each source with `perspective`: \"aligned\", "
                "\"neutral\" or \"opposing\".\n"
            )

        lang_instruction = t("llm.language_instruction")
        prompt = f"""You are a researcher. Gather material on the claim below with REAL web
search. You do NOT judge the claim — a separate step does that.

CLAIM: "{claim}"
TYPE: {claim_type}{stance_block}
INSTRUCTIONS:
1. Search for PRIMARY and AUTHORITATIVE sources.
2. Find AT LEAST 2-3 INDEPENDENT sources, on different domains.
3. For numerical claims report the EXACT figure each source gives.
4. For quotes find the original wording and the surrounding context.
5. Report where the sources contradict each other instead of picking a side.
6. State, for each figure, WHICH POINT IN TIME it describes.
7. Do NOT say whether the claim is true or false, and do NOT give a verdict.

Return ONLY valid JSON:
{{
  "findings": "4-6 sentences: what the sources actually say, with figures and dates",
  "sources": [
    {{
      "title": "Source title",
      "url": "https://...",
      "date": "YYYY-MM-DD or YYYY or Unknown",
      "relevant_quote": "Key finding, verbatim where possible",
      "source_type": "official_stat|peer_reviewed|fact_checker|news|other",
      "perspective": "aligned|neutral|opposing"
    }}
  ]
}}
{lang_instruction}"""

        try:
            response = self.client.responses.create(
                model="gpt-4o",
                tools=[{"type": "web_search_preview"}],
                input=prompt,
            )
            raw = ""
            for item in response.output:
                if hasattr(item, "content"):
                    for block in item.content:
                        if hasattr(block, "text"):
                            raw += block.text
            raw = raw.strip()
            if not raw:
                raise ValueError("Empty response")
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
            result["search_method"] = "web_search_preview"
            logger.info("      [WebSearch] found %d sources", len(result.get("sources") or []))
            self.cache.set(cache_key, result)
            return result
        except Exception as e:
            logger.warning("      [WebSearch] Failed: %s", e)
            return None

    # ── VERIFICATION CHECKS ─────────────────────────────────────────────────

    @staticmethod
    def _compute_evidence_metrics(sources: List[Dict]) -> Dict:
        """Koliko virov je bilo najdenih in s koliko različnih domen."""
        unique_domains: set = set()
        for source in sources:
            domain = FactChecker._extract_domain(source.get("url", ""))
            if domain:
                unique_domains.add(domain)
        return {
            "source_count": len(sources),
            "independent_domain_count": len(unique_domains),
        }

    def _finalise_result(self, result: Dict) -> Dict:
        """Prešteje vire in pripne oznako razsodbe. Razsodbe ne spremeni."""
        result["evidence_metrics"] = self._compute_evidence_metrics(result.get("sources", []))
        result["verdict_label"] = self._get_verdict_meta(
            (result.get("verdict") or "UNVERIFIABLE").upper())["label"]
        return result

    # ── ZBIRANJE GRADIVA ────────────────────────────────────────────────────

    def _gather_evidence(self, claim_data: Dict,
                         _perplexity_prefetched: Optional[Dict] = None) -> Dict:
        """Run every routed collector and return the material they found.

        No collector issues a verdict here. Which collectors run is decided by
        the claim's type alone, and what comes back is evidence rather than
        opinions. None of it is shown to another collector. The whole set goes
        to `_judge_claim` at once.
        """
        claim = claim_data["exact_claim"]
        claim_type = (claim_data.get("claim_type") or "unknown")

        # What was being argued when the claim was made. It comes from the
        # transcript and says nothing about WHO the speaker is: source balance
        # is keyed on the claim's subject matter, never on the person.
        ctx = (claim_data.get("context") or "").strip()
        claim_context = f"Stated while arguing: {ctx}" if ctx else ""

        ev: Dict = {
            "claim_context": claim_context,
            "papers": [], "wikidata": [], "factchecks": [],
            "web": None, "perplexity": None, "grok": None,
            "skipped": {},
        }

        # ── znanstvene zbirke — samo za znanstvene tipe
        if claim_type.lower() not in self.SCIENTIFIC_CLAIM_TYPES:
            ev["skipped"]["science"] = f"claim type '{claim_type}'"
        else:
            logger.info("      Running scientific literature search...")
            science = self._verify_scientific_claim(claim, claim_type)
            ev["papers"] = (science or {}).get("top_papers", []) or []
            if FactChecker._scholar_rate_limited:
                ev["skipped"]["semantic_scholar"] = "rate limited"

        # ── Wikidata — brezplačen vir, teče pri vsaki trditvi
        try:
            ct = claim_type.lower()
            wants_wikidata = (
                ct in ("statistic", "historical", "economic", "geographic", "policy")
                or bool(re.search(r"\b(19|20)\d{2}\b", claim))
                or bool(self._extract_numbers(claim))
            )
            if not cfg("fact_checking.engines.wikidata", True):
                ev["skipped"]["wikidata"] = "disabled"
            elif not wants_wikidata:
                ev["skipped"]["wikidata"] = "no year and no number in the claim"
            else:
                ev["wikidata"] = self._wikidata_search(claim, max_results=3) or []
        except Exception:
            ev["wikidata"] = []

        # ── Google Fact Check — brezplačen vir, teče pri vseh prednostih
        if cfg("fact_checking.engines.google_factcheck", True):
            ev["factchecks"] = self._google_factcheck_search(
                self._build_science_query(claim), max_results=3) or []
            if ev["factchecks"]:
                logger.info("      [FactCheck API] Found %d existing reviews", len(ev["factchecks"]))
        else:
            ev["skipped"]["google_factcheck"] = "disabled"

        # ── Perplexity — paketno, teče pri vseh prednostih
        ev["perplexity"] = self._perplexity_find(
            claim, claim_type, _prefetched=_perplexity_prefetched)

        # ── Grok — usmerjen po tipu trditve
        routed_out = (
            cfg("fact_checking.engine_routing", True)
            and claim_type.lower() in self.GROK_SKIP_TYPES
        )
        if routed_out:
            ev["skipped"]["grok"] = f"claim type '{claim_type}' adds noise on X"
            logger.info("      [Routing] Grok skipped for claim type '%s'", claim_type)
        else:
            ev["grok"] = self._grok_find(claim, claim_type, claim_context=claim_context)

        # ── spletno iskanje — najdražji zbiralec, teče pri vsaki trditvi
        ev["web"] = self._web_search_find(claim, claim_type, claim_context=claim_context)

        return ev

    @staticmethod
    def _collect_sources(ev: Dict) -> List[Dict]:
        """Sestavi seznam virov iz tega, kar so vrnili zbiralci.

        Mesta se polnijo izmenično, po eno od vsakega zbiralca na krog. Deset
        člankov iste založbe je manj različnih domen kot razpršen nabor, domene
        pa so tisto, kar koda prešteje.
        """
        cap = int(cfg("fact_checking.max_sources_per_claim", 10))

        # Each collector's own results, best first, in the order the collector
        # itself ranked them. Collectors that were routed out or found nothing
        # contribute an empty queue and simply never get a turn.
        queues: List[List[Dict]] = [
            [{"title": s.get("title", "Unknown"), "url": s.get("url", ""),
              "date": s.get("date", "Unknown"),
              "relevant_quote": s.get("relevant_quote", ""),
              "source_type": s.get("source_type", "other")}
             for s in ((ev.get("web") or {}).get("sources") or [])],

            [{"title": p.get("title", "Unknown"), "url": p.get("url", ""),
              "date": p.get("year", "Unknown"),
              "relevant_quote": p.get("abstract", ""), "source_type": "peer_reviewed",
              "citations": p.get("citations"), "journal": p.get("journal", "")}
             for p in (ev.get("papers") or [])],

            [{"title": f"Wikidata: {w.get('entity', 'Unknown')} ({w.get('entity_id', '')})",
              "url": w.get("url", ""), "date": "Unknown",
              "relevant_quote": w.get("fact_summary") or w.get("description", ""),
              "source_type": "structured_knowledge", "database": "wikidata"}
             for w in (ev.get("wikidata") or [])],

            [{"title": f"{fc.get('publisher', 'FactCheck')}: {fc.get('rating', '')}",
              "url": fc.get("url", ""), "date": fc.get("date", "Unknown"),
              "relevant_quote": fc.get("title", ""), "source_type": "fact_checker"}
             for fc in (ev.get("factchecks") or [])],

            [{"title": g.get("title", "Unknown"), "url": g.get("url", ""),
              "date": "Unknown", "relevant_quote": g.get("relevant_quote", ""),
              "source_type": "grok_web_search"}
             for g in ((ev.get("grok") or {}).get("web_sources") or [])],

            [{"title": FactChecker._extract_domain(u), "url": u,
              "date": "Unknown", "relevant_quote": "", "source_type": "perplexity"}
             for u in ((ev.get("perplexity") or {}).get("citations") or [])],
        ]

        sources: List[Dict] = []
        seen: set = set()
        cursors = [0] * len(queues)

        while len(sources) < cap:
            placed = False
            for qi, q in enumerate(queues):
                if len(sources) >= cap:
                    break
                # On its turn a collector advances to its next unseen page. A
                # page another collector already contributed does not cost it
                # its turn, it simply moves on to the next one it holds.
                while cursors[qi] < len(q):
                    entry = q[cursors[qi]]
                    cursors[qi] += 1
                    url = (entry.get("url") or "").strip()
                    if url and url not in seen:
                        seen.add(url)
                        sources.append(entry)
                        placed = True
                        break
            if not placed:
                break   # every collector is spent
        return sources

    # ── ENA RAZSODBA ────────────────────────────────────────────────────────

    def _judge_prompt(self, claim: str, claim_type: str, ev: Dict,
                      sources: List[Dict]) -> str:
        """Lay the gathered material out for the judge, in a fixed order."""
        blocks = []

        if ev.get("papers"):
            lines = []
            for i, p in enumerate(ev["papers"], 1):
                line = f"{i}. {p.get('title', '')} ({p.get('year', '')}) — {p.get('journal', '')}"
                if p.get("citations"):
                    line += f" [{p['citations']} citations]"
                if p.get("abstract"):
                    line += "\n   Abstract: " + p["abstract"][:700]
                lines.append(line)
            blocks.append("=== PEER-REVIEWED LITERATURE ===\n" + "\n".join(lines))

        if ev.get("wikidata"):
            lines = []
            for w in ev["wikidata"][:3]:
                fact = (w.get("fact_summary") or w.get("description") or "").strip()[:400]
                lines.append(f"- {w.get('entity', '')} ({w.get('entity_id', '')}): {fact}")
            blocks.append("=== STRUCTURED KNOWLEDGE (Wikidata) ===\n" + "\n".join(lines))

        if ev.get("factchecks"):
            lines = [f"- {fc.get('publisher', 'Unknown')} rated it: {fc.get('rating', 'N/A')}"
                     for fc in ev["factchecks"][:3]]
            blocks.append("=== EXISTING FACT-CHECKS ===\n" + "\n".join(lines))

        if (ev.get("web") or {}).get("findings"):
            blocks.append("=== WEB SEARCH FINDINGS ===\n" + ev["web"]["findings"])

        if (ev.get("perplexity") or {}).get("findings"):
            blocks.append("=== PERPLEXITY FINDINGS ===\n" + ev["perplexity"]["findings"][:2000])

        if (ev.get("grok") or {}).get("findings"):
            blocks.append("=== WEB AND X FINDINGS (Grok) ===\n" + ev["grok"]["findings"])

        if sources:
            lines = []
            for i, s in enumerate(sources, 1):
                quote = (s.get("relevant_quote") or "").strip().replace("\n", " ")[:220]
                lines.append(
                    f"[{i}] {s.get('title', 'Unknown')} ({s.get('source_type', 'other')}, "
                    f"{s.get('date', 'Unknown')}) — {s.get('url', '')}"
                    + (f"\n     \"{quote}\"" if quote else ""))
            blocks.append("=== NUMBERED SOURCE LIST ===\n" + "\n".join(lines))

        if ev.get("skipped"):
            lines = [f"- {k}: {v}" for k, v in ev["skipped"].items()]
            blocks.append("=== COLLECTORS THAT DID NOT RUN ===\n" + "\n".join(lines))

        material = "\n\n".join(blocks) if blocks else "(no material was retrieved)"
        stance = ev.get("claim_context") or ""
        stance_line = f"\nCLAIM CONTEXT: {stance}\n" if stance else ""

        return f"""You are a professional fact-checker. Every source below was retrieved by
this system. You have NO search of your own: judge the claim on this material and on
nothing else. If the material does not settle the claim, say UNVERIFIABLE.

CLAIM: "{claim}"
TYPE: {claim_type}{stance_line}
{material}

{VERDICT_RULES}

ALSO:
- Weigh the material by what it shows, not by how many collectors mention it. Several
  collectors reporting the same underlying source is still one source.
- Where the material contradicts itself, say so in the explanation rather than picking
  the more convenient side.
- Never invent a source or a URL. Refer to sources only by their number.

THEN GO THROUGH THE SOURCE LIST ONE BY ONE. For every number in it, say which of the
same five verdicts THAT SOURCE ON ITS OWN supports for the claim, judged only from the
title, date and quoted text you were shown for it:
- TRUE / PARTIALLY_TRUE / MISLEADING / FALSE — the source speaks to the claim and points
  that way
- UNVERIFIABLE — the source is about something else, or says too little to point either
  way. Use this whenever you are not sure; do not guess a direction.
Your own verdict need not match the majority. A single official statistic can outweigh
several pages that merely repeat each other, and you should say so in the explanation
when it does.

Return ONLY valid JSON:
{{
  "verdict": "TRUE|PARTIALLY_TRUE|MISLEADING|FALSE|UNVERIFIABLE",
  "explanation": "3-5 sentences with the specific figures and dates the material gives",
  "sources": [
    {{"n": 1, "verdict": "TRUE|PARTIALLY_TRUE|MISLEADING|FALSE|UNVERIFIABLE"}},
    {{"n": 2, "verdict": "..."}}
  ],
  "correction": "If FALSE or MISLEADING: the accurate information in one sentence. Otherwise an empty string"
}}
{t("llm.language_instruction")}"""

    def _judge_claim(self, claim: str, claim_type: str, ev: Dict,
                     sources: List[Dict]) -> Dict:
        """One call decides the verdict over all gathered material.

        This is the only place in fact-checking where a verdict is produced.
        The judge does not search, so its answer is a reading of a fixed,
        recorded evidence set rather than of whatever it happened to find.
        """
        from debate_analyzer import TruncatedJSONError, create_provider

        provider_name = cfg("fact_checking.judge_provider", "anthropic")
        model = cfg("fact_checking.judge_model", "claude-sonnet-5")
        prompt = self._judge_prompt(claim, claim_type, ev, sources)

        logger.info("      [Judge] %s/%s deciding over %d sources...",
                    provider_name, model, len(sources))

        # A cut-off answer is not a wrong answer, it is an unfinished one, so it
        # is worth asking again with room to finish. Every analysis pass already
        # does this; the judge used to call the provider directly and turned one
        # truncated reply straight into ERROR, which then dropped the claim out
        # of every count that follows.
        budget = int(cfg("fact_checking.judge_max_tokens", 4096))
        cap = int(cfg("fact_checking.judge_max_tokens_cap", 16384))
        attempts = max(1, int(cfg("fact_checking.judge_max_attempts", 3)))

        parsed = None
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                provider = create_provider(provider_name, model)
                parsed = provider.call(
                    system="You are a professional fact-checker. Return only valid JSON.",
                    user=prompt,
                    temperature=0.1,
                    max_tokens=budget,
                )
                break
            except TruncatedJSONError as e:
                last_exc = e
                if attempt >= attempts or budget >= cap:
                    break
                budget = min(budget * 2, cap)
                logger.warning("      [Judge] answer was cut off, retrying with %d tokens", budget)
            except Exception as e:
                last_exc = e
                break

        if parsed is None:
            logger.error("      [Judge] Failed: %s", last_exc)
            return {"verdict": "ERROR",
                    "explanation": f"Judgement failed: {last_exc}",
                    "judge_model": f"{provider_name}/{model}"}

        verdict = self._one_verdict(parsed.get("verdict"))

        # Številka mimo seznama se zavrže. Neomenjen vir ostane brez oznake in
        # se v seštevek ne šteje.
        per_source: Dict[int, str] = {}
        for item in (parsed.get("sources") or []):
            if not isinstance(item, dict):
                continue
            try:
                i = int(item.get("n"))
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(sources):
                per_source[i] = self._one_verdict(item.get("verdict"))

        marked = []
        for i, src in enumerate(sources, 1):
            entry = dict(src)
            if i in per_source:
                entry["source_verdict"] = per_source[i]
            marked.append(entry)

        return {
            "verdict": verdict,
            "explanation": parsed.get("explanation", ""),
            "correction": parsed.get("correction", ""),
            "sources": marked,
            "source_verdicts": self._count_source_verdicts(marked),
            "judge_model": f"{provider_name}/{model}",
        }

    @staticmethod
    def _one_verdict(value) -> str:
        """Normalise a verdict to one of the five, or to UNVERIFIABLE."""
        v = str(value or "").upper().replace(" ", "_").replace("-", "_")
        return v if v in ("TRUE", "PARTIALLY_TRUE", "MISLEADING",
                          "FALSE", "UNVERIFIABLE") else "UNVERIFIABLE"

    @staticmethod
    def _count_source_verdicts(sources: List[Dict]) -> Dict[str, int]:
        """Tally the labelled sources across the same five verdicts.

        This is a count of what the material says, not a vote that decides
        anything. The judge's own verdict is allowed to differ from the
        majority here, and the explanation is where it says why.
        """
        tally = {v: 0 for v in ("TRUE", "PARTIALLY_TRUE", "MISLEADING",
                                "FALSE", "UNVERIFIABLE")}
        for s in sources:
            v = s.get("source_verdict")
            if v in tally:
                tally[v] += 1
        return tally

    # ── PREVERJANJE ENE TRDITVE ─────────────────────────────────────────────

    def verify_claim(self, claim_data: Dict, _perplexity_prefetched: Optional[Dict] = None) -> Dict:
        """Collect the material, then judge it once.

        The two steps are deliberately separate. Collecting is mechanical and
        leaves a record of what was retrieved. Judging is one nominal decision
        over exactly that record, and nothing afterwards changes it.
        """
        claim = claim_data["exact_claim"]
        claim_type = claim_data.get("claim_type", "unknown")

        logger.info("   Verifying [%s]: %s...", claim_type, claim[:80])

        ev = self._gather_evidence(claim_data, _perplexity_prefetched=_perplexity_prefetched)
        sources = self._collect_sources(ev)
        result = self._judge_claim(claim, claim_type, ev, sources)

        # Ob napaki razsojanja ostane seznam virov tak, kot ga je sestavila koda.
        result.setdefault("sources", sources)
        result["search_method"] = "collect_then_judge"
        merged = {**claim_data, **result, "fact_checked": result["verdict"] != "ERROR"}
        return self._finalise_result(merged)

    # ── PARALLEL VERIFICATION ───────────────────────────────────────────────

    def verify_claims_parallel(self, claims: List[Dict]) -> List[Dict]:
        max_workers = cfg("fact_checking.parallel_workers", 5)
        total = len(claims)

        logger.info("   Checking %d claims with %d parallel workers...", total, max_workers)

        # Pre-fetch Perplexity results in batches of 5
        perplexity_prefetched = {}
        if self._perplexity_client:
            batch_size = cfg("fact_checking.perplexity_batch_size", 5)
            for i in range(0, total, batch_size):
                batch = claims[i:i + batch_size]
                try:
                    batch_results = self._perplexity_find_batch(batch)
                    perplexity_prefetched.update(batch_results)
                    logger.info("   [Perplexity batch %d-%d] Got %d results",
                                i + 1, min(i + batch_size, total), len(batch_results))
                except Exception as e:
                    logger.warning("   [Perplexity batch] Failed: %s", e)

        results = [None] * total
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for i, claim_data in enumerate(claims):
                future = executor.submit(self.verify_claim, claim_data,
                                         _perplexity_prefetched=perplexity_prefetched,
)
                futures[future] = i

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                    done = sum(1 for r in results if r is not None)
                    logger.info("   [%d/%d] done", done, total)
                except Exception as e:
                    logger.error("   [%d/%d] FAILED: %s", idx + 1, total, e)
                    results[idx] = {
                        **claims[idx], "verdict": "ERROR",
                        "explanation": f"Parallel verification failed: {e}",
                        "sources": [], "fact_checked": False,
                    }

        return [r for r in results if r is not None]

    # ── MAIN PIPELINE ───────────────────────────────────────────────────────

    def fact_check_arguments(self, speakers: Dict, transcript: str = "") -> Dict:
        """Preveri premise izluščenih argumentov.

        Vhod je seznam argumentov, zato vsaka razsodba nosi arg_id premise, iz
        katere izhaja. Premisa je modelova ubeseditev povedanega in ne dobesedni
        navedek, zato razsodba velja za premiso, kot je izpisana.
        """
        logger.info("[3] Fact-checking argument premises...")
        FactChecker._scholar_rate_limited = False
        claims = self.extract_claims_from_arguments(speakers)
        if not claims:
            logger.warning("   No checkable claims among the premises")
            return {"total_claims": 0, "fact_checks": [],
                    "summary": {"message": "No checkable claims"}}
        del transcript
        return self._verify_claim_set(claims)

    def extract_claims_from_arguments(self, speakers: Dict) -> List[Dict]:
        """Pick the premises that assert something checkable.

        One call over the argument list — a few thousand characters — instead of
        one over the whole transcript. Premises that are purely normative
        ("screens should be banned") are not claims and are dropped here.
        """
        blocks: List[str] = []
        known_ids: set = set()
        for speaker, data in (speakers or {}).items():
            if not isinstance(data, dict):
                continue
            for arg in (data.get("arguments") or []):
                if not isinstance(arg, dict):
                    continue
                aid = str(arg.get("arg_id") or "")
                if not aid:
                    continue
                known_ids.add(aid)
                lines = [f'[{aid}] speaker: {speaker}',
                         f'  position: {arg.get("argument", "")}']
                for i, prem in enumerate(arg.get("premises") or []):
                    text = prem if isinstance(prem, str) else prem.get("premise", "")
                    lines.append(f'  premise {i}: {text}')
                blocks.append("\n".join(lines))

        if not blocks:
            return []

        prompt = """You are given the arguments already extracted from a debate, each with an
arg_id and numbered premises. Find the premises that assert something CHECKABLE against
outside sources and return them as claims.

A premise is checkable when it states a fact about the world: a number, a date, a study, a
measurable trend, who said or did what. A premise is NOT checkable when it states what
ought to be done, what is right or fair, or how something feels — those are the positions
being debated, not facts.

One premise may carry SEVERAL checkable facts (several statistics in one sentence). Return
each as its own claim, quoting that part of the premise as written. Do not invent detail
that is not in the premise, and do not generalise it — carry over every number, date and
scope word exactly as the premise has them.

For each claim return:
- exact_claim: the checkable assertion, in the premise's own words
- arg_id: the arg_id it came from, EXACTLY as given
- premise_index: the number of the premise it came from
- speaker: the speaker of that argument
- claim_type: one of [statistic, historical, scientific, quote, policy, health, economic, geographic]
- context: one sentence on what the argument uses this fact for

Return ONLY valid JSON: {"claims": [...]}"""

        model = cfg("fact_checking.claim_extraction_model", "gpt-5.6-luna")
        payload = "\n\n".join(blocks)
        cache_key = f"argclaims:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("   Claim extraction from arguments: cache hit")
            return cached

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": prompt},
                          {"role": "user", "content": payload}],
                **sampling_kwargs(model, 0.0),
                response_format={"type": "json_object"},
            )
            claims = json.loads(response.choices[0].message.content).get("claims", [])
        except Exception as e:
            logger.error("   Claim extraction from arguments failed: %s", e)
            return []

        # Drop anything that points at an argument we do not have: a claim we
        # cannot attach is exactly what this rewrite set out to remove.
        clean: List[Dict] = []
        for c in claims:
            if not isinstance(c, dict) or not str(c.get("exact_claim") or "").strip():
                continue
            if str(c.get("arg_id") or "") not in known_ids:
                logger.info("   Dropping claim with unknown arg_id %r", c.get("arg_id"))
                continue
            clean.append(c)

        logger.info("   Found %d checkable claims across %d arguments",
                    len(clean), len(known_ids))
        self.cache.set(cache_key, clean)
        return clean

    def _verify_claim_set(self, claims: List[Dict]) -> Dict:
        """Razgradi sestavljene trditve in preveri vse.

        Zgornje meje števila trditev ni. Vsaka bi morala odločiti, katere
        odpadejo, seznam pa nastaja po govorcih, zato je rez po vrsti praznil
        drugega govorca prvega.
        """
        claims = self.decompose_claims(claims)

        parallel_workers = cfg("fact_checking.parallel_workers", 5)
        if parallel_workers > 1 and len(claims) > 1:
            fact_checks = self.verify_claims_parallel(claims)
        else:
            fact_checks = []
            for i, claim_data in enumerate(claims, 1):
                logger.info("   [%d/%d]", i, len(claims))
                fact_checks.append(self.verify_claim(claim_data))

        summary = self._generate_summary(fact_checks)

        logger.info("Fact-checking complete")
        logger.info("   Checked: %d | True: %d | False: %d | Misleading: %d",
                     len(fact_checks),
                     summary["verdict_breakdown"].get("TRUE", 0),
                     summary["verdict_breakdown"].get("FALSE", 0),
                     summary["verdict_breakdown"].get("MISLEADING", 0))

        return {
            # Vsaka trditev se preveri zase. Isto dejstvo v dveh argumentih se
            # preveri dvakrat in vsak govorec obdrži svojo.
            "total_claims": len(fact_checks),
            "fact_checks": fact_checks,
            "summary": summary,
            "mechanisms_used": [
                "real_time_web_search_via_openai",
                "pubmed_scientific_literature",
                "semantic_scholar_citation_analysis",
                "openalex_academic_search",
                *(["crossref_academic_search"] if cfg("fact_checking.engines.crossref", True) else []),
                *(["wikidata_structured_facts"] if cfg("fact_checking.engines.wikidata", True) else []),
                "google_factcheck_tools_api",
                *(["perplexity_sonar_pro"] if self._perplexity_client else []),
                *(["grok_web_x_search"] if self._grok_client else []),
                *(["claim_decomposition"] if cfg("fact_checking.claim_decomposition", True) else []),
                *(["engine_routing_by_claim_type"] if cfg("fact_checking.engine_routing", True) else []),
                "single_judgement_over_collected_evidence",
                "disk_cache",
            ],
        }

    def _generate_summary(self, fact_checks: List[Dict]) -> Dict:
        verdict_breakdown: Dict[str, int] = {}
        critical_issues = []

        for fc in fact_checks:
            verdict = fc.get("verdict", "UNKNOWN")
            verdict_breakdown[verdict] = verdict_breakdown.get(verdict, 0) + 1

            if verdict == "FALSE":
                critical_issues.append({
                    "speaker": fc.get("speaker"), "claim": fc.get("exact_claim"),
                    "correction": fc.get("correction", ""),
                })

        total = len(fact_checks)

        # Razsodbe se preštejejo po govorcih in skupno, v številko pa se ne
        # zlijejo. Delež točnosti je zahteval, da neresnični trditvi pripišemo
        # 0,0, delno resnični 0,6 in zavajajoči 0,25, torej uteži, ki jih iz
        # gradiva ni mogoče utemeljiti, imenovalec pa je izpustil nepreverljive
        # trditve, zato je ena sama odpoved razsodnika odstotek postavila na
        # peščico trditev in ga prikazala enako, kot bi stal na vseh.
        by_speaker: Dict[str, Dict[str, int]] = {}
        for fc in fact_checks:
            speaker = (fc.get("speaker") or "").strip()
            if not speaker:
                continue
            v = fc.get("verdict", "UNKNOWN")
            by_speaker.setdefault(speaker, {})
            by_speaker[speaker][v] = by_speaker[speaker].get(v, 0) + 1

        return {
            "verdict_breakdown": verdict_breakdown,
            "verdicts_by_speaker": by_speaker,
            "critical_false_claims": critical_issues,
            "total_checked": total,
        }
