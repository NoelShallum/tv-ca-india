# eGazette form map (verified 2026-09-25, Asia/Kolkata)

## TLS (verified, do not disable)
- Host: `https://egazette.gov.in` (`egazette.nic.in` does not resolve).
- Server sends leaf-only chain: `CN=egazette.gov.in`, issuer `Let's Encrypt YR2`.
- `openssl verify -CAfile yr-chain-bundle.pem -untrusted int-yr2.pem live-0.pem` => **OK**.
- Python `requests.get("https://egazette.gov.in/", verify="egazette/certs/yr-chain-bundle.pem")` => **200, ~66KB**, lands on `(S(...))/default.aspx`.
- Bundle: `egazette/certs/yr-chain-bundle.pem` = `int-yr2.pem` (Let's Encrypt YR2, issued by Root YR) + `root-yr.pem` (ISRG Root YR, self-signed). Fetched from `https://letsencrypt.org/certs/gen-y/`.
- All eGazette clients must pass this bundle as `verify=`; never use `verify=False`.

## Homepage (`GET /` -> `(S(...))/default.aspx`)
- Form: `<form method="post" action="./default.aspx" id="Form1">`.
- Hidden: `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__VIEWSTATEENCRYPTED`, `__EVENTVALIDATION`, `hidden1`.
- Search box: `ddlkeyword` (17 options: Select Keyword, Acts, Appointment, Award, Bharat Ratna, Bills, ...), `Img_Search` image button.
- Nav via `__doPostBack`: `sgzt` (Search), `lnk_Extra_All`, `lnk_Week_All`, `lnk_Login`.
- Direct hrefs: `SearchMenu.aspx`, `RecentUploads.aspx?Category=1..5`, `GazetteDirectory.aspx`, `StateGazette.aspx`.

## Search menu (`SearchMenu.aspx`, title "eGazette Search")
- Buttons (each POSTs to its search page): `btneSearch` (by TEXT), `btnGazetteID` (by Gazette ID), `btnContentID` (by Content ID), `btnMinistry` (by Ministry), `btnCategory` (by Gazette Category), `btnBill` (by Bill/Assent/Act), `btnNotification` (by Notification Date), `btnPublish` (by Publish Date).
- `SearchGazette.aspx` via direct GET => 500 Runtime Error; must be reached via button POST, not direct GET.

## Recent uploads (`RecentUploads.aspx?Category=1`, Bills & Acts)
- Title "eGazette-Recent Uploads", `Total No. of Gazettes : 100` for Category=1.
- Grid `gvGazetteList`, 20 rows/page, per-row `gvGazetteList$ctlNN$imgbtndownload`.
- Columns: S.No., Ministry/Organization, Department, Office, Subject, Category, Part & Section, Issue Date, Publish Date, Gazette ID, Download.
- Pager: `javascript:__doPostBack('gvGazetteList','Page$2'..'Page$5')` (quotes HTML-encoded as `&#39;`).
- Requires fresh session per chain (cookieless `(S(...))` URL); reuse `response.url` base via `urljoin`.

## Gazette directory (`GazetteDirectory.aspx`, title "eGazette Published Gazette")
- Filters: `ddlCategory` (Select Category, Extra Ordinary, Weekly), `ddlPartSection` (dependent), `ddlYear` (1926-2026), image submit `btnSubmit` (needs `btnSubmit.x`/`btnSubmit.y`, NOT `btnSubmit=Submit`), plus `__LASTFOCUS`, `__SCROLLPOSITIONX/Y`.
- Flow: select Category => POST with `__EVENTTARGET=ddlCategory` => `ddlPartSection` populates (30 options each) => POST submit with `btnSubmit.x=10&btnSubmit.y=10`.
- Extra Ordinary Part/Section values: CSL=31, No Part No Section=38, Part I=61, I-Sec1=1, I-Sec2=2, I-Sec3=3, I-Sec4=4, Part II=43, II-Sec1=62 and 5, II-Sec1-A=48, II-Sec1-A Hindi=6, II-Sec2=7 and 63, II-Sec3=37, II-Sec3A=45, II-Sec3-Sub(i)=8, (ii)=9, (iii)=10, II-Sec4=11, Part III=64, III-Sec1=12, III-Sec2=13, III-Sec3=14, III-Sec4=15, Part IV=16 and 65, Part V=41, V-Sec2=66.
- Weekly Part/Section values: CSL=30, No Part No Section=39, Part I=67, I-Sec1=17, I-Sec2=18, I-Sec3=19, I-Sec4=20, Part II=42, II-A=34, II-Sec1=68 and 36, II-Sec2=69 and 32, II-Sec3=33, II-Sec3A=44, II-Sec3-Sub(i)=21, (ii)=22, (iii)=23, II-Sec4=24, Part III=35 and 70, III-Sec1=25, III-Sec2=26, III-Sec3=27, III-Sec4=28, Part IV=29 and 71, Part V=40, V-Sec2=72.
- Verified example: Extra Ordinary + PartSection=9 (II-Sec3-Sub-Sec ii) + 2025 => `No. of Gazettes found : 5917`, 76KB page, rows with Gazette ID like `CG-DL-E-31122025-268976` + size (0.9 MB).
- Partition ledger key: `category + part_section value + year` (e.g. `Extra Ordinary/9/2025`).

## Polite rules (unchanged)
- Single worker, random 0.8-1.8s delay (user asked 0.5-0.9s range; keep polite), session repair on failure, resume-safe ledger; HTTP 200 alone never marks a partition complete; resolve each row's viewer and validate distinct bytes (never bulk the first-row preview).

## Bill/Assent/Act search (`SearchBill.aspx`, via `btnBill` POST, never direct GET)
- URL pattern `SearchBill.aspx?id=<session-token>` (token varies; reach via SearchMenu `btnBill` POST).
- Fields: `ddlreftype` (Select Reference Type, 8=Act, 9=Bill, 15=Assent), `txtRefNo` (Reference Number, optional), `txtKeyword` (textarea, Keywords, optional), `txtDateFrom`/`txtDateTo` (Notification issue range, optional, keypress-blocked date pickers), image buttons `ImgSubmitDetails`/`ImgResetDetails`, `btnBack`.
- This is the act-specific gazette path (Strategy 3 amendments): query by Act reference no + keyword + date window.

## Download resolution (pilot-verified 2026-09-25, single file)
- Row download buttons do NOT return the PDF directly: POSTing `gvGazetteList$ctlNN$imgbtndownload.x/.y` returns the list page plus `window.open('ViewPDF.aspx','_blank')`.
- Same-session `GET ViewPDF.aspx` returns viewer HTML with `<iframe id="framePDFDisplay" src="../WriteReadData/2026/276481.pdf">`.
- Same-session `GET` of the iframe src returns `application/pdf` (pilot: 408,361 bytes, `%PDF` magic, sha256 `8d989295...`, Ministry of Petroleum and Natural Gas row).
- Rules: resolve each row through its own POST -> ViewPDF -> iframe chain; never construct `WriteReadData/...` URLs from Gazette IDs; never reuse row 1's file for other rows; one pilot file only until partitioned enumeration with ledger.

## Act search via client.search_bill() (verified 2026-09-25)
- `search_bill(keyword, reftype=8, ...)` replays menu -> btnBill -> ImgSubmitDetails.
- Pilot: keyword "Commercial Courts" -> Total No. of Gazettes: 2, both Ministry of Law and Justice / Legislative Department, Part II-Section 1: 21-Aug-2018 amendment record + 01-Jan-2016 principal record, same gvGazetteList viewer chain for downloads.
- `txtRefNo` takes a bare Act number (verified 2026-09-25: "30" returns Act-30 across years, 32 records, 3 pages; "31 of 2018" style fails). Walk pages via `__EVENTTARGET=<grid_id>`, `__EVENTARGUMENT=Page$N` (`enumerate.fetch_page`, verified on gvGazetteList).
- `txtDateFrom`/`txtDateTo` are ignored server-side for SearchBill keyword queries (verified 2026-09-25: identical result sets across disjoint ranges).
- Gap: Acts 30 and 48 of 2023 absent from Act search by number; same-title or assent-index Filing TBD (NOT a URL-construction license; resolve via directory partitions or alternate titles).
- Negative results (verified 2026-09-25): SearchBill Act index has no record for Act 48 of 2023 under number/title/assent queries — some recent assents need the GazetteDirectory path instead. Reftype 15 (Assent) + keyword returns empty grid for GST queries.
- Open (2026-09-25): GazetteDirectory.aspx intermittently times out and once rendered without the ddlCategory form — server-side flakiness, not yet distinguished from session-state cause. Directory probes for the 48-of-2023 assent deferred to a healthy window; retry machinery now in client. Do NOT widen concurrency over this.
- Update: direct GazetteDirectory.aspx GET in a fresh session returns HTTP 200 titled "Runtime Error" (3490 bytes, no form). Menu href from home is a plain link, so entry likely needs a warmed in-site navigation, or the app pool is intermittently broken (same flow built the 2025 partition hours earlier). Bill stream (reftype 9) also lacks the 48-of-2023 record (only the 2018 amendment bill).
