<!-- OKF v0.2 source excerpt; navigation added by sklr. -->
[OKF 0.2: section map](SPEC.md). This is format text, not a statement of sklr runtime support. Section references (§) use the original numbering.

## 6. Cross-linking and paths

### 6.1 Links between concepts

Concepts MAY link to other concepts using standard markdown links. Two
forms are supported:

- **Absolute (bundle-relative):** begins with `/`, interpreted relative to
  the bundle root. This is the **recommended** form because it is stable
  when documents are moved within their subdirectory.

  ```markdown
  See the [customers table](/tables/customers.md) for the join key.
  ```

- **Relative:** a standard markdown relative path.

  ```markdown
  See the [neighboring concept](./other.md).
  ```

A link from concept A to concept B asserts a *relationship*. The specific
kind (parent/child, references, joins-with, depends-on) is conveyed by the
surrounding prose, not by the link itself. Consumers that build a graph
view typically treat all links as directed edges of an untyped
relationship.

Consumers MUST tolerate broken links: a link whose target does not exist
in the bundle is not malformed; it may simply represent not-yet-written
knowledge.

### 6.2 Path-valued fields

Several fields name a path or URI: `resource`, `sources[].resource`,
`computation`, `executor.resource`, and `attester.resource` (§10). A
`sources[].resource` may instead be a scope descriptor (§5.1), in which
case it is not a path. Each path-valued field accepts:

- an absolute URL (for example `https://...`),
- a bundle-relative path beginning with `/`, or
- a relative path (for example `../computations/revenue.md`).

### 6.3 The `references/` convention

A `references/` subdirectory conventionally mirrors external material, run
instructions, or code as first-class concepts within the bundle. Sources,
executors, and attesters commonly point into it (for example
`references/attesters/revenue.py`). It is a naming convention, not a
requirement.

---

## 7. Actor convention

Fields that record an identity (`generated.by`, `verified[].by`) use a
single actor convention:

- `<producer>/<version>` for agents and tools, for example
  `reference_agent/gemini-2.5-pro`.
- `human:<id>` for a person, for example `human:ahormati`.
- `process:<id>` for an automated process, for example
  `process:finance-nightly`.

Consumers that classify trust (§5.3) key off the `human:` prefix, so
producers MUST use it for hand-authored or human-confirmed content.

---

## 8. Index files

An `index.md` file MAY appear in any directory, including the bundle root.
It enumerates the directory's contents to support **progressive
disclosure**: letting a human or agent see what is available before
opening individual documents.

Index files contain no frontmatter, with one exception: a bundle-root
`index.md` MAY carry an `okf_version` key (§12). The body uses one or more
sections, each grouping concepts under a heading:

```markdown
# Section / Group Heading

* [Title 1](relative-url-1) - short description of item 1
* [Title 2](relative-url-2) - short description of item 2

# Another Section

* [Subdirectory](subdir/) - short description of the subdirectory
```

Entries SHOULD include the description from the linked concept's
frontmatter. Producers MAY generate `index.md` automatically; consumers
MAY synthesize one on the fly when none is present.

---

## 9. Log files

A `log.md` file MAY appear at any level of the hierarchy to record the
history of changes to that scope. The format is a flat list of
date-grouped entries, newest first:

```markdown
# Directory Update Log

## 2026-05-22
* **Update**: Added a BigQuery table reference for [Customer Metrics](/tables/customer-metrics.md).
* **Creation**: Established the [Dataplex Playbook](/playbooks/dataplex.md).

## 2026-05-15
* **Initialization**: Created foundational directory structure.
```

Date headings MUST use ISO 8601 `YYYY-MM-DD` form. Log entries are prose;
the leading bold word (`**Update**`, `**Creation**`, `**Deprecation**`) is
a convention, not a requirement.

---
