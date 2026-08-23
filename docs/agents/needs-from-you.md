# Needs from you

Leftovers for the human accumulate in one **inbox**: the open issue labeled `needs-from-you` (`gh issue list --label needs-from-you --state open`). Its body is the human's one-stop list; its comments are the append-only journal. A leftover with a defensible default never blocks a ticket — **default-and-log**: pick the default, ship, log the leftover here. Reserve blocking (`needs input:`, ticket left open) for leftovers where any default risks junk work: irreversible actions, ambiguous spec, credentials.

## When closing a ticket with leftovers

1. Sort each leftover: a **task** (a step the human performs) or a **decision** (a choice that shapes future work).
2. Decision → spawn a fable-model subagent (Agent tool, `model: fable`) with the decision, its options, and repo context, asked for a bird's-eye verdict:
   - needs the human → the subagent writes a `grilling` ticket (shape below); the leftover becomes the checklist item `- [ ] Grill: <topic> → #<n>`.
   - does not → apply its call; the leftover becomes the FYI line `decided: <call> — <rationale>`.
3. Append one comment to the inbox with the new items. Word every item as a standalone actionable step — the reader acts on it without opening the source ticket. End the comment with ticket + PR links.
4. **Fold** — rebuild the inbox body from the journal: every still-unticked checklist item under `## Actions`, FYI lines under `## FYI — decided without you`, ticked items pruned. The fold is idempotent, so it also heals any item a parallel writer's body edit dropped.
5. End the ticket's own closing comment with the same `## Needs from you` section.

Complete when the inbox comment is posted, the fold is done, and the ticket's closing comment carries the section.

## Grilling tickets

Label `grilling`. The body is self-contained — the decision, the options, the subagent's bird's-eye writeup — so the grilling session needs zero archaeology, and ends with the contract line:

> Run `/grilling` on this ticket; end the session with `/to-tickets` so the output is one or more `ready-for-agent` implementation tickets.

## Handling items in a session

An inbox item taken care of during any session gets ticked in the body right then; the next fold prunes it. Prune deletes from the body only — the journal comment stays as history.

## Scope

The inbox is the human's surface. Work reaches agents through tickets — a starting agent's context is its ticket, not the inbox.
