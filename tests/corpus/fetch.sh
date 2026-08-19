#!/usr/bin/env bash
# Re-fetch the hyprlang rice corpus at the commits pinned in corpus.lock.json.
#
#   tests/corpus/fetch.sh            # fetch all remote rices
#   tests/corpus/fetch.sh hyde ml4w  # fetch a subset
#   tests/corpus/fetch.sh --local    # (re)capture ~/.config/hypr into tests/corpus/local/
#
# Requirements: git >= 2.25 (sparse-checkout), jq, rsync.
# Clones are shallow-ish (blob:none + sparse) and land in $CORPUS_WORK
# (default: a temp dir) — never inside the repo.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LOCK="$HERE/corpus.lock.json"
WORK=${CORPUS_WORK:-$(mktemp -d -t hypr-corpus.XXXXXX)}
MAX_SIZE=${CORPUS_MAX_SIZE:-200k}

# Files that are never part of the hyprland.conf tree we care about.
RSYNC_FILTERS=(
  --exclude='.git/'
  --exclude='*.png' --exclude='*.jpg' --exclude='*.jpeg' --exclude='*.gif'
  --exclude='*.webp' --exclude='*.mp4' --exclude='*.ttf' --exclude='*.otf'
  --exclude='.wallpaper_*' --exclude='wallpaper_effects/'
  --exclude='hyprlock*' --exclude='hypridle*' --exclude='hyprpaper*' --exclude='hyprsunset*'
  --exclude='*.db' --exclude='*.bak' --exclude='*.backup*' --exclude='*.save'
  --exclude='.gitignore'   # upstream ignore files would hide corpus files from our git
)

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need git; need jq; need rsync

copy_tree() { # src dst [extra rsync --exclude args...]
  local src=$1 dst=$2; shift 2
  mkdir -p "$dst"
  rsync -a --delete "${RSYNC_FILTERS[@]}" "$@" --max-size="$MAX_SIZE" "$src"/ "$dst"/
}

report_omitted() { # src dst label
  # list files present upstream but not copied (so the README can mention them)
  local a b
  a=$(cd "$1" && find . -type f -not -path './.git/*' | sort)
  b=$(cd "$2" && find . -type f | sort)
  local omitted
  omitted=$(comm -23 <(echo "$a") <(echo "$b") || true)
  if [[ -n "$omitted" ]]; then
    printf '  omitted from %s:\n' "$3"
    printf '%s\n' "$omitted" | sed 's/^/    /'
  fi
}

fetch_rice() {
  local rice=$1
  local repo commit root
  repo=$(jq -r ".\"$rice\".repo" "$LOCK")
  commit=$(jq -r ".\"$rice\".commit" "$LOCK")
  root=$(jq -r ".\"$rice\".root" "$LOCK")
  [[ "$repo" == "null" ]] && { echo "$rice: local capture, use --local"; return; }

  local clone="$WORK/$rice" dst="$HERE/$rice"
  echo "== $rice  $repo @ ${commit:0:10}  ($root)"
  mapfile -t paths < <(jq -r ".\"$rice\" | [.root] + (.extra|keys) | .[]" "$LOCK")

  if [[ ! -d "$clone/.git" ]]; then
    git clone -q --filter=blob:none --no-checkout --sparse "$repo" "$clone"
  fi
  git -C "$clone" sparse-checkout set --no-cone "${paths[@]/#//}"
  git -C "$clone" fetch -q --depth=1 origin "$commit" 2>/dev/null || true
  git -C "$clone" checkout -q "$commit"

  # keep hand-written corpus notes across re-fetches
  local keep; keep=$(mktemp -d)
  for f in ROOT NOTES.md; do [[ -f "$dst/$f" ]] && cp "$dst/$f" "$keep/"; done
  rm -rf "$dst"
  mapfile -t excl < <(jq -r ".\"$rice\".exclude // [] | .[] | \"--exclude=\" + ." "$LOCK")
  copy_tree "$clone/$root" "$dst" "${excl[@]}"
  report_omitted "$clone/$root" "$dst" "$root"

  # extra trees (things sourced from outside ~/.config/hypr) -> <rice>/_home/<rel>
  while IFS=$'\t' read -r src rel; do
    [[ -z "$src" ]] && continue
    if [[ -d "$clone/$src" ]]; then
      copy_tree "$clone/$src" "$dst/_home/$rel"
      report_omitted "$clone/$src" "$dst/_home/$rel" "$src"
    else
      mkdir -p "$dst/_home/$(dirname "$rel")"
      cp "$clone/$src" "$dst/_home/$rel"
    fi
  done < <(jq -r ".\"$rice\".extra | to_entries[] | \"\(.key)\t\(.value)\"" "$LOCK")

  cp -n "$keep"/* "$dst"/ 2>/dev/null || true
  rm -rf "$keep"
  echo "   $(find "$dst" -type f | wc -l) files, $(du -sh "$dst" | cut -f1)"
}

# Capture this machine's ~/.config/hypr: hyprland.conf plus everything reachable
# through source= (recursively, resolving $HOME/~/$config/relative), sanitised.
capture_local() {
  local dst="$HERE/local" hyprdir="$HOME/.config/hypr"
  local keep; keep=$(mktemp -d)
  for f in ROOT NOTES.md; do [[ -f "$dst/$f" ]] && cp "$dst/$f" "$keep/"; done
  rm -rf "$dst"; mkdir -p "$dst"
  declare -A seen vars
  vars[HOME]=$HOME
  local queue=("$hyprdir/hyprland.conf")
  while ((${#queue[@]})); do
    local f=${queue[0]}; queue=("${queue[@]:1}")
    f=$(realpath -m "$f")
    [[ -n "${seen[$f]:-}" ]] && continue
    seen[$f]=1
    [[ -f "$f" ]] || { echo "  (missing) $f"; continue; }
    # remember $name = value assignments for path expansion (best-effort)
    while IFS= read -r line; do
      if [[ $line =~ ^\$([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
        vars[${BASH_REMATCH[1]}]=${BASH_REMATCH[2]%%#*}
      fi
    done < "$f"
    # queue sourced files
    while IFS= read -r src; do
      src=${src%%#*}; src=$(echo "$src" | sed -E 's/^\s*source\s*=\s*//; s/\s+$//')
      src=${src/#\~/$HOME}
      for _ in 1 2 3; do  # nested vars, e.g. $config = $HOME/.config
        for k in "${!vars[@]}"; do src=${src//\$$k/${vars[$k]}}; done
      done
      src=${src/#\~/$HOME}
      if [[ $src == /* ]]; then queue+=("$src")
      else queue+=("$(dirname "$f")/$src"); fi
    done < <(grep -E '^\s*source\s*=' "$f" || true)
    # copy with sanitised path
    local rel
    if [[ $f == "$hyprdir"/* ]]; then rel=${f#"$hyprdir"/}
    elif [[ $f == "$HOME"/* ]]; then rel="_home/${f#"$HOME"/}"
    else rel="_abs${f}"; fi
    mkdir -p "$dst/$(dirname "$rel")"
    sed -e "s#$HOME#~#g" -e "s#$USER#user#g" "$f" \
      | grep -viE 'token|secret|passw(or)?d|api[_-]?key' > "$dst/$rel"
    echo "  $rel"
  done
  cp -n "$keep"/* "$dst"/ 2>/dev/null || true
  rm -rf "$keep"
}

if [[ "${1:-}" == "--local" ]]; then capture_local; exit 0; fi
if (($#)); then rices=("$@"); else
  mapfile -t rices < <(jq -r 'to_entries[] | select(.key|startswith("_")|not) | select(.value.repo != null) | .key' "$LOCK")
fi
for r in "${rices[@]}"; do fetch_rice "$r"; done
echo "clones kept in $WORK"
