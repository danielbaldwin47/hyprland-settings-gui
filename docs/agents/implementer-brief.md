# Implementer brief

You build one ticket of a spec. Your prompt names the ticket, your worktree and branch, the spec branch, the **notes directory** and your **scratch directory**. The orchestrator merges your branch; you push nothing and merge nothing into the spec branch.

## Read first

1. The ticket: `gh issue view <n> --json title,body,comments`. Its acceptance criteria are the contract, and its "Blocked by" tickets are in your base.
2. Your orientation note in the notes directory (`t<n>.md`), and `common.md` beside it. Its line numbers are as of the commit it opens with: `git log --oneline <that commit>..HEAD -- <the paths under Touch>` lists what has changed them since. Read the ranges it names before anything wider: whole-file surveys of this repo have cost sessions 90k+ context, and the note is the map.
3. The spec sections the ticket cites, from `spec.md` in the notes directory. Open it with Read and an offset.
4. `CONTEXT.md` (every term comes from it), the ADR each note cites, and the repo's `CLAUDE.md` if it is not in your context.

## Build

1. Confirm your base: `git merge-base --is-ancestor <spec branch> HEAD`. If it fails, `git reset --hard <spec branch>`.
2. Call the Skill tool with `principles` and read, in full, the leaves that bear on this ticket. Then call it with `mattpocock-skills:tdd` and build test-first. A docs-only ticket takes `mattpocock-skills:writing-for-agents` in place of `tdd`.
3. Work inside your worktree, by absolute path, with the main checkout's tools (`docs/agents/local-checks.md` § Worktrees). Read source with Read and change it with Edit or Write. `ruff format` is the formatter; run it on the files you touched, never the whole tree.
4. Scratch files go in your scratch directory, by absolute path.
5. Change what the ticket names. A regression your diff causes on a surface the ticket leaves alone is yours to fix before you report.
6. Where the ticket conflicts with the spec or an ADR, or leaves a choice open, take the reading closest to the spec's rule, record it in one comment on the ticket, and name it in your report. An open call (`docs/agents/tickets.md`) is built to its recommendation; where the recommendation cannot work, report that and build nothing in its place. A change that settles or reverses an ADR updates that ADR in the same commit.
7. A **found** defect is one in code already merged, by another ticket or on `main`. Fix it when the fix is under ten lines, as its own commit naming the ticket that introduced it. Fixed or not, it goes under **Found** in your report.
8. Commit in small verifiable units. Each message names the ticket (`… (#<n>)`) and ends with `Co-Authored-By: Claude <your model> <noreply@anthropic.com>`.

## Done

1. `git merge <spec branch>`: its tip may have moved. Settle a conflict in your favour only where your ticket owns the lines.
2. The done checks (`local-checks.md` § Done checks) pass in your worktree. pytest queues behind every other run on the machine: a `pytest: waiting for` line is your place in that queue, so let the run go on.
3. Run the real artifact. A UI-facing ticket (a page, dialog or widget the user sees) gets a widget probe of the state it changes, and a cropped screenshot (`widget_probe.shoot`) where the change is visual. Both run only through `.venv/bin/python tools/widget_probe.py <probe.py>`, whose first line is `import widget_probe` (`local-checks.md` § Widget probes); green UI-tier tests are assembly proof, not appearance proof. An Engine ticket proves itself in its unit and static tiers, plus the Harness tier where it changes what reaches the compositor.
4. Report in under 25 lines:
   - the branch and its head commit;
   - each acceptance criterion with how you verified it (the test's name, or what the probe read);
   - the done checks with their counts;
   - **Found**, and **Left open** for what you saw and could not settle;
   - each principle whose leaf you read that changed a choice, with the choice and the alternative it rejected. A choice the ticket or the note had already made is not a principle's; "none" is a full answer.
