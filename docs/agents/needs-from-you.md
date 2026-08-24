# Needs from you

Leftovers for the human accumulate in one **inbox**: the open issue labeled `needs-from-you` (`gh issue list --label needs-from-you --state open`). Its body is the human's one-stop list; its comments are the append-only journal. A leftover with a defensible default never blocks a ticket — **default-and-log**: pick the default, ship, log the leftover here. Reserve blocking (`needs input:`, ticket left open) for leftovers where any default risks junk work: irreversible actions, ambiguous spec, credentials.

## When closing a ticket with leftovers

1. Sort each leftover: a **task** (a step the human performs) or a **decision** (a choice that shapes future work).
2. Decision → a fable-model subagent (Agent tool, `model: fable`) gets the decision, its options, and repo context, and returns a bird's-eye verdict. Batch every pending decision into that one spawn at close-out — one verdict each, one subagent total. **Await the verdict before posting anything that cites it** — a spawned-then-orphaned advisor whose result never arrived has had its verdict asserted anyway once; a claim without the result in hand is fabrication. Exception: a decision settled empirically against the reference implementation (probe output in hand) is a fact, not a judgement call — log it as FYI directly. For each judgement call the verdict says:
   - needs the human → the subagent writes a `grilling` ticket (shape below); the leftover becomes the checklist item `- [ ] Grill: <topic> → #<n>`.
   - does not → apply its call; the leftover becomes the FYI line `decided: <call> — <rationale>`.
3. Append one comment to the inbox with the new items, **including the verdict subagent's reasoning verbatim** for every applied call — the human audits the judge, not just the judgement. The verbatim reasoning lives in this journal comment only; the fold carries just the one-line `decided:` form. Word every item as a standalone actionable step — the reader acts on it without opening the source ticket. End the comment with ticket + PR links.
4. **Append-only.** The journal comment is the whole per-ticket obligation; the inbox *body* belongs to the dispatcher's run-end fold below. (A leg that read and re-folded the body paid 10–50k tokens for it, once per leg, on a body that had grown to 44KB.)
5. End the ticket's own closing comment with the same `## Needs from you` section.

Complete when the inbox comment is posted and the ticket's closing comment carries the section.

## Fold — dispatcher, run end (or any session on request)

Once per dispatch run, after the last leg: rebuild the inbox body from the journal comments — every still-unticked checklist item under `## Actions` at the top, one-line `decided:` forms under `## FYI — decided without you` below (never the verbatim reasoning — that stays in the journal), ticked items pruned. The fold is idempotent, so it also heals any item a parallel writer's body edit dropped. The human sees every open action without scrolling.

## Grilling tickets

Label `grilling`. The body is self-contained — the decision, the options, the subagent's bird's-eye writeup — so the grilling session needs zero archaeology, and ends with the contract line:

> Run `/grilling` on this ticket; end the session with `/to-tickets` so the output is one or more `ready-for-agent` implementation tickets.

## Handling items in a session

An inbox item taken care of during any session gets its checkbox ticked in the body right then — a one-character edit, not a fold; the next fold prunes it. Prune deletes from the body only — the journal comment stays as history.

## Scope

The inbox is the human's surface. Work reaches agents through tickets — a starting agent's context is its ticket, not the inbox.
