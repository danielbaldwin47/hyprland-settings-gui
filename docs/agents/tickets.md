# Specs and tickets: what `/to-spec` and `/to-tickets` add here

Both skills read this file before they draft. It adds four things to them: the **decided literals** a spec carries, the **size** of a ticket, the **Model** line it carries, and the **critic** that reads a draft before it goes to GitHub.

## A spec

- A spec is an issue labelled `spec` (`docs/agents/issue-tracker.md` § Specs). Its tickets each carry `Part of #<spec>` and their blocking edges (`issue-tracker.md` § Wayfinding operations, Blocking).
- **Decided literals.** Every text or number a user will read that an earlier decision settled (an ADR, a grilling answer, a prototype the owner accepted) goes into the spec as a table: where and when it shows, what it says, and the issue or ADR that decided it, copied from there.
- **Open calls.** A text, number or case that no decision covers is listed under "Open calls" for the owner, with a recommendation. The build follows the recommendation and the PR reports it.
- **A promise of no change is checked against every other line.** Where the spec says a surface stays as it is, each change the spec asks for either carries that condition or is listed as an open call.

## A ticket

- **Size: one implementing session, about 120k context**: one package or subsystem, its tests included. Heavy UI-visual verification splits into its own ticket. Every ticket of one audited 7-ticket run overshot this 1.4–3.5x; cut small.
- **Blocking edges name the artifact** they wait for ("needs `move_bind` from #N's diff"). Topic adjacency is not an edge: one wrong edge cost a session 173 idle minutes, and one audited edge ran backwards.
- **Acceptance criteria quote the spec.** A text or number in a criterion appears in the spec's decided literals or in a named fixture. A criterion that needs a value the spec lacks says so as an open call.
- **Live needs.** A ticket whose proof needs a running compositor says so and names the nested route (`docs/agents/local-checks.md` § Running the app, § Harness tier). One that needs real monitors or input devices says that too, so the effort PR can hand it to the owner.
- **Model line.** Each ticket ends with `Model: Sonnet` or `Model: Opus` and a clause of reason. The `/to-tickets` quiz shows it beside each title, so the owner approves or moves it with the granularity. Opus builds a ticket when any of these holds, and Sonnet builds the rest:
  - it blocks two or more tickets, so its shape is their contract;
  - its body runs past about 3,500 characters;
  - it changes the Apply pipeline, the Writer's output, or the Manifest or Journal on disk, where a defect damages a user's config;
  - it changes a module that pages outside the ticket share (`session.py`, `ui/rows/`, `ui/shell/`).

  `/implement-spec` gives each implementer its ticket's model (`docs/agents/implement-spec.md` § Subagents).

## The critic, before publishing

One fresh read-only subagent reads the draft from a file, with the sources it was written from: the ADRs and grilling answers it cites, `CONTEXT.md`, and the fixtures it names. Its report lists:

- for a spec: each decision in the sources that the draft lost or changed, each pair of its lines that contradict each other, and each number with no source;
- for tickets: each acceptance criterion that contradicts a rule of the spec or a sibling ticket, each text or number found in neither the spec nor a fixture, each requirement of the spec that no ticket carries, and each blocking edge that names no artifact.

The author fixes each finding or lists it under "Open calls" in the issue. Publish when every finding is one or the other.
