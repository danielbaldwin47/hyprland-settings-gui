# Tickets

Read by the agent about to write a ticket — a `/to-tickets` session, a follow-up filed at ticket close, a Release check — before the first `gh issue create`.

A ticket is admitted when one implementing session can land it at about 120k context: one package or subsystem, its tests included, with its acceptance evidence named (which gate tier, or which hand test) and every blocking edge naming the artifact it waits for.

## Sizing

Budget one implementing session ≈ 120k context per ticket: one package or subsystem, its tests included. Heavy UI-visual verification splits into its own ticket. Every ticket of one audited 7-ticket run overshot this 1.4–3.5x — err small.

## Edges

Every blocking edge names the artifact it waits for ("needs class X from #N's diff") — topic adjacency is not an edge: one wrong edge cost a leg 173 idle minutes, and one audited edge ran backwards. Edges are GitHub issue dependencies (`docs/agents/issue-tracker.md` §Wayfinding operations).

## Labels

Agent-filed tickets get `needs-triage`, never `ready-for-agent` (`docs/agents/triage-labels.md`). A ticket's Reading line names the doc sections the implementer fetches, so the ticket is read once.
