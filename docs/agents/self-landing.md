# Self-landing PRs

Read by the agent whose PR is green and reviewed, before `gh pr merge`.

Agents merge their own PRs in this repo — background agents included. Putting `ready-for-agent` on a ticket is the approval; no human reviews the PR afterward, so the gates below are the whole safety review.

## Gates

All four must hold before merging:

1. **Tests green** on the branch: the PR's CI checks all pass (`gh pr checks <n>` — the workflow runs ruff, mypy, and the meson test suite). Locally, `meson test -C build` covers the same suite.
2. **Review verdict, cross-checked.** `/code-review` has run and each review subagent has posted its own verdict as a PR comment — first line `Verdict: clean` / `Verdict: findings-<n>` / `Verdict: held`, one line per finding, a contemporaneous record written at review time. When findings were fixed, the fix batch carries a delta re-review comment of its own. The PR body line matches: `Review: clean` (no findings), `Review: fixed-<n>` (findings fixed, delta comment clean), or `Review: findings held`. The body line alone authorizes nothing — the gate counts findings in the comments against the line. Held, disagreeing, or missing comments → the PR stays for a human.
3. **Base is `main`.** A PR stacked on an unmerged branch waits: once its base lands, retarget to `main`, rebase, and re-check these gates. A dispatcher may perform that retarget/rebase/land mid-run the moment the base clears — base-gate holds only; review-held PRs always wait for the human.
4. **Acceptance evidence is citable.** CI green is necessary, not sufficient: when a ticket's acceptance proof lives outside CI — the nested-compositor integration tier, or the UI verification CLAUDE.md requires for UI-facing work — the PR body cites the local run: command, pass count, HEAD SHA it ran against. No citation, no merge. After any rebase, the citation is stale: re-run the tier before landing.

A PR whose diff touches `.github/workflows/`, `tests/golden/`, or `docs/adr/` is modifying its own gate inputs: it declares them in the body under `Self-gate changes:`. The gate check greps the diff for those paths — present but undeclared holds the PR.

## Merge

```sh
gh pr ready <n>
gh pr merge <n> --squash
git fetch origin
git merge-base --is-ancestor "$(gh pr view <n> --json mergeCommit -q .mergeCommit.oid)" origin/main
```

Merge without `--delete-branch`: the repo deletes the remote branch on merge already, and the flag's local deletion fails from a worktree (`fatal: 'main' is already used by worktree`). Run the fetch and verify against your own worktree (`git -C <your-worktree> fetch origin` then the merge-base check) — commands redirected at the shared checkout are refused under worktree isolation. The last line verifies the squash commit reached `main` (the PR head SHA never will — squash rewrites it). On failure the merge landed on a stale base — reland the head branch as a fresh PR against `main` and say so in your report. On success the landing is fully confirmed — close the ticket now; a vigil on post-merge CI only delays the baton.

## Cleanup

The remote branch is already gone (the repo deletes branches on merge). After a verified merge, remove the local leftovers too — worktree and branch — as the session's **very last act**, after the ticket close and your report, using absolute paths against the main checkout:

```sh
git -C <repo-root> worktree remove --force <your-worktree-path>
git -C <repo-root> branch -D issue-<n>
```

Last act because removing the worktree you stand in leaves the shell in a deleted directory — nothing runs after it. This works only for a worktree you created yourself (`git worktree add`); a harness-created isolation worktree refuses to remove itself — skip cleanup there, say so in your report, and the dispatcher reclaims it. A held PR keeps its worktree and branches: they are still the work.

## Held PRs

A PR held by any gate stays open — **marked ready, never left in draft** — with a comment naming the gate. Close the ticket anyway, linking the PR — the ticket tracks the work existing, not the merge. During a dispatch run, the dispatcher lands base-gate holds as their bases clear, per gate 3.
