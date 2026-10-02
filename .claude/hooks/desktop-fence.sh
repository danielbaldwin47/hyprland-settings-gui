#!/usr/bin/env bash
# PreToolUse guard on Bash: the desktop fence for an agent's own shell
# (docs/agents/local-checks.md, Running the app). The owner's desktop
# compositor is their daily session, and the session exports its
# HYPRLAND_INSTANCE_SIGNATURE and WAYLAND_DISPLAY, so a bare call reaches it.
# Four commands are refused unless they name a nested instance:
#
#  - `hyprctl`, any subcommand but `instances` (which reads lock files only).
#    Nested means a selector, `--instance <sig>`, `-i <sig>`, `--instance=<sig>`
#    before the subcommand or a `HYPRLAND_INSTANCE_SIGNATURE=<sig>` prefix,
#    whose <sig> is written out (no `$…`), is not an index (`hyprctl instances`
#    is time-sorted, so `-i 0` can be the desktop), is not the inherited
#    HYPRLAND_INSTANCE_SIGNATURE, and whose hyprland.lock names a live pid
#    running with a HOME other than the account's. That last test is the
#    Harness guard's (tests/integration/harness/guard.py): the session's own
#    compositor runs the account's config, a nested one a sandbox HOME.
#  - `wtype`, which types into WAYLAND_DISPLAY: nested means a
#    `WAYLAND_DISPLAY=<display>` prefix naming the Wayland socket of an
#    instance that passes the same lock test.
#  - `Hyprland` (and `hyprland`, `start-hyprland`): tools/sandbox.py and the
#    Harness start the nested ones.
#  - `ydotool` (and `ydotoold`), always: it writes to the kernel's uinput and
#    has no nested form.
#
# A command is judged only at command position: the command is split into
# simple commands (quotes, `$(…)`, backticks and heredocs followed), and the
# word after any `VAR=…`, `env`, `command`, `exec` or wrapper (`timeout`,
# `nohup`, `sudo`, …) is the one judged, so `grep hyprctl docs/` or a heredoc
# commit message passes. Doubt fails closed: without jq, any call whose input
# contains one of the four words is refused. The hook guards against
# mistakes, not deliberate evasion: a call inside a script, `bash -c '…'`,
# `eval`, an alias, a command held in a variable, or a direct write to the
# compositor's socket (`socat`, `nc`) passes. tests/unit/test_no_unguarded_instance.py
# fences tests/integration; this hook fences the interactive route.
set -uo pipefail

input=$(cat)

if ! command -v jq > /dev/null 2>&1; then
    if grep -Eq 'hyprctl|Hyprland|wtype|ydotool' <<< "$input"; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"This call names hyprctl, Hyprland, wtype or ydotool, and without jq the desktop fence cannot tell whether it reaches the owner'"'"'s desktop compositor, so it fails closed. install jq, then run it again (.claude/hooks/desktop-fence.sh)."}}'
    fi
    exit 0
fi

cmd=$(jq -r '.tool_input.command // empty' <<< "$input")
[ -n "$cmd" ] || exit 0

deny() {
    jq -cn --arg reason "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
    exit 0
}

docs="docs/agents/local-checks.md, Running the app; .claude/hooks/desktop-fence.sh"
sig_shape="hyprctl --instance <signature printed by tools/sandbox.py>"
display_shape="WAYLAND_DISPLAY=<display printed by tools/sandbox.py> wtype …"

# --- Splitting the command ----------------------------------------------------

# Prints one line per simple command, its words separated by \037, each word
# prefixed `L` when literal or `X` when the shell expands it at run time
# (`$…`, a backtick, an unquoted glob). Quotes are removed; a heredoc's body,
# a comment and a redirection's target are dropped; `$(…)`, `<(…)` and
# backticks are simple commands of their own.
read -r -d '' split_awk << 'AWK'
function flush() {
    if (inword) {
        if (skipword) skipword = 0
        else { gsub(/\n/, " ", word); seg = seg (seg == "" ? "" : US) flag word }
    }
    word = ""; inword = 0; flag = "L"
}
function endseg() { flush(); if (seg != "") print seg; seg = "" }
function push(kind) {
    depth++
    skind[depth] = kind; sq[depth] = q; sword[depth] = word; sseg[depth] = seg
    pc[depth] = 0; seg = ""; word = ""; inword = 0; flag = "L"; q = ""
}
function pop() {
    endseg()
    q = sq[depth]; word = sword[depth]; seg = sseg[depth]; inword = 1; flag = "X"
    depth--
}
# Skips the bodies of the heredocs opened on the line that ends at s[i].
function heredocs(   k, j, line, nl) {
    for (k = 1; k <= nhd; k++) {
        while (i < n) {
            nl = index(substr(s, i + 1), "\n")
            if (nl == 0) { line = substr(s, i + 1); j = n } else { line = substr(s, i + 1, nl - 1); j = i + nl }
            i = j
            if (hdash[k]) sub(/^\t+/, "", line)
            if (line == hdelim[k]) break
        }
    }
    nhd = 0
}
BEGIN {
    s = ENVIRON["FENCE_CMD"]; n = length(s); US = "\037"
    seg = ""; word = ""; inword = 0; flag = "L"; q = ""; depth = 0; nhd = 0; skipword = 0; pc[0] = 0
    for (i = 1; i <= n; i++) {
        c = substr(s, i, 1); d = substr(s, i + 1, 1)
        if (q == "'") { if (c == "'") q = ""; else word = word c; continue }
        if (c == "\\") {
            if (q == "\"" && d !~ /[$`"\\\n]/) { word = word c; continue }
            i++
            if (d != "\n") { word = word d; inword = 1 }
            continue
        }
        if (c == "`") {
            if (depth > 0 && skind[depth] == "`") pop()
            else { inword = 1; flag = "X"; push("`") }
            continue
        }
        if (c == "$") {
            inword = 1; flag = "X"
            if (d == "(") { i++; push("(") } else word = word c
            continue
        }
        if (q == "\"") { if (c == "\"") q = ""; else word = word c; continue }
        if (c == "\"" || c == "'") { q = c; inword = 1; continue }
        if (c == " " || c == "\t") { flush(); continue }
        if (c == "#" && !inword) { while (i < n && substr(s, i + 1, 1) != "\n") i++; continue }
        if (c == "\n") { endseg(); if (nhd) heredocs(); continue }
        if ((c == "<" || c == ">") && d == "(") { flush(); i++; push("("); continue }
        if (c == "<" || c == ">" || (c == "&" && d == ">")) {
            if (word ~ /^[0-9]+$/ && flag == "L") { word = ""; inword = 0 } else flush()
            if (c == "<" && d == "<" && substr(s, i + 2, 1) != "<") {
                i++; dash = 0
                if (substr(s, i + 1, 1) == "-") { dash = 1; i++ }
                while (substr(s, i + 1, 1) ~ /[ \t]/) i++
                delim = ""
                while (i < n && substr(s, i + 1, 1) !~ /[ \t\n;&|<>()]/) {
                    i++; e = substr(s, i, 1)
                    if (e != "'" && e != "\"" && e != "\\") delim = delim e
                }
                nhd++; hdelim[nhd] = delim; hdash[nhd] = dash
                continue
            }
            while (substr(s, i + 1, 1) ~ /[<>&|]/) i++
            skipword = 1
            continue
        }
        if (c == ";" || c == "&" || c == "|") { endseg(); continue }
        if (c == "(") { endseg(); pc[depth]++; continue }
        if (c == ")") {
            if (pc[depth] > 0) { pc[depth]--; endseg() }
            else if (depth > 0 && skind[depth] == "(") pop()
            else endseg()
            continue
        }
        if (c ~ /[*?[]/) flag = "X"
        word = word c; inword = 1
    }
    endseg()
    while (depth > 0) pop()
    endseg()
}
AWK

# --- Judging an instance ------------------------------------------------------

account_home=$(getent passwd "$(id -u)" 2> /dev/null | cut -d: -f6)

# Why the instance whose hyprland.lock is $1 is not a nested one, or nothing.
not_nested_lock() {
    local pid home
    [ -n "${XDG_RUNTIME_DIR:-}" ] || { echo "XDG_RUNTIME_DIR is unset, so no instance can be read"; return; }
    [ -r "$1" ] || { echo "it has no hyprland.lock under \$XDG_RUNTIME_DIR/hypr, so no live instance answers to it"; return; }
    pid=$(head -n 1 "$1")
    [[ $pid =~ ^[0-9]+$ ]] || { echo "its hyprland.lock names no pid"; return; }
    home=$(tr '\0' '\n' < "/proc/$pid/environ" 2> /dev/null | sed -n 's/^HOME=//p') \
        || { echo "the environment of its pid $pid cannot be read"; return; }
    [ -n "$home" ] || { echo "the environment of its pid $pid cannot be read"; return; }
    if [ "$home" = "${HOME:-}" ] || [ "$home" = "$account_home" ]; then
        echo "it is the session's own compositor (it runs with HOME=$home)"
    fi
}

# Why the selector word $1 (flag + text) names no nested instance, or nothing.
not_nested_signature() {
    local flag=${1:0:1} sig=${1:1}
    if [ "$flag" = X ]; then
        echo "\`$sig\` is expanded only when the command runs, so the hook cannot tell which instance it names; write the signature out"
    elif [ -z "$sig" ]; then
        echo "the selector is empty"
    elif [[ $sig =~ ^[0-9]+$ ]]; then
        echo "\`$sig\` is an index into \`hyprctl instances\`, which is time-sorted, so it can be the desktop; name the signature instead"
    elif [ "$sig" = "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
        echo "\`$sig\` is the session's own HYPRLAND_INSTANCE_SIGNATURE"
    elif ! [[ $sig =~ ^[A-Za-z0-9_]+$ ]]; then
        echo "\`$sig\` is not an instance signature"
    else
        local why
        why=$(not_nested_lock "$XDG_RUNTIME_DIR/hypr/$sig/hyprland.lock")
        [ -z "$why" ] || echo "\`$sig\`: $why"
    fi
}

# Why the WAYLAND_DISPLAY word $1 (flag + text) is no nested instance's, or nothing.
not_nested_display() {
    local flag=${1:0:1} display=${1:1} lock socket
    if [ "$flag" = X ]; then
        echo "\`$display\` is expanded only when the command runs, so the hook cannot tell which display it names; write the display out"
        return
    fi
    [ -n "$display" ] || { echo "WAYLAND_DISPLAY is empty"; return; }
    [ "$display" = "${WAYLAND_DISPLAY:-}" ] && { echo "\`$display\` is the session's own WAYLAND_DISPLAY"; return; }
    [ -n "${XDG_RUNTIME_DIR:-}" ] || { echo "XDG_RUNTIME_DIR is unset, so no instance can be read"; return; }
    for lock in "$XDG_RUNTIME_DIR"/hypr/*/hyprland.lock; do
        [ -r "$lock" ] || continue
        socket=$(sed -n 2p "$lock")
        [ "$socket" = "$display" ] || continue
        why=$(not_nested_lock "$lock")
        [ -z "$why" ] && return
        echo "\`$display\` belongs to $(basename "$(dirname "$lock")"): $why"
        return
    done
    echo "no Hyprland instance under \$XDG_RUNTIME_DIR/hypr serves \`$display\`"
}

# --- Judging a simple command -------------------------------------------------

judge() {
    local -a w=("$@")
    local n=${#w[@]} i=0 text wrapped=0 his="" display=""
    # Prefix: keywords, assignments, `env` and its flags, wrappers and theirs.
    while ((i < n)); do
        text=${w[i]:1}
        case "$text" in
            '!' | '{' | '}' | if | then | else | elif | fi | do | done | while | until | time | exec | builtin \
                | nohup | setsid | sudo | doas | nice | timeout | stdbuf | xargs | watch | --)
                wrapped=1
                ;;
            command)
                [[ ${w[i + 1]:1} == -[vV] ]] && return # a lookup, not a run
                wrapped=1
                ;;
            env)
                wrapped=0
                while ((i + 1 < n)); do
                    case "${w[i + 1]:1}" in
                        -u | --unset | -C | --chdir) i=$((i + 2)) ;;
                        -S* | --split-string*) # a command line in one word: doubt
                            [[ $* =~ hyprctl|[Hh]yprland|wtype|ydotool ]] && return 1
                            return 0
                            ;;
                        -*) i=$((i + 1)) ;;
                        *) break ;;
                    esac
                done
                ;;
            HYPRLAND_INSTANCE_SIGNATURE=*) his=${w[i]:0:1}${text#*=} ;;
            WAYLAND_DISPLAY=*) display=${w[i]:0:1}${text#*=} ;;
            *)
                if [[ $text =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
                    :
                elif ((wrapped)) && [[ $text == -* || $text =~ ^[0-9.]+[smhd]?$ ]]; then
                    :
                else
                    break
                fi
                ;;
        esac
        i=$((i + 1))
    done
    ((i < n)) || return 0
    [ "${w[i]:0:1}" = L ] || return 0 # a command held in a variable: a stated limit
    local name=${w[i]:1}
    name=${name##*/}
    case "$name" in
        hyprctl) judge_hyprctl "$his" "${w[@]:i+1}" ;;
        wtype) judge_wtype "$display" ;;
        Hyprland | hyprland | start-hyprland)
            deny "\`$name\` from an agent's shell starts a compositor on the owner's desktop session (its seat, display and user manager). Start a nested one with \`.venv/bin/python tools/sandbox.py\` or the Harness tier's NestedHyprland; for the version, \`pacman -Q hyprland\` ($docs)."
            ;;
        ydotool | ydotoold)
            deny "\`$name\` writes to the kernel's uinput, so its keys and clicks land on the owner's desktop session; it has no nested form. Type into a nested instance with \`$display_shape\`, or drive the widget in a widget probe ($docs)."
            ;;
    esac
    return 0
}

judge_hyprctl() {
    local his=$1
    shift
    local -a args=("$@") selectors=()
    local j=0 m=${#args[@]} text sub=""
    while ((j < m)); do
        text=${args[j]:1}
        case "$text" in
            -i | --instance)
                selectors+=("${args[j + 1]:-L}")
                j=$((j + 1))
                ;;
            --instance=*) selectors+=("${args[j]:0:1}${text#--instance=}") ;;
            --batch) sub=--batch; break ;;
            -*) ;;
            *) sub=$text; break ;;
        esac
        j=$((j + 1))
    done
    [ "$sub" = instances ] && return 0
    local what="\`hyprctl${sub:+ $sub}\`"
    [ -n "$his" ] && selectors+=("$his")
    if ((${#selectors[@]} == 0)); then
        deny "$what names no instance, so it reaches the owner's desktop compositor (HYPRLAND_INSTANCE_SIGNATURE is the session's). Aim it at a nested instance: \`$sig_shape ${sub:-…} …\` ($docs)."
    fi
    local selector why
    for selector in "${selectors[@]}"; do
        why=$(not_nested_signature "$selector")
        [ -z "$why" ] && continue
        deny "$what may reach the owner's desktop compositor: $why. Aim it at a nested instance: \`$sig_shape ${sub:-…} …\` ($docs)."
    done
}

judge_wtype() {
    local why
    if [ -z "$1" ]; then
        why="it follows WAYLAND_DISPLAY, which is the session's (\`${WAYLAND_DISPLAY:-unset}\`)"
    else
        why=$(not_nested_display "$1")
        [ -z "$why" ] && return 0
    fi
    deny "\`wtype\` would type into the owner's desktop session: $why. Name a nested display: \`$display_shape\` ($docs)."
}

doubt="the desktop fence fails closed on a call it cannot read that names hyprctl, Hyprland, wtype or ydotool, since that call may reach the owner's desktop compositor"
segments=$(FENCE_CMD="$cmd" awk "$split_awk") \
    || deny "awk could not split this command, and $doubt. Report the command on the fence's ticket and run it in a nested instance's shape ($docs)."
while IFS= read -r line; do
    IFS=$'\037' read -r -a words <<< "$line"
    judge "${words[@]}" || deny "\`env -S\` hides the command it runs, and $doubt. Run the command without \`env -S\` ($docs)."
done <<< "$segments"
exit 0
