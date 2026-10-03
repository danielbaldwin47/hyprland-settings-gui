#!/usr/bin/env bash
# PreToolUse guard on Bash for the gh calls that decide where a PR lands
# (docs/agents/issue-tracker.md, Open a PR). Two shapes are refused:
#
#  - A merge: `gh pr merge`, `gh api` on a `pulls/<n>/merge` path, and
#    `gh api graphql` whose query names `mergePullRequest`. The owner
#    merges, from their own shell; a session opens the PR to main and marks
#    it ready, and the merge closes the tickets its body names
#    (docs/agents/issue-tracker.md, Open a PR). All three shapes are judged
#    because sessions refused `gh pr merge` have merged through the REST path.
#  - `gh pr create --base <b>` and `gh pr edit --base <b>` where <b> is not
#    main, has a MERGED PR to main and no OPEN one: a dead base. GitHub
#    closes issues only on a merge into the default branch, so a PR onto a
#    dead base strands every `Closes #<n>` in its body (in next-train, two
#    tickets stayed open for two days that way).
#
# Ported from next-train, which adapted Quill's hook of the same name. The hook reads
# the call it is about to allow and judges only those shapes at command
# position. Everything else passes. The two rules treat doubt differently:
# the base rule passes on every doubt (no jq, no gh, gh failing, no base
# named); the merge rule fails closed (without jq, any trace of a merge in
# the input denies). The hook guards against mistakes, not
# against deliberate evasion: a merge behind `bash -c '…'`, a script or an
# alias is a shell indirection a command hook cannot follow.
set -uo pipefail
set -f # the command is split into words below, and a `*` in it stays a `*`

input=$(cat)

if ! command -v jq > /dev/null 2>&1; then
    # Without jq the command cannot be parsed. The merge rule still fails
    # closed: any trace of a merge in the raw input denies.
    if grep -Eq 'gh pr merge|pulls/[^/[:space:]]+/merge|mergePullRequest' <<< "$input"; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"A merge, and no jq to judge it: the merge rule fails closed (.claude/hooks/pr-base-guard.sh)."}}'
    fi
    exit 0
fi

cmd=$(jq -r '.tool_input.command // empty' <<< "$input")
[ -n "$cmd" ] || exit 0

deny() {
    jq -cn --arg reason "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
    exit 0
}

# One segment per simple command: split on the operators between them.
segment_regex='s/(&&|\|\||[;|()]|\$\()/\n/g'

# Drops what stands before the command in $segment: leading space, `VAR=…`,
# `env` with its flags, `command`, `exec`. A host shell may export GH_REPO,
# so `env -u GH_REPO gh …` is the usual form of a gh call.
strip_prefix() {
    local word
    while :; do
        segment=${segment#"${segment%%[![:space:]]*}"}
        word=${segment%%[[:space:]]*}
        case "$word" in
            env | command | exec) segment=${segment#"$word"} ;;
            -u | --unset)
                segment=${segment#"$word"}
                segment=${segment#"${segment%%[![:space:]]*}"}
                segment=${segment#"${segment%%[[:space:]]*}"}
                ;;
            -i | --ignore-environment | --unset=*) segment=${segment#"$word"} ;;
            *=*) [[ $word =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || break; segment=${segment#"$word"} ;;
            *) break ;;
        esac
    done
}

# --- The merge rule -----------------------------------------------------------

# Judged on the raw command, quotes included: a quoted API path or GraphQL
# query is still a merge. Only a segment whose command is `gh` (by name or by
# path, after any `VAR=…`, `env` with its flags, `command` or `exec` prefix) is a candidate,
# so a `gh pr merge` inside an echo or a commit message passes. gh takes its
# repo flag before the subcommand (`gh -R o/r pr merge`, `gh --repo=o/r api`),
# so gh_re also skips `-R x`, `-R=x`, `--repo x` and `--repo=x`.
gh_re='^([^[:space:]]*/)?gh[[:space:]]+((-R|--repo)(=|[[:space:]]+)[^[:space:]]+[[:space:]]+)*'
mapfile -t raw_segments < <(sed -E "$segment_regex" <<< "$cmd")
for segment in "${raw_segments[@]}"; do
    strip_prefix
    if [[ $segment =~ ${gh_re}pr[[:space:]]+merge([[:space:]]|$) ]]; then
        :
    elif [[ $segment =~ ${gh_re}api([[:space:]]|$) ]]; then
        grep -Eq '(^|[^[:alnum:]_])pulls/[^/[:space:]"'"'"']+/merge([^[:alnum:]_]|$)' <<< "$segment" \
            || grep -q mergePullRequest <<< "$segment" \
            || continue
    else
        continue
    fi
    deny "Merging is the owner's: a session opens the PR to main and marks it ready for review (docs/agents/issue-tracker.md, Open a PR)."
done

# --- The base rule ------------------------------------------------------------

# The command with its quoted strings removed, so a `--base` inside a commit
# message or an echo is not a base.
bare=$(sed -E "s/\"[^\"]*\"//g; s/'[^']*'//g" <<< "$cmd")
mapfile -t segments < <(sed -E "$segment_regex" <<< "$bare")
base=""
repo=""
verb=""
for segment in "${segments[@]}"; do
    strip_prefix
    [[ $segment =~ ${gh_re}pr[[:space:]]+(create|edit)[[:space:]] ]] || continue
    verb=${BASH_REMATCH[-1]}
    expect=""
    for word in $segment; do
        if [ -n "$expect" ]; then
            case "$expect" in base) base=$word ;; repo) repo=$word ;; esac
            expect=""
            continue
        fi
        case "$word" in
            -B | --base) expect=base ;;
            --base=*) base=${word#--base=} ;;
            -R | --repo) expect=repo ;;
            --repo=*) repo=${word#--repo=} ;;
        esac
    done
    [ -n "$base" ] && break # one base is enough to judge; the gh call below is bounded to one
done
[ -n "$base" ] || exit 0
[ "$base" = main ] && exit 0

command -v gh > /dev/null 2>&1 || exit 0
cwd=$(jq -r '.cwd // empty' <<< "$input")
[ -d "$cwd" ] && cd "$cwd"
# With no repo named in the command, the clone's remote decides, not the
# GH_REPO a host shell may export.
gh_repo=()
if [ -n "$repo" ]; then gh_repo=(-R "$repo"); else unset GH_REPO; fi

# The base's own PRs to main. Refuse only when one has merged and none is open.
states=$(timeout 8 gh pr list --state all --head "$base" --base main "${gh_repo[@]}" --json state --jq '.[].state' 2> /dev/null) || exit 0
grep -qx MERGED <<< "$states" || exit 0
grep -qx OPEN <<< "$states" && exit 0

deny "\`gh pr $verb --base $base\`: that branch's own PR to main has merged, so a PR onto it closes nothing on main. Use \`--base main\`, or retarget with \`gh pr edit <n> --base main\` (docs/agents/issue-tracker.md, Open a PR)."
