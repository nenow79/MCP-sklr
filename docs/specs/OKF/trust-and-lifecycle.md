<!-- OKF v0.2 source excerpt; navigation added by sklr. -->
[OKF 0.2: section map](SPEC.md). This is format text, not a statement of sklr runtime support. Section references (§) use the original numbering.

## 5. Provenance, trust, and lifecycle

These frontmatter families make "where did this come from," "how much
should I trust it," and "is it still current" answerable from frontmatter.
All are optional. Their absence carries meaning: an unverified concept is
distinguishable from a verified one, but is never rejected (§11).

Every timestamp-valued key in OKF is an ISO 8601 datetime with an explicit
UTC offset, for example `2026-06-30T14:00:00Z`.

### 5.1 Provenance: `sources`

`sources` records the materials a concept derives from, external or
internal to the bundle.

```yaml
sources:
  - id: ga4-schema
    resource: https://developers.google.com/analytics/bigquery/export-schema
    title: GA4 BigQuery Export schema
    author: team:ga4-docs
    usage_count: 5000
    last_modified: 2026-05-30T00:00:00Z
usage_window: { from: 2026-06-01T00:00:00Z, to: 2026-06-30T00:00:00Z }
```

Each `sources` entry:

- `resource`: REQUIRED within an entry. Names either a concrete artifact a
  consumer can follow (an absolute URL, a bundle-relative path, or a path
  into a `references/` subdirectory, §6) or a population or scope descriptor
  it cannot (for example `all queries in BigQuery project X`).
- `id`: Optional. A stable key used to attribute individual claims (see
  below). SHOULD be present when the body cites the source.
- `title`: Optional. Human-readable label for the source.
- The optional credibility signals `author`, `usage_count`, and
  `last_modified`, described next.

**Source credibility signals.** OKF records objective, per-source signals
so a consumer can judge how much to trust a concept by judging the sources
it was extracted from. It does not store a credibility score: a score is
subjective, unportable across consumers, and goes stale. Credibility is
*inferred* from the signals, the same way trust tiers are (§5.3), not
stored. Each signal is optional and lives on a `sources` entry:

- `author`: Who or what produced the source, in the actor convention (§7).
  An authority signal.
- `usage_count`: How often `resource` was exercised (dashboard views, query
  executions, page reads) over `usage_window`. An adoption and liveness
  signal. For a single artifact it is that artifact's own exercise count;
  for a scope descriptor it is the number of exercises within the scope that
  touch the concept.
- `last_modified`: When the source itself last changed. A recency signal,
  distinct from `generated.at` (§5.2), which records when the concept was
  written.
- `usage_window`: Written once as a sibling of `sources`, it frames every
  `usage_count` with a `{ from, to }` datetime range. A single entry MAY
  carry its own `usage_window` to override the shared one.

`usage_count` is a coarse signal. It is comparable at the
alive-versus-dead and order-of-magnitude level, and against a source's own
history over time, but not as a precise cross-kind ranking: a scheduled
query's executions and a human's deliberate dashboard views do not carry
equal weight. Consumers SHOULD read it as liveness and trend, not as a
score.

Lineage is expressed through links, not a dedicated field. When a
`resource` points at another OKF concept, the derivation edge already
exists in the bundle graph (§6), so a consumer MAY recurse into that
source's own `sources` and let credibility propagate. External leaf sources
carry only their intrinsic signals. Deeper lineage (an explicit external
`derived_from`, or data lineage) is out of scope for v0.2.

**Per-claim attribution.** To attribute a specific claim, use a markdown
footnote whose label is a `sources[].id`:

```markdown
The `events_` table is sharded daily as `events_YYYYMMDD`.[^ga4-schema]

[^ga4-schema]: GA4 BigQuery Export schema
```

The footnote label is the join key into `sources`; consumers resolve
attribution through the matching entry, not by parsing the footnote prose.
Labels are keyed rather than positional (`sources[0]`) because agents
constantly rewrite these documents: a positional index misattributes
silently the moment the list is reordered, whereas a stable `id` survives
reordering.

### 5.2 Trust: `generated` and `verified`

`generated` records how the current content was produced. `verified`
records who or what has confirmed the content against its sources or
`resource`. They are kept distinct because who *wrote* a concept need not
be who *confirmed* it.

```yaml
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
```

- `generated.by`: REQUIRED within `generated`. An actor (§7).
- `generated.at`: An ISO 8601 datetime marking the content's last
  meaningful change. Consumers use it to tell a recent edit from a stale
  fact.

```yaml
verified:
  - { by: human:ahormati, at: 2026-06-25T09:00:00Z }
  - { by: process:finance-nightly, at: 2026-06-26T02:00:00Z }
```

- `verified`: A list of verification events, each with `by` (an actor) and
  `at` (an ISO 8601 datetime). Multiple entries capture independent
  checks, for example a human sign-off plus a nightly process. "How
  recently" is the latest `at`.
- `verified` is independent of `generated.at`: content can change without
  re-confirmation, and facts can be re-confirmed without regeneration.
- A single verifier MAY be written as one `{ by, at }` mapping without the
  list dash. Consumers MUST treat a bare mapping as a one-element list:

```yaml
verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }
```

### 5.3 Trust tiers

Consumers derive a trust tier from `verified`, lowest to highest:

- No `verified` key ⇒ **unverified**.
- `verified` by non-`human:` actors only ⇒ **machine-confirmed**.
- `verified` by a `human:<id>` actor ⇒ **human-reviewed**.

A concept with no trust frontmatter is still consumable; consumers MUST
NOT reject it (§11). Trust tiers are advisory signals, not access control.

### 5.4 Lifecycle: `status`

```yaml
status: stable        # draft | stable | deprecated
```

- `draft`: not yet reviewed; possibly incomplete.
- `stable`: default; ready for consumption.
- `deprecated`: kept for links and history; no longer current.

Absent `status` ⇒ `stable`.

### 5.5 Lifecycle: `stale_after`

```yaml
stale_after: 2026-09-23T00:00:00Z   # content is stale on/after this instant
```

Optional. An absolute instant. A concept is stale when
`now >= stale_after`. An absolute instant, not a relative TTL, keeps the
staleness decision a plain comparison with no reference to when the
concept was read.

---
