<!-- OKF v0.2 source excerpt; navigation added by sklr. -->
[OKF 0.2: section map](SPEC.md). This is format text, not a statement of sklr runtime support. Section references (§) use the original numbering.

## 10. Attested computations concept

An Attested Computation concept carries not just what a value *means* but a
sanctioned way to *compute* it, so a consumer can confirm the agent ran the
blessed computation instead of improvising its own. Provenance (§5.1)
answers "where did this claim come from"; attestation answers "was this
number produced the way we said it must be." OKF records the computation
and the means to check it; it does not execute anything itself.

### 10.1 A computation is its own concept

A sanctioned computation is a standalone concept of
`type: Attested Computation`. A concept that needs the value (a `Metric`, a
`BigQuery Table`) links to it with a normal markdown link (§6). Three
properties motivate the standalone concept:

- **`runtime` defines what `parameters` mean.** A parameter is a SQL bind
  variable, a dbt var, or a Python argument depending on the runtime.
  Keeping `runtime` and `parameters` in one frontmatter makes the binding
  semantics self-evident.
- **One computation, many consumers.** The same computation can back a
  metric, a dashboard concept, and a report; as a concept it is referenced
  once and reused.
- **Trust state is per computation.** `verified`, `stale_after`, and a
  single `attester` describe one thing. Revenue, profit, and margin each
  verify and attest independently, which is three concepts, not three
  entries in one frontmatter.

### 10.2 Contract fields

The contract is the concept's top-level frontmatter. In addition to the
provenance, trust, and lifecycle families (§5), an Attested Computation
concept carries:

- `runtime`: REQUIRED for this type. The single field that says how to run
  the computation, and so how the executor and attester interpret it and
  what `parameters` mean. Example values: `bigquery`, `postgres`, `dbt`,
  `python`, `Looker`.
- `parameters`: A list of the typed, named holes the agent may fill. Each
  entry: `{ name, type, required }`. Binding semantics follow `runtime`.
- `computation`: Optional. A path (§6.2) to a file holding the
  computation, used instead of an inline body fence (see §10.3). Absent ⇒
  the body `# Computation` fence is the computation.
- `executor`: How the computation is run. `resource` names run
  instructions or code; a runner (an agent, or deterministic consumer
  code) follows it. `receipt` declares the fields a run must return, the
  evidence the attester inspects (for example a BigQuery `job_id` and the
  SQL the job actually executed).
- `attester`: The deterministic check. `resource` names code (no LLM) that
  takes a receipt and returns a verdict. It is meant to run consumer-side.

What sits behind a `resource` (a Skill, a script, a container) is a
packaging choice; OKF fixes the interface, not the packaging (§1).

```markdown
---
type: Attested Computation
title: Revenue for fiscal year
description: Recognized revenue for a fiscal year, per Finance's definition.
status: stable
runtime: bigquery
parameters:
  - { name: year, type: integer, required: true }
executor:
  resource: references/skills/run-on-bq.md
  receipt: [job_id, executed_sql, result]
attester:
  resource: references/attesters/revenue.py
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }
stale_after: 2026-09-23T00:00:00Z
sources:
  - id: rev-policy
    resource: https://wiki.acme/finance/revenue-recognition
    title: Revenue recognition policy
---

# Computation

    SELECT SUM(amount) AS revenue
    FROM finance.recognized_revenue
    WHERE fiscal_year = @year

The computation binds only the declared `parameters`, per the recognition
policy.[^rev-policy]

[^rev-policy]: Revenue recognition policy
```

### 10.3 The computation

Provide the computation in one of two ways:

- **Inline:** a single fenced code block in the body under `# Computation`.
  Best for a short computation reviewed alongside the contract.
- **File:** set `computation` to a path (§6.2) and omit the body fence.
  Best for a long or generated computation, or one already kept as a real
  file shared with non-OKF tooling.

```yaml
runtime: bigquery
computation: references/computations/lib/revenue.sql
parameters:
  - { name: year, type: integer, required: true }
```

The agent MAY only supply *values* for the declared `parameters`; it MUST
NOT author or edit the computation. Binding `computation` with the
parameter values into the executable artifact is the consumer's job, and
the attester independently re-derives that same binding to compare against
what actually ran. Because the comparison is on the expanded, compiled
artifact the receipt carries (`executed_sql`, `compiled_sql`), a rewritten
query, a swapped computation file, or a mutated dependency fails the check.
A typed, parameter-only surface is what makes "did the sanctioned thing
run" a mechanical comparison rather than a judgement call.

### 10.4 Concepts that use a computation

A document is rarely a single computation. An income-statement overview
that discusses revenue, profit, and margin stays one readable concept and
links to one Attested Computation per figure:

```markdown
---
type: Metric
title: Revenue
description: Recognized revenue for a fiscal year.
tags: [finance, revenue]
status: stable
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
---

# Definition

Recognized revenue sums `amount` over rows booked to the fiscal year,
computed by [the revenue computation](../computations/revenue.md).
```

Because each computation is its own concept, revenue can be fresh while
profit is past its `stale_after`, and each attests on its own run.
Co-locating them is a directory choice (a `computations/` folder with an
`index.md`), not a frontmatter one.

### 10.5 How a consumer uses it (informative)

This subsection is informative, not normative. The runtime artifacts below
are **not** stored in the bundle.

1. **Discover** via `type: Attested Computation`, a frontmatter signal
   liftable into `index.md`; a consumer reaches one directly or by
   following a link from a concept that uses it.
2. **Load** the contract from frontmatter and the computation from the
   body (or the file named by `computation`).
3. **Parameterize**: the agent supplies values for the declared parameters.
4. **Execute**: the executor runs the bound computation and returns a
   receipt shaped by `executor.receipt`.
5. **Attest**: the consumer runs the attester over the receipt. It
   confirms provenance (the computation that ran equals `computation` bound
   with the claimed parameters, not agent-authored SQL) and fidelity (the
   displayed value matches the receipt's authoritative source, re-read by
   job id rather than taken from the agent's text).
6. **Gate**: refuse to display a failing attestation; warn or refuse when
   `now >= stale_after`. On success, surface the verdict (for example a
   link to the job log) so trust is visible.

### 10.6 Verification versus attestation

`verified` (§5.2) and attestation are distinct, and both exist:

- `verified` confirms the *definition* still matches policy. It is
  doc-level, slow, and recorded in the bundle.
- Attestation confirms a single *run* produced the value the sanctioned
  way. It is per-call, runtime, and not stored in the bundle.

A concept with a stale definition can still attest cleanly, and a
freshly-verified definition still requires attestation on each run, which
is why both are needed.

---
