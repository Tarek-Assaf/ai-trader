# Research source adapters and untrusted content

T-041 provides an offline-testable source layer for search, URL fetch, RSS/Atom, FRED, economic-calendar JSON and COT JSON. The adapters use the already pinned `httpx`, SQLite and SQLAlchemy packages; tests never call a remote provider.

## Safety and data handling

- Each configured endpoint must use HTTPS, an explicit hostname allow-list, a public DNS result, and port 443. User-supplied fetch URLs must match that allow-list. Redirects are rejected.
- Requests disable ambient proxy configuration, have a timeout and a one-megabyte default response limit. Only transient network errors, HTTP 429 and HTTP 5xx are retried. A malformed response is not retried as a network error.
- Rate reservations are stored atomically in the SQLite cache. If cache/rate-limit storage fails, the source does not send a request.
- Provider credentials are passed at construction from environment-backed settings. They are not written to cache/provenance or included in safe source errors. Query credential parameters are redacted in provenance URLs.
- HTML is converted to text; scripts, styles, templates, controls and Unicode format characters are removed. The remaining content is length-capped and escaped inside an `untrusted_content` wrapper. This labels data; it does not assert factual correctness or authorize tools.
- Every normalized item has a stable ID, fetched-at UTC time, sanitized content SHA-256, source and redacted URL. It is written to the existing `research_items` table and cached for the configured TTL. Repeating an unchanged query is idempotent.
- Only claims that cite at least one ID present in the stored provenance set are returned by `supported_claims`. The later T-043 full-graph integration must keep fetched text out of tool permissions and verify injection fixtures end to end.

## Adapter configuration

Construct adapters with an explicit endpoint host allow-list, a `SQLiteResearchCache`, and a `ResearchRepository` backed by the application's SQLAlchemy engine. Set per-source `ttl_seconds` and `min_interval_seconds`. Search, calendar and COT providers remain configurable because the project has not selected vendors or response schemas. Calendar/COT adapters validate their configured top-level JSON arrays and expose only the documented scalar fields.

FRED uses the official observations endpoint and requires `FRED_API_KEY` from runtime configuration; it is sent only to the HTTPS endpoint. Web search uses a configured HTTPS endpoint and bearer credential, such as `SEARCH_API_KEY`. Never put key values in this file, a URL, the handoff, or logs.

No source adapter places broker orders, imports broker or agent tooling, or executes content returned by a provider.
