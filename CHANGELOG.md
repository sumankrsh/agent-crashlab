# Changelog

## 0.1.0 (2026-11-18)

First release.

- A sandbox accounts-payable backend with three read tools and two write tools, honouring idempotency keys, that records every side effect.
- Six fault types: `timeout`, `lost_reply`, `error`, `duplicate`, `partial` and `crash`, drawn per call from a seeded plan, with four built-in profiles and per-fault overrides.
- Crash and resume: a crash kills the agent after the backend applies an effect, and the run restarts with the agent's durable memory.
- Four invariants judged from the recorded effects: `finished`, `complete`, `no_duplicates`, `no_unexpected`, with the likely cause of each violation.
- `agent-crashlab run`: many seeded runs per agent, a reliability score with a 95% Wilson interval, failure reasons, and JSON, Markdown and HTML reports; `--min-score` for CI.
- `agent-crashlab replay`: one seed, call by call.
- Three example agents (naive, retrying, careful) and two synthetic scenarios.
