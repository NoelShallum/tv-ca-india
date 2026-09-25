# Quarantine — problem URLs, retried later

Regenerated 2026-09-25 15:53. AUTO section is machine-written; Curated is hand-maintained.

<!-- AUTO -->
## India Code request failures (status != 200)
none — all logged requests returned HTTP 200.

## eGazette open partitions
- Extra_Ordinary/9/2025 | OPEN | pages=3 records=45 downloads=9 expected=5917 | no error | 2026-09-25 05:52
<!-- /AUTO -->

## Curated (hand-maintained — edit freely, keep the retry condition)

- eGazette SearchBill Act queries returning the 20806-byte unrendered shell.
  Problem: host outage, no grid rendered. Retry when: `recover fetch --dry-run` returns records.
- eGazette GazetteDirectory.aspx rendering the formless shell / Runtime Error.
  Problem: directory route unavailable since ~11:45 2026-09-25. Retry when: directory GET renders ddlCategory.
- Act 48 of 2023 assent gazette (CGST Second Amendment, s.110, assent 28-12-2023).
  Problem: absent from every SearchBill index path tried. Retry via: directory Extra-Ordinary Part-II-Sec-1 Dec-2023 partition once healthy.
- Notification pulls (NOTIFICATION_QUEUE.md, 97 S.O./G.S.R. refs).
  Problem: need directory partitions or content-ID search; both blocked on the same outage. Retry when: directory healthy.
