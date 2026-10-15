<!-- OKF v0.2 source excerpt; navigation added by sklr. -->
[OKF 0.2: section map](SPEC.md). This is format text, not a statement of sklr runtime support. Section references (§) use the original numbering.

## 11. Conformance

A bundle is **conformant** with OKF v0.2 if:

1. Every non-reserved `.md` file in the tree contains a parseable YAML
   frontmatter block.
2. Every frontmatter block contains a non-empty `type` field.
3. Every reserved filename (`index.md`, `log.md`) follows the structure in
   §8 and §9 respectively when present.

When the trust, lifecycle, provenance, or computation families are
present, producers SHOULD follow §5 through §10, and consumers:

- MUST treat a bare `verified` mapping as a one-element list (§5.2).
- MUST NOT reject a concept for missing any optional family (§5.3).
- SHOULD derive trust tiers and staleness only from the fields specified
  here, and SHOULD surface, not silently drop, a failing attestation
  (§10.5).

Consumers SHOULD treat all other constraints as soft guidance. In
particular, consumers MUST NOT reject a bundle because of:

- Missing optional frontmatter fields.
- Unknown `type` values.
- Unknown additional frontmatter keys.
- Broken cross-links.
- Missing `index.md` files.

---

## 12. Versioning

This document specifies OKF version **0.2**. Revisions are versioned as
`<major>.<minor>`:

- A **minor** version bump introduces backward-compatible additions (new
  optional fields, new conventional section headings).
- A **major** version bump may make breaking changes (renaming required
  fields, changing reserved filenames).

Bundles MAY declare the version they target with `okf_version: "0.2"` in a
bundle-root `index.md` frontmatter block (the only place frontmatter is
permitted in an `index.md`). Consumers that do not understand the declared
version SHOULD attempt best-effort consumption rather than refusing the
bundle.

### Considered and deferred

The following are intentionally left to a future revision:

- The full runtime protocol: receipt and verdict wire formats, and the
  attestation lifecycle around a run.
- The attester ABI, portability, and sandboxing, likely bundled with
  future work on serving and Skills.
- Attestation caching.
- Semantic-layer templates (Looker, dbt) where the attester comparison
  shifts from SQL equality to model-and-binding equality.

---

## 13. Changes from v0.1

v0.2 supersedes OKF v0.1 and is a minor version bump under §12, except for
two deliberate breaking changes called out below because they rename or
retire v0.1 fields. A v0.1 bundle is consumable by a v0.2 consumer under
the fallbacks noted here.

### 13.1 Breaking changes

- **`timestamp` is superseded by `generated.at`.** A concept's last
  content change is now recorded as `generated: { by, at }` (§5.2).
  Consumers MAY fall back to a legacy `timestamp` when `generated` is
  absent.
- **The body `# Citations` list is superseded by `sources`.** Provenance
  moves to frontmatter (§5.1). Consumers SHOULD read `sources` and MAY
  still parse a legacy `# Citations` body list for v0.1 documents.

### 13.2 Additive changes

All of the following are additive: new optional keys, one new concept
type, and one new conventional heading. Their absence yields a plain v0.1
concept.

- New frontmatter families: `sources` with its per-source credibility
  signals (`author`, `usage_count`, `last_modified`) and the `usage_window`
  sibling; `generated`, `verified`; `status`, `stale_after` (§5).
- New concept type `Attested Computation` and its computation keys
  `runtime`, `parameters`, `computation`, `executor`, `attester` (§10).
- New conventional body heading `# Computation` (§4.2).
- The actor convention for `generated.by` and `verified[].by` (§7).

Everything else (bundle structure, reserved filenames, the required
`type`, recommended `title`/`description`/`resource`/`tags`, cross-linking,
index files, log files, permissive conformance) is carried forward
unchanged.

---
