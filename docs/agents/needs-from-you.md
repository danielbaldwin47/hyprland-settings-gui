# Needs from you

The owner sees decisions in two places, and a leftover goes to exactly one:

- **Inside an effort**, an owner call (what only the owner can decide, from an implementer's open call, a review or the hand-test) goes in the effort PR's ready comment (`docs/agents/implement-spec.md` step 8), quoted or marked settled with its commit. A call with a defensible default is built to that default and reported there; the effort never waits on it.
- **Outside an effort**, or a call the owner defers past the PR's merge, goes in the **inbox**: the open issue labelled `needs-from-you` (`gh issue list --label needs-from-you --state open`). Append one comment per item, worded as a standalone step the owner can act on without opening the source, ending with the issue or PR it came from. A call that needs a design session becomes a `grilling` ticket, and its inbox line is `- [ ] Grill: <topic> → #<n>`.

The inbox body is the owner's one-screen list: any session asked to tidy it rebuilds the body from the comments, open items at the top, ticked ones pruned. Comments stay as history.

Work reaches agents through tickets, not the inbox: a starting agent's context is its ticket.
