# Where amendment instruments actually live (verified 2026-09-25, ~15 API probes)

Question: can amendments be sourced from India Code alone, pausing eGazette?

Findings (all via `https://indiacode.gov.in/server/api`, polite single-worker):
- Principal ACT collection scope (`69a0c1fb-...`, + `f.identifier_collection=ACT,equals`):
  scoped `dc.identifier.act_number:N` works; hits carry full metadata (number, year, title,
  collection, state) for client-side matching. CGST 2017 found as 12 of 2017 (method validated).
- That scope contains NO annual Finance Acts and NO GST amendment acts
  (checked numbers 12, 13, 30, 31 + title searches; inventory independently has 0 Finance Acts).
- Seven "Act Amendments" collections exist but are EMPTY (0 items each).
- Preamble-phrase full-text search for the Finance Act finds nothing.
- What India Code DOES host and our crawl already captures: principal acts, rules,
  notifications, circulars, orders (181 amendment-*Rules* items and counting).

Conclusion: India Code-first holds for everything it hosts. But amendment *Acts*
(Finance Acts, CGST/IGST amendment acts, 48 of 2023) are not in its repository, so the
eGazette-recovered PDFs fill a genuine gap. Recommendation: eGazette stays as the
amendment-Act source (resume when the host renders pages again); everything else stays
India Code. Standing by for user call.
