# Radio / TV catalog search performance — 22 October 2026

Graph MCP tools were unavailable. Source inspection found repeated catalog
normalization, quadratic duplicate checks in Radio suggestions, and a TV text
cache that did not detect changes to existing channel objects.

## Changes

Radio uses an object-keyed weak cache for normalized station title and combined
search text. Title, subtitle, category and frequency changes invalidate that
station's cached fields. Both local full search and suggestions share the same
matching helper. A Set replaces nested duplicate-ID scans, and stable prefix /
non-prefix partitions replace sorting. Suggestions retain only the first eight
rows in each partition and stop when eight prefix matches are known. Full local
search stays uncapped and preserves prefix-first ordering.

TV's text cache compares name, group and language with their cached source
values. Metadata edits therefore become searchable without replacing the whole
catalog or reloading the player. Full search still returns every ranked match.
Suggestion requests use a separate bounded path retaining eight unique URLs;
duplicate URL aliases compete by score, with original catalog order breaking
ties. Empty-query picks use one stable Set-based pass through recent, favorite
and verified channels.

Late remote Radio results also refresh the open suggestion list using the
latest request ID for that query. The search revision guard still prevents
an older search from replacing the current catalog.

No result cache hides availability changes. Visibility is checked on every TV
search, so failure filtering, recovery and forced retry remain current. Weak
cache entries do not retain discarded catalog objects. Saved preferences,
request IDs, playback order and source ownership are unchanged.

## Evidence and limits

Runtime regressions compare Radio suggestions with the former ranking over
1,202 rows, verify a repeated 2,000-station search normalizes only its query,
and check mutated metadata. TV regressions compare bounded suggestions against
full stable ranking across seven queries and 3,001 rows, including duplicate
aliases and verified channels. Full search still returns more than 1,000
matches in that fixture. Hosted documents and standalone exports are regenerated
and checked for reproducibility.

These are deterministic algorithmic and functional checks, not a claim about
physical-device FPS or broadcaster response times. Remote search providers,
stream startup and native mobile playback still depend on external systems.

## Verified release

The final production TypeScript/Vite build and all 107 checks across 13
frontend suites passed. All 23 Chrome browser flows passed; the three
suggestion/protocol browser flows were repeated after the remote-update
fix and passed. Export regeneration remained reproducible.
