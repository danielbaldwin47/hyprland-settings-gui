# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Applying `ready-for-human` also files an inbox item ("Implement yourself: <title> → #<n>") per `docs/agents/needs-from-you.md` — the label alone leaves the ticket invisible to the human, who reads the inbox, not label queries.

**Agent-filed tickets get `needs-triage`, never `ready-for-agent`.** Promotion to agent-ready is the human's act — it is the approval the self-landing grant rests on, and an agent applying it to its own follow-up launders that approval away (one run shipped seven self-approved tickets this way).

Edit the right-hand column to match whatever vocabulary you actually use.
