# Implementing specs: one effort, one branch, one PR

Overrides for the `implement-spec` skill in this repo; where a line here and the skill differ, this line holds. The session that runs `/implement-spec` is the **orchestrator**, and its subagents are **orienters**, **implementers**, **reviewers** and a **hand-tester**.

An **effort** is the set of specs the owner reviews and merges together: one branch `effort/<slug>` off `main`, one draft PR to `main`. Specs merge into the effort branch. With one spec in the effort, its spec branch is the effort branch: it comes off `main` and carries the PR.

An effort handed to `/implement-spec` as an issue labelled `effort` is read from that issue. Its specs are its sub-issues, in order: `gh api repos/{owner}/{repo}/issues/<effort>/sub_issues --jq '.[].number'`, each spec's tickets the same way from the spec. Blocking edges are the native ones (`gh api repos/{owner}/{repo}/issues/<n>/dependencies/blocked_by`). Specs run strictly in that order: each spec branch is cut from the effort branch after the spec before it has merged in (step 5), so a ticket blocked by a ticket of an earlier spec finds it in its base. Issues close only at the PR's merge, so issue state never shows a blocker done: a blocker counts as satisfied once its ticket branch has merged into the spec branch (step 3), or its spec into the effort branch (step 5). The first spec in, the one that opens the draft PR, is the first sub-issue. The PR's `Closes` lines include the effort issue itself.

## Branches

| Branch | Off | Only writer | Merged into |
| --- | --- | --- | --- |
| `effort/<slug>` | `main` | the orchestrator | `main`, by the owner |
| `spec/<n>-<slug>` | the effort branch | the orchestrator | the effort branch |
| `ticket/<n>` | the spec branch | its implementer | the spec branch |
| `fix/<n>-<slug>` (one per review) | the branch under review | its implementer | that branch |

- `main` reaches a spec branch only through the orchestrator merging the effort branch into it (step 5). Shared branches are merged, never rebased.
- The orchestrator merges each ticket branch itself, `git merge` in the spec branch's checkout, and settles an implementer's hedges ("rename if review objects") at that merge. A merge that conflicts goes to a **merger** subagent, which resolves it in the spec branch's checkout and returns the resolution in under ten lines (§ The orchestrator stays thin).
- The done checks (`docs/agents/local-checks.md` § Done checks) run on the receiving branch after every merge: a semantic conflict survives a clean textual merge. A red merge is undone (`git reset --hard ORIG_HEAD`) before anything else starts, and its ticket goes back to an implementer with the failing lines.
- A ticket worktree goes under `.claude/worktrees/` (gitignored), made by the orchestrator off the spec branch by absolute path. It needs no setup: the tools are the main checkout's `.venv/bin/<tool>` run from the worktree root, and `pythonpath = ["src"]` makes them load the worktree's code (`local-checks.md` § Worktrees).

## Subagents

A model the owner names when starting `/implement-spec` overrides this table.

| Role | Model | Its brief |
| --- | --- | --- |
| Orchestrator | the session's own model (Fable for effort #148) | this file, § The orchestrator stays thin |
| Orienter | Sonnet | step 2 |
| Implementer | its ticket's `Model:` line (`docs/agents/tickets.md`); Opus for a ticket with none | `docs/agents/implementer-brief.md` |
| Fix implementer | Opus | the findings list in place of a ticket, with the brief's Build and Done steps |
| Reviewer | Sonnet, coordinating; its **Standards** sub-agent on Opus and its **Spec** sub-agent on Fable | `/mattpocock-skills:code-review`; it passes `model: opus` and `model: fable` when it spawns the two axes |
| Merger | Opus | the two branches, the conflict list and both implementers' reports |
| Hand-tester | Opus | step 7 |

The **notes directory** is `~/.cache/effort-loop/hyprtweaker/spec-<n>/notes/`, outside the repo so every subagent reads the same files: the spec's body as `spec.md`, the orientation notes, and `carry-to-review.md`. Each implementer has a scratch directory of its own under `~/.cache/effort-loop/hyprtweaker/spec-<n>/scratch/`.

An implementer's prompt is its ticket, worktree, branch, the spec branch, the notes directory, its scratch directory and the brief's path. Add only what none of those hold.

## The orchestrator stays thin

The orchestrator lives for the whole effort, so whatever it reads it carries to the end. Call the Skill tool with `principles` and read the guard-the-context-window leaf before step 1. Its context holds pointers and states; files and GitHub hold the content.

- **It reads no content it can delegate.** Orienters read tickets and code, implementers read code, reviewers read diffs, and a merger reads conflicts. The orchestrator reads the effort issue, the spec list and each subagent's capped report. It opens no source file, ticket body or diff of its own.
- **State lives on disk.** `~/.cache/effort-loop/hyprtweaker/effort-<slug>/state.md` holds one line per ticket: number, spec, branch, status (`oriented`, `building`, `merged`, `returned`) and head commit. The orchestrator updates it after every event, and reads it, together with the native `blocked_by` edges, to find the frontier. It never recalls the frontier from earlier turns.
- **Reports go to files, not to working memory.** Each **Found** and **Left open** item is appended to `carry-to-review.md` as it arrives. Review findings go to the fix implementer as a file path.
- **Commands run quiet.** Done checks report counts (`ruff check . -q`, `mypy` last line, `pytest -q -n auto ... | tail -1`). On red, the failing test names go to the implementer; the orchestrator does not diagnose. `git merge` runs with `--no-edit -q`, and a conflict goes to the merger.
- **Waits are notifications.** A background subagent notifies when it finishes, and an external wait (CI) gets one Monitor. There is no polling and no "are you done?" message.


## Per spec

1. Branch `spec/<n>-<slug>` from the effort branch and push it. Write the spec's body to `spec.md` in the notes directory.
2. Orient every ticket, then build each frontier.
   - **Orient.** Orienters are `general-purpose` subagents (an `Explore` one cannot write files), four or five tickets each, started together before the first implementer. Each writes one note per ticket into the notes directory, named for the ticket (`t139.md`). The orienter that holds the first frontier also writes `common.md`: what every ticket needs once, such as the fixtures and conftest fixtures the tickets share, the modules they meet in, and the traps in the done checks. An orienter returns the paths it wrote and each conflict it found between a ticket and the spec or an ADR, and the orchestrator adds those conflicts to `carry-to-review.md`. The first frontier's implementers start when its notes exist; the other notes are ready before their tickets unblock.
   - **A note** opens with the commit it was read at and has five parts: **Touch** (`path:start-end`, one line on what changes there), **Read first** (the ADR and `docs/research/` section, with lines), **Tests** (files, fixtures, the command that runs only them), **Seams** (where the ticket decides an interface) and **Traps** (what looks wrong but is decided, and where the ticket's criteria and the spec disagree). It points at code and leaves the design to the implementer. A note for a UI-facing ticket names the widget properties a probe would read to prove it.
   - **Build.** One implementer per ticket of the frontier, each on `ticket/<n>` in its own worktree. At most **four implementers run at once**; the next ticket starts when one reports. The suite runs one pytest at a time on the machine (`local-checks.md` § Done checks), so a fifth implementer only lengthens the queue, and the cap is what stops a repeat of 2026-10-01, when ten concurrent `pytest -n auto` runs pushed app.slice past systemd-oomd's limit and oomd killed the owner's terminal. A ticket that starts after others have merged gets the same prompt: its implementer reads what moved since the note from `git log` (`docs/agents/implementer-brief.md`). Done when the branch holds the ticket's commits and the implementer has reported.
3. Merge each finished ticket branch into the spec branch. Each **Found** and **Left open** item of an implementer's report goes into `carry-to-review.md` as it arrives. Done when every ticket is merged and the done checks pass on the spec branch.
4. Spec review: one reviewer, a fresh subagent running `/mattpocock-skills:code-review` on the spec branch against the effort branch, with the Standards axis on Opus and the Spec axis on Fable (§ Subagents). The Spec axis also gets `carry-to-review.md` and returns a verdict on each carried item. The reviewer writes its report to `review-<n>.md` in the notes directory and returns the path and counts. The report keeps **owner calls**, what only the owner can decide, apart from its findings. The findings become one fix implementer on the spec branch, merged the same way, which reports each finding as fixed or answered. In an effort of one spec the hand-test runs beside this review, and the fix implementer starts when both lists are in (step 7). Done when the review is clear, or every finding is fixed or answered in the PR.
5. Merge the effort branch into the spec branch, run the done checks, merge the spec branch into the effort branch, push. The first spec in opens the draft PR, base `main` (`docs/agents/issue-tracker.md` § Open a PR); its body carries a `Closes #<n>` line for every spec and ticket in the effort, added as each spec lands. Comment on the spec issue with the merge commit.

## Per effort

6. Effort review, once every spec is in: one reviewer on the effort branch against `main`. Findings become one fix implementer on the effort branch, started when the hand-test's list is in too (step 7). An effort of one spec skips this step: step 4 reviewed the same diff.
7. Hand-test the effort branch: the app running windowless against a nested Hyprland with a sandboxed config (`local-checks.md` § Running the app), never against the desktop compositor and never as a window on the owner's desktop. Each surface a ticket changed gets a widget probe of the running app, and a cropped screenshot where the change is visual; one surface the effort must leave as it was is probed too. An effort that touches the Engine's IPC, apply or importer paths also runs the Harness tier once, with `HARNESS_DRM_CARD` set (`local-checks.md` § Harness tier): without it the `Canvas` tests skip on the owner's NVIDIA host and the count proves little. An effort that changes no UI and no Engine runtime path (docs, CI, tests only) gets no hand-test: the review and CI cover it. The hand-tester starts when the effort's last review starts (step 6, or step 4 in an effort of one spec), on the same commit, since both only read; that review's fix implementer takes both lists. Once its fix is merged, a hand-tester probes once more, only the surfaces a fix changed. Defects that block the effort are fixed on the effort branch; the rest become GitHub issues labelled `needs-triage`.
8. Mark the PR ready for review. CI (`.github/workflows/ci.yml`) runs on a PR from here, and only from here: a draft gets no run, so a green check means everything on the PR has been addressed. Once CI is green, add the `ready-to-merge` label and one PR comment: what is done, the review verdicts, what the hand-test probed and its Harness-tier count (or why the effort got none), every owner call from the reviews and the hand-test, and what only the owner can check on real hardware (monitors, input devices). Each owner call is quoted, or marked settled with the commit that settled it: the comment is the only place the owner sees them. The label is the only ready signal: the `Ready to merge` check (`.github/workflows/merge-ready.yml`) reads it, and whoever reopens work on the PR removes it. Done when the label and the comment are on the PR.

The owner merges; `.claude/hooks/pr-base-guard.sh` refuses a merge from a session. The owner squash-merges, so once the PR has merged `git branch -d` refuses the effort's branches: confirm with `git merge-base --is-ancestor <branch> origin/main` or against the PR's merge, and delete with `-D`. Ticket worktrees are removed with `git worktree remove --force <path>` from the main checkout.

## Reviews: one per level

One review per spec (step 4) and one per effort (step 6); implementers ship without a self-review. A docs-only or config-only spec skips step 4 and step 7; the effort PR says so.

Fixes from a review go to a new implementer, not back to the one that built the ticket: the findings list takes the ticket's place in its brief. A review of a spec document, before any branch exists, is different: its findings go back to the spec's author, because the deliverable is the document.
