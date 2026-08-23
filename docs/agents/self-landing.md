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
gh pr merge <n> --squash
git fetch origin
git merge-base --is-ancestor "$(gh pr view <n> --json mergeCommit -q .mergeCommit.oid)" origin/main
```

Merge without `--delete-branch`: the repo deletes the remote branch on merge already, and the flag's local deletion fails from a worktree (`fatal: 'main' is already used by worktree`). The last line verifies the squash commit reached `main` (the PR head SHA never will — squash rewrites it). On failure the merge landed on a stale base — reland the head branch as a fresh PR against `main` and say so in your report. On success the landing is fully confirmed — close the ticket now; a vigil on post-merge CI only delays the baton.

## Cleanup

The remote branch is already gone (the repo deletes branches on merge). After a verified merge, remove the local leftovers too — worktree and branch — as the session's **very last act**, after the ticket close and your report, using absolute paths against the main checkout:

```sh
git -C <repo-root> worktree remove --force <your-worktree-path>
git -C <repo-root> branch -D issue-<n>
```

Last act because removing the worktree you stand in leaves the shell in a deleted directory — nothing runs after it. A held PR keeps its worktree and branches: they are still the work.

## Held PRs

A PR held by any gate stays open with a comment naming the gate. Close the ticket anyway, linking the PR — the ticket tracks the work existing, not the merge.
