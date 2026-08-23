# Self-landing PRs

Agents merge their own PRs in this repo — background agents included. Putting `ready-for-agent` on a ticket is the approval; no human reviews the PR afterward, so the gates below are the whole safety review.

## Gates

All three must hold before merging:

1. **Tests green** on the branch. _(No repo-wide test runner exists yet; when one lands, name its command here — it becomes this gate.)_
2. **Review clean** — `/code-review` has run and the PR body carries `Review: clean`. Findings held → write `Review: findings held` instead and leave the PR for a human.
3. **Base is `main`.** A PR stacked on an unmerged branch waits: once its base lands, retarget to `main`, rebase, and re-check these gates.

## Merge

```sh
gh pr ready <n>
gh pr merge <n> --squash --delete-branch
git fetch origin && git merge-base --is-ancestor <head-sha> origin/main
```

The last line verifies the commits reached `main`. On failure the merge landed on a stale base — reland the head branch as a fresh PR against `main` and say so in your report.

## Held PRs

A PR held by any gate stays open with a comment naming the gate. Close the ticket anyway, linking the PR — the ticket tracks the work existing, not the merge.
