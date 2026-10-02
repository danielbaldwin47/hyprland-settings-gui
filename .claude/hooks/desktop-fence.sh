#!/usr/bin/env bash
# PreToolUse guard on Bash and Monitor: the desktop fence for an agent's own shell
# (docs/agents/local-checks.md, Running the app and Private X displays). The
# owner's desktop compositor is their daily session, and the session exports its
# HYPRLAND_INSTANCE_SIGNATURE, WAYLAND_DISPLAY and DISPLAY, so a bare call reaches
# it. Five commands are refused unless they name a nested instance or a private
# display:
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
#  - `wtype`, `wl-copy`, `grim` and `hyprpicker`, which act on WAYLAND_DISPLAY:
#    nested means a `WAYLAND_DISPLAY=<display>` prefix naming the Wayland socket
#    of an instance that passes the same lock test.
#  - `xdotool`, which types into DISPLAY (the desktop's Xwayland): private means
#    a `DISPLAY=:<n>` prefix, n in 200-999, written out.
#  - `Hyprland` (and `hyprland`, `start-hyprland`, `cage`, `sway`): tools/sandbox.py
#    and the Harness start the nested ones.
#  - `ydotool` (and `ydotoold`), always: it writes to the kernel's uinput and
#    has no nested form.
#
# More families are refused always (#209, review of #151), each with its working shape:
#
#  - A pattern kill, which can kill the owner's Hyprland, terminal or apps:
#    `pkill`, `killall`, `killall5`, `skill`, `fuser -k`, and `kill` of pids found by name (`$(pgrep …)`,
#    `$(pidof …)`, `$(ps …)`, a variable or `read` filled from them, or a pipe from
#    them into `xargs kill`), or of a process group (`kill 0`, `kill -1`,
#    `kill -- -<pgid>`). Kill a PID you started and recorded: `kill <pid>`, `kill $!`.
#  - A GTK start outside the probe route: `python`/`python3`/`pythonX.Y` code
#    (`-c`, a heredoc or here-string on stdin, or a pipe into `python -`) that
#    loads GTK (`import gi`, `from gi`, `gi.require_version`, `gi.repository`), or
#    running the app directly (`-m hyprtweaker`, a script under
#    `src/hyprtweaker`) instead of through `tools/sandbox.py`. The route is
#    `tools/widget_probe.py`, `tools/sandbox.py` or pytest. The same python code
#    is refused when it runs a fenced command (`subprocess`, `os.system`, …) or
#    removes or links an X socket or lock (`os.unlink`, …).
#  - An X server started from the shell: `Xvfb`, `Xorg`, `X`, `Xwayland`, `Xephyr`,
#    `Xnest`, `Xvnc`, `xvfb-run`, `startx`, `xinit` (an X server unlinks the socket
#    of the display it binds, which is how an agent's Xvfb replaced the desktop's
#    `:0`). pytest and tools/widget_probe.py start their own Xvfb as children, on a
#    private display, and pass.
#  - `rm`, `unlink`, `rmdir`, `mv`, `ln`, `shred`, and `find` with `-delete` or
#    an `-exec` of one of those, when an operand (after one level of `{a,b}`) is
#    under `/tmp/.X11-unix/`, is a `/tmp/.X<n>-lock`, or is `/` or `/tmp` above
#    them. Reading them (`ls`, `ss -xlp`, `find -exec ls`) passes.
#  - A command on the owner's session or its managers: `omarchy-*` (but
#    `omarchy-version`), `hyprpm` (but `list`), `uwsm` (but `check`), `hyprshot`,
#    `notify-send`, `loginctl` and `systemctl` but their reads (`list-*`, `show*`,
#    `*-status`; `status`, `cat`, `is-*`).
#  - Setting `HYPRTWEAKER_UI_HOST_DISPLAY` or `HYPRTWEAKER_HARNESS_HOST_WINDOW`, the
#    owner's opt-ins that put the UI tier or the Harness on the desktop, as a prefix,
#    an assignment or an `export`.
#  - An app launch outside the sandbox (#148 review F12): the `hyprtweaker` launcher,
#    `meson devenv` (its command judged as typed), `tools/sandbox.py --window`; and the
#    Harness tier (`pytest tests/integration`) without `HARNESS_DRM_CARD=` on it.
#  - A theming tool, wallpaper daemon or bar (ruling A14): matugen, wallust, noctalia,
#    qs/quickshell, dms, swww, awww, hyprpaper, waybar; `gsettings` and `dconf` but
#    their reads; `dbus-update-activation-environment`. The session's own daemons
#    (#270): hyprlock, hypridle, hyprsunset, swaybg, mpvpaper; `makoctl` but its reads.
#  - A desktop launcher: `gtk-launch`, `gio launch`, and the app's desktop entry
#    (`io.github.danielbaldwin47.Hyprtweaker[.desktop]`) wherever a command stands.
#  - python code that starts the app (`hyprtweaker.application` with `main(` or
#    `.run(`, `runpy.run_module("hyprtweaker…")`), as `-c` or on stdin.
#  - `git stash` but `list` and `show`: one stack shared by every worktree.
#
# A command is judged only at command position: the command is split into
# simple commands (quotes, `$(…)`, backticks, pipes and heredocs followed), and
# the word after any `VAR=…`, `env`, `command`, `exec` or wrapper (`timeout`,
# `nohup`, `sudo`, `xargs`, `flock`, the launchers `uwsm-app`, `uwsm app` and
# `app2unit`, …, each with the options that take a value, and `--`) is the one
# judged, so `grep hyprctl docs/` or a heredoc commit message passes.
# A command line handed to a shell is split and judged in turn: `bash -c '…'`,
# `sh -c`, a heredoc into `bash`, `eval`, `watch '…'` and `flock -c`. Doubt fails
# closed: without jq, any call whose input names one of the fenced words, a GTK
# load in python, an app start, a pattern kill or a removal or link of an X
# socket or lock is refused. The hook guards against mistakes, not deliberate
# evasion: a call inside a script file, an alias, a command held in a variable,
# python code in a script file or read from a file, a kill by a variable filled
# in an earlier call, a path relative to a `cd` into /tmp/.X11-unix, or a direct
# write to the compositor's socket (`socat`, `nc`) passes.
# tests/unit/test_no_unguarded_instance.py fences tests/integration; this hook
# fences the interactive route.
set -uo pipefail

input=$(cat)

if ! command -v jq > /dev/null 2>&1; then
    # Raw JSON, so no command position: the words, a GTK python -c, an app start, an X file.
    raw_words='hyprctl|Hyprland|wtype|ydotool|xdotool|pkill|killall|xvfb-run|Xvfb|Xorg|Xwayland|Xephyr|Xnest|Xvnc|startx|xinit|HYPRTWEAKER_UI_HOST_DISPLAY=[^ "]'
    raw_x='(^|[^[:alnum:]_.-])X +:[0-9]'
    raw_kill='(pgrep|pidof)[^;&]*kill|kill[^;&|]*(pgrep|pidof)|fuser[^;&|]* -[a-z]*k|kill( +-[A-Za-z0-9]+)? +(--? +)?(0|-1)([\";]|$)'
    raw_gtk='python[^|;&]*-[A-Za-z]*c[ "'"'"'].*(Gtk|Adw|Gdk|gi\.repository)'
    raw_stdin='python[0-9.]*( +-)? *<<.*(Gtk|Adw|Gdk|gi\.repository)'
    raw_app='python[0-9.]*( +-[A-Za-z]+)* +(-[A-Za-z]*m *hyprtweaker|src/hyprtweaker)|hyprtweaker\.application|run_module\(.?.?hyprtweaker|Hyprtweaker(\.desktop)?([^[:alnum:]_.-]|$)'
    raw_xfile='(^|[^[:alnum:]_.-])(rm|unlink|rmdir|mv|ln|shred|find)[ \t"][^|;&]*(\.X11-unix|\.X[^ /"]*-lock|/tmp/\.X[0-9]*[*?])'
    raw_session='(^|[^[:alnum:]_.-])(omarchy-[a-z]|(hyprpm|uwsm|uwsm-app|app2unit|gtk-launch|hyprlock|hypridle|hyprsunset|swaybg|mpvpaper|makoctl|loginctl|notify-send|wl-copy|grim|hyprshot|hyprpicker|cage|sway|killall5|skill|matugen|wallust|noctalia|quickshell|swww|awww|hyprpaper|dbus-update-activation-environment)([^[:alnum:]_.-]|$))|gsettings +(set|reset)|dconf +(write|reset|load|update)|gio +launch|git( +-[^ ]+)* +stash( +(push|pop|apply|drop|clear|save|store|create|branch)|[\";]|$)|sandbox\.py[^|;&]* --window|HYPRTWEAKER_HARNESS_HOST_WINDOW=[^ "]'
    raw_systemctl='systemctl[^|;&]* (start|stop|restart|try-restart|reload|reload-or-restart|kill|isolate|mask|unmask|enable|disable|daemon-reload|set-environment|unset-environment|import-environment|edit|poweroff|reboot|suspend|hibernate)'
    raw_find_tmp='find +/+(tmp/*)?[ "][^|;&]*-(delete|exec)'
    if grep -Eq "$raw_words|$raw_x|$raw_kill|$raw_gtk|$raw_stdin|$raw_app|$raw_xfile|$raw_session|$raw_systemctl|$raw_find_tmp" <<< "$input"; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"This call names hyprctl, Hyprland, wtype, ydotool, xdotool, a pattern kill (pkill, killall, fuser -k, kill by pgrep or pidof), an X server (Xvfb, Xorg, X, Xwayland, Xephyr, Xnest, Xvnc, xvfb-run), a python -c that names Gtk, Adw, Gdk or gi.repository, an app start, HYPRTWEAKER_UI_HOST_DISPLAY, a command on the owner'"'"'s session (omarchy-*, hyprpm, uwsm, loginctl, systemctl stop or restart, notify-send, wl-copy, grim, hyprshot, hyprpicker, cage, sway), or a removal or link of /tmp/.X11-unix or an X lock, and without jq the desktop fence cannot tell whether it reaches the owner'"'"'s desktop session, so it fails closed. install jq, then run it again (.claude/hooks/desktop-fence.sh)."}}'
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
xdocs="docs/agents/local-checks.md, Private X displays; .claude/hooks/desktop-fence.sh"
fenced_words='hyprctl|[Hh]yprland|hyprpm|hyprshot|hyprpicker|omarchy-|uwsm|loginctl|notify-send|wl-copy|wtype|ydotool|xdotool|pkill|killall|xvfb-run|Xvfb|Xorg|Xwayland|Xephyr|Xnest|Xvnc|startx|xinit|HYPRTWEAKER_UI_HOST_DISPLAY|HYPRTWEAKER_HARNESS_HOST_WINDOW|matugen|wallust|noctalia|quickshell|swww|awww|hyprpaper|waybar|gsettings|dbus-update-activation-environment|app2unit|gtk-launch|hyprlock|hypridle|hyprsunset|swaybg|mpvpaper|makoctl|dconf|hyprtweaker|Hyprtweaker|\.X11-unix|\.X[^ /]*-lock'
re_x_dir='(^|/)\.X11-unix(/|$)'
re_x_lock='^(/tmp/)?\.X[^/]*-lock$'
re_x_glob='(^|/)\.X[0-9]*[*?[]'
re_x_above='^/+(tmp/*)?$' # `/` or `/tmp`: removing it removes the X files with it
# Python code that loads GTK, runs a command, or removes or links a file.
re_gtk='gi\.repository|gi\.require_version|(^|[^[:alnum:]_.])(import|from)[[:space:]]+gi([^[:alnum:]_]|$)'
re_py_run='subprocess|Popen|os\.system|os\.popen|os\.exec|os\.spawn|pty\.spawn|create_subprocess'
re_py_word='(^|[^[:alnum:]_.-])(hyprctl|Hyprland|hyprland|start-hyprland|wtype|ydotool|ydotoold|xdotool|pkill|killall|xvfb-run|Xvfb|Xorg|Xwayland|Xephyr|Xnest|Xvnc|startx|xinit|matugen|wallust|noctalia|noctalia-shell|qs|quickshell|dms|swww|awww|swww-daemon|awww-daemon|hyprpaper|waybar|gsettings|dbus-update-activation-environment|uwsm|uwsm-app|app2unit|gtk-launch|hyprlock|hypridle|hyprsunset|swaybg|mpvpaper|makoctl|dconf)([^[:alnum:]_./-]|$)'
# Python code that starts the app, as `python -m hyprtweaker` would (#270).
re_py_app='hyprtweaker\.(application|__main__)([^[:alnum:]_]|$).*(main|run)[[:space:]]*\(|run_(module|path)\([^)]*hyprtweaker'
# The app's desktop entry, launched by id or by file wherever a command stands.
re_app_entry='(^|/)io\.github\.danielbaldwin47\.Hyprtweaker(\.desktop)?(:.*)?$'
# A theming tool, wallpaper daemon or bar: each writes, recolours or restarts something on
# the owner's desktop session (F12 and ruling A14 of the #148 review). Tests and probes use
# the refusing stand-ins and stub tools of tests/hermetic.py; real tools are the owner's.
re_desktop_tool='^(matugen|wallust|noctalia|noctalia-shell|qs|quickshell|dms|swww|awww|swww-daemon|awww-daemon|hyprpaper|waybar)$'
re_py_unlink='unlink|remove|rename|replace|rmtree|rmdir|symlink|link'
re_py_xfile='\.X11-unix|\.X[0-9]+-lock'
# A word that holds pids found by name: the splitter writes `$(<first command>)`
# for a command substitution.
re_pid_sub='\$\((pgrep|pidof|ps|lsof|fuser)\)'
re_pid_finder=' (pgrep|pidof|ps|lsof|fuser) '
sig_shape="hyprctl --instance <signature printed by tools/sandbox.py>"
display_shape="WAYLAND_DISPLAY=<display printed by tools/sandbox.py> wtype …"
kill_shape="Kill a PID you started and recorded: \`kill <pid>\`, or \`kill \$!\` after a background start"

# The options of each wrapper that take their value as the next word. A word
# misread here becomes the judged command, so each list follows the tool's --help.
# `sudo -h` stays out: it is `--help` alone and `--host=…` attached.
declare -A wrapper_values=(
    [timeout]=" -s -k --signal --kill-after "
    [sudo]=" -u -g -p -C -D -R -T -U -r -t --user --group --prompt --close-from --chdir --chroot --command-timeout --other-user --role --type --host "
    [doas]=" -u -C "
    [xargs]=" -a -d -E -I -L -n -P -s --arg-file --delimiter --max-lines --max-args --max-procs --max-chars --process-slot-var "
    [stdbuf]=" -i -o -e --input --output --error "
    [watch]=" -n -q -s --interval --equexit --shotsdir "
    [nice]=" -n --adjustment "
    [ionice]=" -c -n -p -P -u --class --classdata --pid --pgid --uid "
    [chrt]=" -T -P -D --sched-runtime --sched-period --sched-deadline "
    [flock]=" -w -E --timeout --wait --conflict-exit-code "
    [strace]=" -a -b -e -E -I -o -O -p -P -s -S -u -U -X --output --trace --signal --status --attach --user --env "
    [systemd-run]=" -p -u -E -M -H -C --property --unit --setenv --machine --host --capsule --description --slice --uid --gid --nice --working-directory --service-type "
    [dbus-run-session]=" --config-file --dbus-daemon "
    [exec]=" -a "
    [uwsm-app]=" -s -t -a -u -d -p -S "
    [app2unit]=" -s -t -a -u -d -p -S "
)
# Wrappers whose first operand is not the command: flock's lock file, taskset's mask.
declare -A wrapper_positionals=([flock]=1 [taskset]=1)

# --- Splitting the command ----------------------------------------------------

# Reads the command on stdin and prints one line per simple command, its words
# separated by \037, each word prefixed `L` when literal or `X` when the shell
# expands it at run time (`$…`, a backtick, an unquoted glob). Quotes are
# removed; a comment and a redirection's target are dropped; a newline inside a
# word becomes \036. `$(…)`, `<(…)` and backticks are simple commands of their
# own, printed before the one holding them, which keeps the word as
# `$(<first command name>)`. A line may open with `P` (it reads a pipe from the
# line before) and `D` (it runs inside a substitution), and close with an
# `H<text>` word per heredoc or here-string it reads (lines joined by \036).
read -r -d '' split_awk << 'AWK'
function flush() {
    if (inword) {
        gsub(/\n/, NL, word)
        if (skipword == 1) skipword = 0
        else if (skipword == 2) { skipword = 0; body[curid] = body[curid] US "H" word }
        else seg = seg (seg == "" ? "" : US) flag word
    }
    word = ""; inword = 0; flag = "L"
}
function endseg(   k, w1) {
    flush()
    if (seg != "") {
        nout++; order[nout] = curid; out[curid] = seg; dep[curid] = depth
        if (depth > 0 && sfirst[depth] == "") {
            k = index(seg US, US); w1 = substr(seg, 2, k - 2); sub(/.*\//, "", w1)
            sfirst[depth] = w1
        }
        curid = ++nid
    }
    seg = ""
}
function push(kind) {
    depth++
    skind[depth] = kind; sq[depth] = q; sword[depth] = word; sseg[depth] = seg
    scur[depth] = curid; sskip[depth] = skipword; sfirst[depth] = ""
    curid = ++nid; skipword = 0
    pc[depth] = 0; seg = ""; word = ""; inword = 0; flag = "L"; q = ""
}
function pop() {
    endseg()
    q = sq[depth]; seg = sseg[depth]; curid = scur[depth]; skipword = sskip[depth]
    word = sword[depth] "$(" sfirst[depth] ")"; inword = 1; flag = "X"
    depth--
}
# Reads the bodies of the heredocs opened on the line that ends at s[i].
function heredocs(   k, j, line, text) {
    for (k = 1; k <= nhd; k++) {
        text = ""
        while (i < n) {
            j = i + 1
            while (j <= n && substr(s, j, 1) != "\n") j++
            line = substr(s, i + 1, j - i - 1)
            i = (j > n) ? n : j
            if (hdash[k]) sub(/^\t+/, "", line)
            if (line == hdelim[k]) break
            text = text (text == "" ? "" : NL) line
        }
        body[hid[k]] = body[hid[k]] US "H" text
    }
    nhd = 0
}
{ s = s (NR > 1 ? "\n" : "") $0 }
END {
    n = length(s); US = "\037"; NL = "\036"
    seg = ""; word = ""; inword = 0; flag = "L"; q = ""; depth = 0; nhd = 0; skipword = 0; pc[0] = 0
    nid = 1; curid = 1; nout = 0
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
                nhd++; hdelim[nhd] = delim; hdash[nhd] = dash; hid[nhd] = curid
                continue
            }
            herestring = (c == "<" && d == "<")
            while (substr(s, i + 1, 1) ~ /[<>&|]/) i++
            skipword = herestring ? 2 : 1
            continue
        }
        if (c == "|") {
            if (d == "|") { i++; endseg(); continue }
            if (d == "&") i++
            endseg(); piped[curid] = 1
            continue
        }
        if (c == ";" || c == "&") { endseg(); continue }
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
    for (k = 1; k <= nout; k++) {
        id = order[k]
        print (piped[id] ? "P" US : "") (dep[id] > 0 ? "D" US : "") out[id] body[id]
    }
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

# --- Judging a command line ---------------------------------------------------

doubt="the desktop fence fails closed on a call it cannot read that names hyprctl, Hyprland, wtype, ydotool, xdotool, pkill, killall, an X server or an X socket, since that call may reach the owner's desktop session"

# Splits the command line $1 and judges each simple command in it. Called again
# for a command line handed to a shell (`bash -c`, `eval`, `watch`, …).
judge_command() {
    local level=$((level + 1)) segments line piped inner judged_name="" pipeline=" " pipeline_text="" pid_vars=" "
    local -a words bodies
    if ((level > 5)); then
        [[ $1 =~ $fenced_words ]] && deny "This call nests shells more than five deep, and $doubt. Run the inner command directly ($docs)."
        return 0
    fi
    segments=$(LC_ALL=C awk "$split_awk" <<< "$1") || {
        [[ $1 =~ $fenced_words ]] && deny "awk could not split this command, and $doubt. Report the command on the fence's ticket and run it in a nested instance's shape ($docs)."
        return 0
    }
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        IFS=$'\037' read -r -a words <<< "$line"
        words=("${words[@]//$'\036'/$'\n'}")
        piped=0 inner=0 bodies=()
        while ((${#words[@]})); do
            case "${words[0]}" in
                P) piped=1 ;;
                D) inner=1 ;;
                *) break ;;
            esac
            words=("${words[@]:1}")
        done
        while ((${#words[@]})) && [ "${words[-1]:0:1}" = H ]; do
            bodies=("${words[-1]:1}" "${bodies[@]}")
            unset 'words[-1]'
        done
        # A pipeline is followed at the top level only; a substitution's own is not.
        ((inner)) && piped=0
        ((piped || inner)) || { pipeline=" " pipeline_text=""; }
        judge "${words[@]}" || deny "\`env -S\` hides the command it runs, and $doubt. Run the command without \`env -S\` ($docs)."
        if ((!inner)); then
            pipeline+="$judged_name "
            pipeline_text+=" $(printf '%s ' "${words[@]#?}")"
        fi
    done <<< "$segments"
}

# The words $2… (flag + text) joined as one command line, for a shell to run.
joined() {
    local -a rest=("${@#?}")
    printf '%s' "${rest[*]}"
}

# --- Judging a simple command -------------------------------------------------

judge() {
    local -a w=("$@")
    local n=${#w[@]} i=0 text wrapped=0 wrapper="" positional=0 his="" display="" xdisplay=""
    local launcher=""
    judged_name=""
    prefix_card=""
    # Prefix: keywords, assignments, `env` and its flags, wrappers and theirs.
    while ((i < n)); do
        text=${w[i]:1}
        case "$text" in
            '!' | '{' | '}' | if | then | else | elif | fi | do | done | while | until | time | builtin | --)
                wrapped=1
                ;;
            exec | nohup | setsid | sudo | doas | nice | timeout | stdbuf | xargs | watch | ionice | chrt \
                | taskset | flock | strace | systemd-run | dbus-run-session | coproc | uwsm-app | app2unit)
                wrapped=1 wrapper=$text positional=${wrapper_positionals[$text]:-0}
                [[ $text == uwsm-app || $text == app2unit ]] && launcher=$text
                ;;
            command)
                [[ ${w[i + 1]:-L} == ?-[vV] ]] && return # a lookup, not a run
                wrapped=1
                ;;
            env)
                wrapped=0
                while ((i + 1 < n)); do
                    case "${w[i + 1]:1}" in
                        -u | --unset | -C | --chdir) i=$((i + 2)) ;;
                        -S* | --split-string*) # a command line in one word: doubt
                            [[ $* =~ $fenced_words ]] && return 1
                            return 0
                            ;;
                        -*) i=$((i + 1)) ;;
                        *) break ;;
                    esac
                done
                ;;
            HYPRLAND_INSTANCE_SIGNATURE=*) his=${w[i]:0:1}${text#*=} ;;
            WAYLAND_DISPLAY=*) display=${w[i]:0:1}${text#*=} ;;
            DISPLAY=*) xdisplay=${w[i]:0:1}${text#*=} ;;
            HARNESS_DRM_CARD=?*) prefix_card=${text#*=} ;;
            *)
                if [[ $text =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
                    judge_assignment "${w[i]}"
                elif ((wrapped)) && [[ $text == -* ]]; then
                    if [ "$wrapper" = flock ] && [[ $text == -c || $text == --command ]]; then
                        text=${w[i + 1]:-L}
                        judge_command "${text:1}"
                        return 0
                    fi
                    [ -n "$wrapper" ] && [[ ${wrapper_values[$wrapper]:-} == *" $text "* ]] && i=$((i + 1))
                elif ((positional > 0)); then
                    positional=$((positional - 1))
                elif ((wrapped)) && [[ $text =~ ^[0-9.]+[smhd]?$ ]]; then
                    :
                elif [ "$wrapper" = watch ]; then # watch hands its words to `sh -c`
                    judge_command "$(joined "${w[@]:i}")"
                    return 0
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
    judged_name=$name
    local sub
    local -a rest=("${w[@]:i+1}")
    case "$name" in
        hyprctl) judge_hyprctl "$his" "${rest[@]}" ;;
        wtype | wl-copy | grim | hyprpicker) judge_wayland_client "$name" "$display" ;;
        hyprshot)
            deny "\`hyprshot\` asks the session's compositor (through hyprctl) what to shoot, so it captures the owner's desktop. Screenshot a nested instance: \`.venv/bin/python tools/sandbox.py --shot <png>\`, or \`WAYLAND_DISPLAY=<display printed by tools/sandbox.py> grim <png>\` ($docs)."
            ;;
        notify-send)
            deny "\`notify-send\` pops a notification on the owner's desktop session. Print what you need the owner to see to your own output, or put it in the effort PR's ready comment ($docs)."
            ;;
        omarchy-version*) ;;
        omarchy-*)
            deny "\`$name\` restarts or changes the owner's desktop session (Omarchy runs it there); it has no nested form. Probe behaviour in a nested instance from \`tools/sandbox.py\`, and leave a change to the owner's session to the owner ($docs)."
            ;;
        hyprpm)
            [[ $(first_operand " " "${rest[@]}") =~ ^(list|)$ ]] \
                || deny "\`hyprpm\` builds or loads plugins into the session's compositor on the owner's desktop. \`hyprpm list\` reads; leave plugin changes to the owner ($docs)."
            ;;
        uwsm)
            # `uwsm app` launches its command line as a unit: judge what it would run.
            [ "${rest[0]:1}" = app ] && { judge "Luwsm-app" "${rest[@]:1}"; return 0; }
            [[ $(first_operand " " "${rest[@]}") =~ ^(check|)$ ]] \
                || deny "\`uwsm\` starts, stops or launches into the owner's desktop session. Run the app in a nested instance with \`.venv/bin/python tools/sandbox.py\` ($docs)."
            ;;
        loginctl)
            sub=$(first_operand " -p -P -H -M -n -o -s --property --host --machine --lines --output --signal --kill-whom " "${rest[@]}")
            [[ $sub =~ ^(list-.*|show-.*|.*-status|)$ ]] \
                || deny "\`loginctl $sub\` acts on the owner's login session (ending, locking or signalling the desktop). Reads pass: \`loginctl list-sessions\`, \`loginctl show-session <id>\` ($docs)."
            ;;
        systemctl)
            sub=$(first_operand " -p -P -t -H -M -n -o -s --property --type --state --host --machine --lines --output --signal --kill-whom --kill-value --what --root --image --job-mode --preset-mode --timestamp --message --when " "${rest[@]}")
            [[ $sub =~ ^(status|show|cat|help|list-.*|is-.*|show-environment|get-default|)$ ]] \
                || deny "\`systemctl $sub\` changes a manager the owner's desktop session runs under (its units, its environment, the machine's power state). Reads pass: \`systemctl --user status|show|list-units\`, \`systemctl --user show-environment\` ($docs)."
            ;;
        xdotool) judge_xdotool "$xdisplay" ;;
        Hyprland | hyprland | start-hyprland | cage | sway)
            deny "\`$name\` from an agent's shell starts a compositor on the owner's desktop session (its seat, display and user manager). Start a nested one with \`.venv/bin/python tools/sandbox.py\` or the Harness tier's NestedHyprland; for the version, \`pacman -Q hyprland\` ($docs)."
            ;;
        ydotool | ydotoold)
            deny "\`$name\` writes to the kernel's uinput, so its keys and clicks land on the owner's desktop session; it has no nested form. Type into a nested instance with \`$display_shape\`, or drive the widget in a widget probe ($docs)."
            ;;
        pkill | killall | killall5 | skill)
            deny "\`$name\` matches processes by name across the whole session, so it can kill the owner's desktop compositor, terminal or apps, not only your own. $kill_shape ($docs)."
            ;;
        kill) judge_kill "${rest[@]}" ;;
        fuser)
            local arg
            for arg in "${rest[@]}"; do
                [[ ${arg:1} == --kill || ${arg:1} =~ ^-[A-Za-z]*k ]] || continue
                deny "\`fuser -k\` kills every process that holds the file or port, the owner's desktop compositor, Xwayland or terminal among them. $kill_shape ($docs)."
            done
            ;;
        matugen | wallust | noctalia | noctalia-shell | qs | quickshell | dms | swww | awww | swww-daemon | awww-daemon | hyprpaper | waybar)
            deny "\`$name\` is a theming tool, wallpaper daemon or bar: run from an agent's shell it recolours, re-wallpapers or restarts the owner's desktop session (a matugen post_hook reloads whichever compositor it finds). Tests use the refusing stand-ins and \`stub_tool\` of tests/hermetic.py, and the sandbox and widget probe put stand-ins first on PATH; a real run is the owner's ($docs)."
            ;;
        Xvfb | Xorg | X | Xwayland | Xephyr | Xnest | Xvnc | xvfb-run | startx | xinit)
            deny "\`$name\` from an agent's shell starts an X server, which unlinks the socket of the display number it binds (/tmp/.X11-unix/X<n>) without asking who listens there, so it can replace the desktop's own :0 (it did on 2026-10-01); \`xvfb-run\` also leaves GDK_BACKEND and WAYLAND_DISPLAY alone, so GTK maps on the desktop. Run \`.venv/bin/pytest tests/ui\` or \`.venv/bin/python tools/widget_probe.py <probe.py>\`, which start their own Xvfb on a private display; a script that needs an X server calls \`start_xvfb\` in tests/ui/private_display.py ($xdocs)."
            ;;
        export | declare | typeset | readonly | local)
            local arg
            for arg in "${rest[@]}"; do
                [[ ${arg:1} == *=* ]] && judge_assignment "$arg"
            done
            ;;
        for) # `for p in $(pgrep …)`: p holds pids found by name
            [ ${#rest[@]} -gt 2 ] && names_pids "${rest[@]:2}" && pid_vars+="${rest[0]:1} "
            ;;
        read) # `pgrep … | while read p`: p holds pids found by name
            if ((piped)) && [[ $pipeline =~ $re_pid_finder ]]; then
                local arg
                for arg in "${rest[@]}"; do
                    [[ ${arg:1} == -* ]] || pid_vars+="${arg:1} "
                done
            fi
            ;;
        bash | sh | dash | zsh | ksh | mksh | ash | fish) judge_shell "${rest[@]}" ;;
        meson) judge_meson "${rest[@]}" ;;
        gtk-launch)
            deny "\`gtk-launch\` starts a desktop application in the owner's session, so its window maps on the owner's desktop (the app's own entry among them). Run the app windowless in a nested Hyprland: \`.venv/bin/python tools/sandbox.py\` ($docs)."
            ;;
        gio)
            [ "$(first_operand " " "${rest[@]}")" = launch ] \
                && deny "\`gio launch\` starts a desktop entry in the owner's session, so its window maps on the owner's desktop (the app's own entry among them). Run the app windowless in a nested Hyprland: \`.venv/bin/python tools/sandbox.py\` ($docs)."
            ;;
        hyprlock | hypridle | hyprsunset | swaybg | mpvpaper)
            deny "\`$name\` is one of the session's own daemons (lock screen, idle, night light, wallpaper): run from an agent's shell it locks, dims or re-wallpapers the owner's desktop, or fights the instance already running there. It has no nested form here; leave it to the owner ($docs)."
            ;;
        makoctl)
            [[ $(first_operand " " "${rest[@]}") =~ ^(list|history|help|)$ ]] \
                || deny "\`makoctl $(first_operand " " "${rest[@]}")\` dismisses, restores or reconfigures the owner's desktop notifications. Reads pass: \`makoctl list\`, \`makoctl history\` ($docs)."
            ;;
        dconf)
            [[ $(first_operand " " "${rest[@]}") =~ ^(read|list|dump|watch|help|)$ ]] \
                || deny "\`dconf $(first_operand " " "${rest[@]}")\` writes the owner's desktop settings, which their running apps and theme read at once. Reads pass: \`dconf read\`, \`dconf list\`, \`dconf dump\`. Tests and probes keep GSettings in memory (GSETTINGS_BACKEND=memory) ($docs)."
            ;;
        hyprtweaker)
            deny "\`$name\` is the app's launcher: it runs the app against the session's own WAYLAND_DISPLAY, so its window maps on the owner's desktop and its writes reach the owner's real config. Run it windowless in a nested Hyprland: \`.venv/bin/python tools/sandbox.py\` ($docs)."
            ;;
        pytest | py.test) judge_pytest "${rest[@]}" ;;
        git) judge_git "${rest[@]}" ;;
        gsettings)
            [[ $(first_operand " --schemadir " "${rest[@]}") =~ ^(get|list-.*|range|describe|writable|help|)$ ]] \
                || deny "\`gsettings $(first_operand " --schemadir " "${rest[@]}")\` writes the owner's desktop settings (dconf), which their running apps and theme read at once. Reads pass: \`gsettings get\`, \`gsettings list-keys\`. Tests and probes keep GSettings in memory (GSETTINGS_BACKEND=memory) ($docs)."
            ;;
        dbus-update-activation-environment)
            deny "\`dbus-update-activation-environment\` rewrites the environment the owner's session bus starts services with, so their portals and apps can end up pointed at a dead display. It has no nested form; leave the session's environment to the owner ($docs)."
            ;;
        eval) judge_command "$(joined "${rest[@]}")" ;;
        python | python[0-9]*) judge_python "${rest[@]}" ;;
        rm | unlink | rmdir | mv | ln | shred | find) judge_x_files "$name" "${rest[@]}" ;;
        *)
            [[ $name =~ $re_app_entry ]] \
                && deny "\`$name\` is the app's desktop entry: launched, the app runs against the session's own WAYLAND_DISPLAY, so its window maps on the owner's desktop and its writes reach the owner's real config. Run it windowless in a nested Hyprland: \`.venv/bin/python tools/sandbox.py\` ($docs)."
            ;;
    esac
    # Judged first for what it runs (so the refusal names that); a launcher that would run
    # something harmless still starts it as a unit in the owner's session.
    [ -n "$launcher" ] \
        && deny "\`$launcher\` starts \`$name\` as a unit in the owner's desktop session: a window it opens maps on the owner's desktop, and the unit runs under their user manager. Run the command directly, and the app windowless in a nested Hyprland: \`.venv/bin/python tools/sandbox.py\` ($docs)."
    return 0
}

# An assignment word (flag + text): the owner's host-display opt-in, or a
# variable filled with pids found by name.
judge_assignment() {
    local text=${1:1}
    local var=${text%%=*} value=${text#*=}
    if [ "$var" = HYPRTWEAKER_UI_HOST_DISPLAY ] && [ -n "$value" ]; then
        deny "\`HYPRTWEAKER_UI_HOST_DISPLAY\` is the owner's opt-in: it maps the UI tier's windows on the owner's desktop session, where Hyprland may raise its \"Application Not Responding\" dialog over their work. Run \`.venv/bin/pytest tests/ui\` as it is, on its private Xvfb; to see a widget, \`.venv/bin/python tools/widget_probe.py <probe.py>\` and its \`shoot\`, or \`tools/sandbox.py --shot\` ($docs)."
    fi
    if [ "$var" = HYPRTWEAKER_HARNESS_HOST_WINDOW ] && [ -n "$value" ]; then
        deny "\`HYPRTWEAKER_HARNESS_HOST_WINDOW\` is the owner's opt-in: it nests the Harness tier's Hyprland into the owner's desktop session, one host window per test. Run the tier windowless: \`HARNESS_DRM_CARD=/dev/dri/card0 timeout 900 .venv/bin/pytest tests/integration -m hyprland\` ($docs)."
    fi
    names_pids "$1" && pid_vars+="$var "
    return 0
}

# `meson devenv [-C dir] [-w dir] <command>`: the command it runs is judged as typed.
judge_meson() {
    local -a args=("$@")
    local j=0 m=$#
    [ "${args[0]:-L}" = Ldevenv ] || return 0
    j=1
    while ((j < m)); do
        case "${args[j]:1}" in
            -C | -w | --workdir) j=$((j + 2)) ;;
            --dump | --dump-format) j=$((j + 2)) ;;
            -*) j=$((j + 1)) ;;
            *) break ;;
        esac
    done
    ((j < m)) && judge "${args[@]:j}"
    return 0
}

# pytest on the Harness tier: refused unless the same command names its card, because
# without one the nested Hyprland opens a window per test on the owner's desktop (F12).
judge_pytest() {
    local arg text harness=0
    for arg in "$@"; do
        text=${arg:1}
        [[ $text == tests/integration* || $text == */tests/integration* ]] && harness=1
    done
    if ((harness)) && [ -z "$prefix_card" ]; then
        deny "The Harness tier (\`tests/integration\`) without \`HARNESS_DRM_CARD\` on the same command nests each test's Hyprland into the owner's desktop session, one host window per test. Name the card: \`HARNESS_DRM_CARD=/dev/dri/card0 timeout 900 .venv/bin/pytest tests/integration -m hyprland\` ($docs)."
    fi
    return 0
}

# `git stash` that writes: the stash is one stack shared by the main checkout and every
# worktree, and in effort #148 another agent's `stash pop` pulled changes into the wrong
# tree twice. `git stash list` and `git stash show` read and pass.
judge_git() {
    local -a args=("$@")
    local j=0 m=$# text sub
    while ((j < m)); do
        text=${args[j]:1}
        case "$text" in
            -C | -c | --git-dir | --work-tree | --namespace | --exec-path | --config-env) j=$((j + 2)); continue ;;
            -*) j=$((j + 1)); continue ;;
        esac
        break
    done
    ((j < m)) || return 0
    [ "${args[j]:1}" = stash ] || return 0
    sub=${args[j + 1]:-L}
    sub=${sub:1}
    [[ $sub =~ ^(list|show)$ ]] && return 0
    deny "\`git stash${sub:+ $sub}\` writes the one stash stack the main checkout and every worktree share: in effort #148 another agent's \`stash pop\` pulled changes into the wrong tree twice. Set work aside with a WIP commit on your own branch, then amend or reset it; \`git stash list\` and \`git stash show\` still read (docs/agents/local-checks.md, Worktrees; .claude/hooks/desktop-fence.sh)."
}

# Whether any of the words $@ (flag + text) holds pids found by name: a
# `$(pgrep …)`-like substitution, or a variable already filled from one.
names_pids() {
    local arg rest re='\$\{?([A-Za-z_][A-Za-z0-9_]*)'
    for arg in "$@"; do
        [ "${arg:0:1}" = X ] || continue
        rest=${arg:1}
        [[ $rest =~ $re_pid_sub ]] && return 0
        while [[ $rest =~ $re ]]; do
            [[ $pid_vars == *" ${BASH_REMATCH[1]} "* ]] && return 0
            rest=${rest#*"${BASH_REMATCH[0]}"}
        done
    done
    return 1
}

# `kill`: refused for pids found by name and for a process group.
judge_kill() {
    local -a args=("$@")
    local j=0 m=$# text why="" opts=1
    while ((j < m)); do
        text=${args[j]:1}
        if ((opts)); then
            opts=0
            case "$text" in
                --) ;;
                -s | -n | --signal)
                    j=$((j + 1))
                    [ "${args[j]:-L}" = L0 ] && return 0 # signal 0 only asks whether it lives
                    ;;
                -l | -L | --list | --table | -0) return 0 ;;
                -*) ;; # the signal
                *) continue ;;
            esac
            j=$((j + 1))
            continue
        fi
        if [ "${args[j]:0:1}" = L ]; then
            [[ $text == 0 || $text =~ ^-[0-9]+$ ]] \
                && why="\`$text\` signals a whole process group (\`-1\` every process you may signal), the owner's desktop session among them"
        elif names_pids "${args[j]}"; then
            why="on pids found by name (\`$text\`) kills every match across the whole session, not only your own"
        fi
        [ -n "$why" ] && break
        j=$((j + 1))
    done
    if [ -z "$why" ] && ((piped)) && [[ $pipeline =~ $re_pid_finder ]]; then
        why="on pids piped from \`${BASH_REMATCH[1]}\` kills every match across the whole session, not only your own"
    fi
    [ -n "$why" ] && deny "\`kill\` $why, so it can kill the owner's desktop compositor, terminal or apps. $kill_shape ($docs)."
    return 0
}

# A shell that runs a command line: `-c '…'` or a heredoc on stdin is judged in turn.
judge_shell() {
    local -a args=("$@")
    local j=0 m=$# text has_c=0 body
    while ((j < m)); do
        text=${args[j]:1}
        case "$text" in
            --) j=$((j + 1)); break ;;
            -o | +o | -O | +O | --rcfile | --init-file) j=$((j + 1)) ;;
            --*) ;;
            -*c*) has_c=1 ;;
            [-+]*) ;;
            *) break ;;
        esac
        j=$((j + 1))
    done
    if ((has_c)); then
        ((j < m)) && judge_command "${args[j]:1}"
    elif ((j >= m)); then
        for body in "${bodies[@]}"; do judge_command "$body"; done
    fi
    return 0
}

# Python: a GTK load outside the probe route, a fenced command run through
# subprocess, an X file removed, or the app run directly.
judge_python() {
    local -a args=("$@")
    local j=0 m=$# text code="" module="" script="" k ch value
    while ((j < m)); do
        text=${args[j]:1}
        case "$text" in
            --)
                script=${args[j + 1]:-L}
                script=${script:1}
                break
                ;;
            -) script=-; break ;;
            --*) ;;
            -*) # a cluster of short options: `-Ic`, `-um`, `-mhyprtweaker`
                for ((k = 1; k < ${#text}; k++)); do
                    ch=${text:k:1}
                    case "$ch" in
                        c | m)
                            value=${text:k+1}
                            if [ -z "$value" ]; then
                                j=$((j + 1))
                                value=${args[j]:-L}
                                value=${value:1}
                            fi
                            if [ "$ch" = c ]; then code=$value; else module=$value; fi
                            break 2
                            ;;
                        W | X)
                            [ -z "${text:k+1}" ] && j=$((j + 1))
                            break
                            ;;
                    esac
                done
                ;;
            *) script=$text; break ;;
        esac
        j=$((j + 1))
    done
    # Code on stdin: a heredoc or here-string of this command's own, or a pipe into it.
    if [ -z "$code$module" ] && { [ -z "$script" ] || [ "$script" = - ]; }; then
        if ((${#bodies[@]})); then
            code=$(printf '%s\n' "${bodies[@]}")
        elif ((piped)); then
            code=$pipeline_text
        fi
    fi
    if [[ $code =~ $re_gtk ]]; then
        deny "\`python -c\` or stdin code that loads GTK (\`${BASH_REMATCH[0]}\`) starts GTK on the session's own GDK_BACKEND and WAYLAND_DISPLAY, so its window maps on the owner's desktop. Run it as a widget probe, whose first line is \`import widget_probe\`: \`.venv/bin/python tools/widget_probe.py <probe.py>\` ($docs)."
    fi
    if [[ $code =~ $re_py_run ]] && [[ $code =~ $re_py_word ]]; then
        deny "This python code runs \`${BASH_REMATCH[2]}\` through \`subprocess\` or \`os.system\`, which the shell fence refuses for reaching the owner's desktop session. Run it from the shell in the shape the fence lets through (\`$sig_shape …\`, the sandbox, pytest), so the fence can judge it ($docs)."
    fi
    if [[ $code =~ $re_py_unlink ]] && [[ $code =~ $re_py_xfile ]]; then
        deny "This python code removes, moves or links \`${BASH_REMATCH[0]}\`, the desktop's X sockets or locks: the owner's Xwayland listens on /tmp/.X11-unix/X0. Reading them is the whole of an agent's business there ($xdocs)."
    fi
    if [[ $script =~ (^|/)tools/sandbox\.py$ ]]; then
        local arg
        for arg in "${args[@]:j+1}"; do
            [[ ${arg:1} == --window ]] && deny "\`tools/sandbox.py --window\` is the owner's interactive mode: the nested Hyprland opens as a window on the focused workspace of the owner's desktop. Run it windowless, as it is by default: \`.venv/bin/python tools/sandbox.py --shot <png>\` ($docs)."
        done
    fi
    [ "$module" = pytest ] && judge_pytest "${args[@]:j+1}"
    local app=""
    if [[ $module == hyprtweaker || $module == hyprtweaker.* ]]; then
        app="python -m $module"
    elif [[ $script =~ (^|/)src/hyprtweaker(/|$) ]]; then
        app="python $script"
    elif [[ ${code//$'\n'/ } =~ $re_py_app ]]; then
        app="python code that runs the app"
    fi
    if [ -n "$app" ]; then
        deny "\`$app\` runs the app against the session's own WAYLAND_DISPLAY, so its window maps on the owner's desktop and its config writes reach the owner's real config. Run it windowless in a nested Hyprland: \`.venv/bin/python tools/sandbox.py\` ($docs)."
    fi
}

# Removing, moving or linking the session's X sockets and locks. The words after the
# command name are matched as paths wherever they stand, so a flag or a target counts.
judge_x_files() {
    local name=$1 arg text hit="" path pre alt
    local -a paths alts
    shift
    for arg in "$@"; do
        text=${arg:1}
        paths=("$text")
        if [[ $text =~ ^([^{]*)\{([^}]*)\}(.*)$ ]]; then # one level of brace expansion
            pre=${BASH_REMATCH[1]}
            IFS=, read -r -a alts <<< "${BASH_REMATCH[2]}"
            for alt in "${alts[@]}"; do paths+=("$pre$alt${BASH_REMATCH[3]}"); done
        fi
        for path in "${paths[@]}"; do
            if [[ $path =~ $re_x_dir || $path =~ $re_x_lock || $path =~ $re_x_glob || $path =~ $re_x_above ]]; then
                hit=$path
                break 2
            fi
        done
    done
    [ -n "$hit" ] || return 0
    if [ "$name" = find ]; then # a find that only lists or reads is a read
        local writes=0 exec=0
        for arg in "$@"; do
            text=${arg:1}
            if ((exec)); then
                exec=0
                [[ ${text##*/} =~ ^(rm|unlink|rmdir|mv|ln|shred)$ ]] && writes=1
            fi
            case "$text" in
                -delete) writes=1 ;;
                -exec | -execdir | -ok | -okdir) exec=1 ;;
            esac
        done
        ((writes)) || return 0
    fi
    deny "\`$name\` on \`$hit\` removes, moves or links the desktop's X sockets or locks: the owner's Xwayland listens on /tmp/.X11-unix/X0, and a change there cuts off every X11 app the owner starts next (2026-10-02). Reading them (\`ls /tmp/.X11-unix\`, \`ss -xlp\`) is the whole of an agent's business there; an agent's own X server takes a private display through \`start_xvfb\` ($xdocs)."
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

# A Wayland client that types, copies, captures or picks on WAYLAND_DISPLAY: $1 the
# command, $2 the WAYLAND_DISPLAY word of its prefix.
judge_wayland_client() {
    local why
    if [ -z "$2" ]; then
        why="it follows WAYLAND_DISPLAY, which is the session's (\`${WAYLAND_DISPLAY:-unset}\`)"
    else
        why=$(not_nested_display "$2")
        [ -z "$why" ] && return 0
    fi
    deny "\`$1\` would act on the owner's desktop session: $why. Name a nested display: \`WAYLAND_DISPLAY=<display printed by tools/sandbox.py> $1 …\` ($docs)."
}

# The first operand among the words $2… (flag + text): the subcommand. $1 lists the
# options that take the next word as their value, space-delimited.
first_operand() {
    local values=$1 text
    shift
    while (($#)); do
        text=${1:1}
        shift
        if [[ $values == *" $text "* ]]; then
            (($#)) && shift
        elif [[ $text != -* ]]; then
            printf '%s' "$text"
            return
        fi
    done
}

# `xdotool` types into DISPLAY: only a private display (200-999) written out passes.
judge_xdotool() {
    local flag=${1:0:1} d=${1:1} why number session
    session=${DISPLAY:-}
    session=${session##*:}
    session=${session%%.*}
    if [ -z "$1" ]; then
        why="it follows DISPLAY, which is the session's (\`${DISPLAY:-unset}\`, the desktop's Xwayland)"
    elif [ "$flag" = X ]; then
        why="\`$d\` is expanded only when the command runs, so the hook cannot tell which display it names"
    elif [[ $d =~ ^:([0-9]+)(\.[0-9]+)?$ ]] && number=$((10#${BASH_REMATCH[1]})) && ((number >= 200 && number <= 999)); then
        [ "$number" != "$session" ] && return 0
        why="\`$d\` is the session's own DISPLAY"
    else
        why="\`$d\` is not a private display (:200 to :999)"
    fi
    deny "\`xdotool\` would type or click into the owner's desktop session: $why. Name a private display written out, \`DISPLAY=:<200-999> xdotool …\`, served by an Xvfb that \`start_xvfb\` started ($xdocs)."
}

level=0
judge_command "$cmd"
exit 0
