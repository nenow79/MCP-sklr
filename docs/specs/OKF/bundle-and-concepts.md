<!-- OKF v0.2 source excerpt; navigation added by sklr. -->
[OKF 0.2: section map](SPEC.md). This is format text, not a statement of sklr runtime support. Section references (§) use the original numbering.

## 3. Bundle structure

A bundle is a directory tree of markdown files. The directory structure
is independent of the domain: producers organize concepts however makes
sense for the knowledge being captured.

```
path/to/bundle/
  index.md                      # Optional. Directory listing for progressive disclosure.
  log.md                        # Optional. Chronological history of updates.
  <concept>.md                  # A concept at the bundle root.
  <subdirectory>/               # Subdirectories organize concepts into groups.
    index.md
    <concept>.md
    <subdirectory>/
      ...
```

A bundle MAY be distributed as:

- A git repository (recommended, since it provides history, attribution,
  and diffs).
- A tarball or zip archive of the directory.
- A subdirectory within a larger repository.

### 3.1 Reserved filenames

The following filenames have defined meaning at any level of the
hierarchy and MUST NOT be used for concept documents:

| Filename   | Purpose                          |
|------------|----------------------------------|
| `index.md` | Directory listing. See §8.       |
| `log.md`   | Update history. See §9.          |

All other `.md` files are concept documents.

Tags remain a first-class concept through the `tags` frontmatter field
(§4.1). OKF does not specify a separate file format for aggregating
documents by tag; a consumer that wants a tag-browsing view can
synthesize one at consumption time by scanning frontmatter.

---

## 4. Concept documents

Every concept is a UTF-8 markdown file with two parts:

1. A **YAML frontmatter block**, delimited by `---` on its own line at the
   start of the file and a closing `---` on its own line.
2. A **markdown body**, containing free-form content.

### 4.1 Frontmatter

```yaml
---
type: <Type name>                  # REQUIRED
title: <Optional display name>
description: <Optional one-line summary>
resource: <Optional canonical URI for the underlying asset>
tags: [<tag>, <tag>, ...]          # Optional
# ... trust, lifecycle, provenance, and computation families (see §5, §10)
# ... other producer-defined key/value pairs
---
```

**Required:**

- `type`: A short string identifying the kind of concept. Consumers use it
  for routing, filtering, and presentation. Example values:
  `BigQuery Table`, `BigQuery Dataset`, `API Endpoint`, `Metric`,
  `Playbook`, `Reference`, `Attested Computation`.

  Type values are **not** registered centrally. Producers SHOULD pick
  values that are descriptive and self-explanatory; consumers MUST
  tolerate unknown types gracefully, typically by treating them as generic
  concepts.

`type` is the only always-required key; a concept carrying just `type` is
fully conformant (§11).

**Recommended:**

- `title`: Human-readable display name. If omitted, consumers MAY derive a
  title from the filename.
- `description`: A single sentence summarizing the concept. Used by
  `index.md` generators, search snippets, and previews.
- `resource`: A URI that uniquely identifies the underlying asset the
  concept describes. Absent for concepts that describe abstract ideas
  rather than physical resources.
- `tags`: A YAML list of short strings for cross-cutting categorization.

The optional **provenance**, **trust**, and **lifecycle** families (§5) and
the **computation** fields for Attested Computation concepts (§10) may also
appear.

**Extensions:** Producers MAY include any additional keys. Consumers
SHOULD preserve unknown keys when round-tripping and MUST NOT reject
documents with unrecognized fields.

### 4.2 Body

The body is standard markdown. Producers SHOULD favor structural markdown
(headings, lists, tables, fenced code blocks) over freeform prose, since
structure aids both human reading and agent retrieval.

There are no required body sections. The following headings have
**conventional** meaning and SHOULD be used when applicable:

| Heading         | Purpose                                                |
|-----------------|--------------------------------------------------------|
| `# Schema`      | Structured description of an asset's columns/fields.   |
| `# Examples`    | Concrete usage examples, often as fenced code blocks.  |
| `# Computation` | The sanctioned computation of an Attested Computation. See §10. |

Per-claim attribution to external sources uses markdown footnotes keyed to
`sources` entries rather than a body citations list (§5.1).

### 4.3 Example: a concept bound to a resource

```markdown
---
type: BigQuery Table
title: Customer Orders
description: One row per completed customer order across all channels.
resource: https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders
tags: [sales, orders, revenue]
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-05-28T14:30:00Z }
---

# Schema

| Column        | Type      | Description                              |
|---------------|-----------|------------------------------------------|
| `order_id`    | STRING    | Globally unique order identifier.        |
| `customer_id` | STRING    | Foreign key into [customers](/tables/customers.md). |
| `total_usd`   | NUMERIC   | Order total in US dollars.               |
| `placed_at`   | TIMESTAMP | When the customer submitted the order.   |

# Joins

Joined with [customers](/tables/customers.md) on `customer_id`.
```

### 4.4 Example: a concept not bound to a resource

```markdown
---
type: Playbook
title: "Incident response: data freshness alert"
description: Steps to triage a freshness alert on the orders pipeline.
tags: [oncall, incident]
generated: { by: human:ahormati, at: 2026-04-12T09:00:00Z }
---

# Trigger

A freshness alert fires when `orders` lags more than 30 minutes behind its
expected SLA. See the [orders table](/tables/orders.md).

# Steps

1. Check the [ingestion job dashboard](https://example.com/dash).
2. ...
```

---
