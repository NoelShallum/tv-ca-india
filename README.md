# Time-Versioned Central Acts of India

This repository contains reproducible data products for building a
time-versioned corpus of Central Acts of India from the India Code
repository. The first published artifact is an auditable inventory of the
Central-Act records discovered on 10 September 2026.

## Current snapshot

| Artifact | Value |
|---|---|
| File | [`data/central_acts_inventory.csv`](data/central_acts_inventory.csv) |
| Rows | 849 data rows (850 physical CSV lines including the header) |
| Distinct India Code UUIDs | 849 |
| `ACT` collection records | 847 |
| `REPEAL_ACTS` collection records | 2 |
| File size | 464,019 bytes |
| SHA-256 | `4527A35E44299793BF0162509692D31F80337D963BC26729A2B37081CD1BDBD9` |
| Source run | `20260910T114258Z-all-3e437db3` |
| Retrieval date | 2026-09-10 (UTC) |

The rows are ordered by discovery rank. India Code is a live, evolving
service; this CSV is therefore a dated snapshot, not a promise that the
current site will return the same list in the future.

## What this CSV is—and is not

The CSV is the inventory stage of the harvester: a catalog of records and
stable URLs that can be used to start document acquisition. It contains
identifiers, titles, Act metadata, collection membership, and links to the
item, public Act page, sections, and schedules.

It is not the completed legal corpus. The inventory alone does not contain
the PDFs, section text, footnote HTML, amendments, commencement orders,
notifications, rules, or Gazette evidence. Those are acquired by the
follow-on crawl and stored in the crawler's SQLite/blob run directory. The
50-Act pilot used for parser validation is a separate run and is not mixed
into this file.

## Data dictionary

### Identity and ordering

| Column | Meaning |
|---|---|
| `rank` | Deterministic discovery order within the inventory run. |
| `uuid` | India Code/DSpace UUID for the record; unique in this snapshot. |
| `collection_filter` | Collection used to discover the record: `ACT` or `REPEAL_ACTS`. |
| `handle` | India Code handle when supplied by the API. |

### Expected and observed Act fields

The `expected_*` fields are populated when a frozen pilot manifest supplies an
expected value. They are blank for this live all-corpus inventory. The
`observed_*` fields come from the India Code API record.

| Column | Meaning |
|---|---|
| `canonical_id` | Canonical identifier supplied by a pilot manifest, when applicable. |
| `expected_title` | Manifest title, when applicable. |
| `expected_year` | Manifest enactment year, when applicable. |
| `expected_number` | Manifest Act number, when applicable. |
| `title` | Title observed in the India Code record. |
| `observed_act_id` | India Code's Act identifier, for example `AC_CEN_...`. |
| `observed_act_year` | Year parsed/returned by India Code. |
| `observed_act_number` | Act number parsed/returned by India Code. |
| `collection` | API collection value for the record. |
| `state_name` | Jurisdiction/state value; the inventory query requested `CENTRAL`. |
| `no_of_section` | Number of sections when India Code supplies it. |
| `last_modified` | Last-modified value returned by India Code, when present. |

### Reproducible URLs and provenance

| Column | Meaning |
|---|---|
| `api_url` | HAL API item URL used for metadata acquisition. |
| `public_url` | Public India Code Act page. |
| `sections_url` | Public sections view for the Act. |
| `schedules_url` | Public schedules view for the Act. |
| `inventory_source_blob_sha256` | SHA-256 of the raw inventory-page response that discovered the row. |
| `raw_blob_sha256` | SHA-256 of a raw item snapshot, when the item has been fetched. |

Blank values mean that India Code did not provide the field at the inventory
stage; they are not inferred values.

## How the snapshot was produced

The API-first harvester queried India Code's DSpace API using the exact
Central-Act filter:

```text
query=dc.identifier.state_name:CENTRAL
f.identifier_collection=ACT,equals
scope=69a0c1fb-7b22-4481-b16a-1dc59b5d02e6
```

The `REPEAL_ACTS` collection was queried separately because the India Code
service exposes those records without the principal-Act collection scope.
Results were paginated, the raw JSON response for every page was retained,
and each UUID was de-duplicated before being written to the CSV. The source
run also persisted the exact request URL, response hash, retrieval time,
retry/error information, and configuration in its run manifest.

The public API origin used for this snapshot was:

```text
https://indiacode.gov.in/server/api
```

Public records are linked under:

```text
https://indiacode.gov.in/act/<uuid>
```

## From inventory to time-versioned Acts

The intended processing sequence is:

1. Use this inventory as the stable queue of principal Act records.
2. Fetch each API item and preserve all metadata and HAL links.
3. Traverse sections and schedules, retaining section ordering and identifiers.
4. Parse HTML footnote fields losslessly, including markers, effective-date text,
   linked IDs, raw HTML, normalized text, and source offsets.
5. Reconcile the HTML layer with the current India Code PDF. Store the PDF,
   extracted text/coordinates, and OCR evidence only when OCR is required.
6. Traverse related records for amendments, commencement instruments,
   adaptations, repeals, rules, notifications, orders, and other linked
   documents. Record their relationship and classification rather than
   assuming every related record changes Act text.
7. Resolve promising instruments against the eGazette. A printed operative
   Gazette instrument—not a metadata label or an inferred footnote—is required
   before promoting an event to a legal time-version.

Effective/commencement dates and notification/publication dates must remain
separate fields. A current consolidated PDF is not sufficient to reconstruct
every historical version; original Acts, adaptation orders, territorial
applications, subordinate legislation, and repeal context may also be needed.

## Reproducibility and resumability

The companion scraper stores a run in a durable SQLite database and
content-addressed blob directory. Targets have explicit states (`PENDING`,
`IN_PROGRESS`, `RETRY`, `DONE`, or `FAILED`) and durable retry timestamps.
An interrupted run can be resumed without redownloading completed blobs:

```powershell
python -m indiacode_scraper resume `
  --run-id 20260910T114258Z-all-3e437db3 `
  --data-root data
```

For a new inventory, the live command is consent-gated and records the
permission manifest in the run metadata. Production runs should retain TLS
verification, use a polite per-host delay, and keep the India Code Terms of
Use/permission reference with the run.

## Files intentionally omitted

Standalone thumbnails and image bitstreams are not included in this data
release. The crawler records their existence and skip reason, but the pilot
and inventory runs use the requested no-thumbnail/no-image policy.

## Source and legal-use notes

India Code is the discovery, inventory, consolidation, and structural source
for this project. It is not by itself the authority for deciding whether a
historical amendment or commencement event is legally operative. For that
purpose, retain and verify the relevant printed Gazette instrument and its
operative wording.

This repository's software is released under the accompanying MIT `LICENSE`.
The underlying government texts, metadata, and linked documents may have
separate government-source terms or copyright conditions. Users are
responsible for complying with the India Code Terms of Use and any applicable
law when retrieving or redistributing source documents.

## Validation

The companion scraper's offline regression suite covers API pagination,
resumable queues, HTML footnote parsing, citation offsets, section capture,
blob integrity, and the image-skip policy. The inventory snapshot itself has
849 unique UUIDs and a SHA-256 recorded above so that a downstream consumer
can verify an unchanged copy.
