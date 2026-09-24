#!/usr/bin/env bash
# Publish catalog onto main.
# skills/ is replaced from the pinned submodules.
# Every other path is merged, so files that exist only on main stay put.
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

if [[ ! -f .gitmodules ]]; then
  echo "error: .gitmodules not found; refusing to replace main with an empty catalog" >&2
  exit 1
fi

git submodule update --init --recursive --depth 1

catalog_sha=$(git rev-parse HEAD)
paths=()
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  paths+=("$path")
done < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' | awk '{print $2}' || true)

if [[ ${#paths[@]} -eq 0 ]]; then
  echo "error: .gitmodules lists no submodule paths" >&2
  exit 1
fi

for path in "${paths[@]}"; do
  case "$path" in
    /*|*..*) echo "error: refusing submodule path $path" >&2; exit 1 ;;
  esac
  if [[ ! -d "$repo_root/$path" ]]; then
    echo "error: submodule $path did not check out" >&2
    exit 1
  fi
  shopt -s nullglob
  skills=("$repo_root/$path"/skills/*/SKILL.md)
  shopt -u nullglob
  if [[ ${#skills[@]} -eq 0 ]]; then
    echo "error: $path has no skills/*/SKILL.md" >&2
    exit 1
  fi
  echo "expand $path (${#skills[@]} skills)"
done

if [[ -f "$(git rev-parse --git-path shallow)" ]]; then
  git fetch --unshallow origin
fi
git fetch --no-tags origin main

work=$(mktemp -d)
cleanup() {
  git worktree remove --force "$work" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

git worktree add --detach "$work" origin/main

# Merge catalog into main, then drop submodule pointers. skills/ is rewritten
# below and is not taken from either side's existing tree.
if ! git -C "$work" merge-base --is-ancestor "$catalog_sha" HEAD; then
  merge_err=$(mktemp)
  if ! git -C "$work" \
    -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    merge --no-commit --no-ff "$catalog_sha" 2>"$merge_err"; then
    echo "error: merging catalog into main failed." >&2
    cat "$merge_err" >&2
    git -C "$work" diff --name-only --diff-filter=U >&2 || true
    rm -f "$merge_err"
    exit 1
  fi
  rm -f "$merge_err"
  git -C "$work" rm -f --cached --ignore-unmatch -- .gitmodules
  rm -f "$work/.gitmodules"
  for path in "${paths[@]}"; do
    git -C "$work" rm -rf --cached --ignore-unmatch -- "$path"
    rm -rf "$work/$path"
  done
fi

rm -rf "$work/skills"
mkdir -p "$work/skills"
shopt -s nullglob
for path in "${paths[@]}"; do
  provider=$(basename "$path")
  for skill_dir in "$repo_root/$path"/skills/*/; do
    if [[ ! -f "${skill_dir}SKILL.md" ]]; then
      continue
    fi
    name=$(basename "$skill_dir")
    dest="$work/skills/$provider/$name"
    mkdir -p "$dest"
    rsync -a --exclude '.git' "${skill_dir}" "$dest/"
  done
done
shopt -u nullglob

shopt -s nullglob
root_skills=("$work"/skills/*/*/SKILL.md)
shopt -u nullglob
if [[ ${#root_skills[@]} -eq 0 ]]; then
  echo "error: skills/<provider>/<name>/ at the catalog root is empty" >&2
  exit 1
fi
echo "catalog skills/: ${#root_skills[@]}"

# The worktree's own .git sits at the root. Anything deeper is a submodule
# checkout that would be recorded as a gitlink.
if find "$work" -mindepth 2 -name '.git' -print -quit | grep -q .; then
  echo "error: expanded tree still contains a .git entry" >&2
  exit 1
fi

git -C "$work" add -A -- skills
if git -C "$work" diff --cached --quiet && ! git -C "$work" rev-parse -q --verify MERGE_HEAD >/dev/null; then
  echo "main already matches catalog $catalog_sha"
  exit 0
fi

gitlinks=$(git -C "$work" ls-files --stage | awk '$1 == "160000" { print }')
if [[ -n "$gitlinks" ]]; then
  echo "error: refusing to publish a gitlink; expansion did not materialize files" >&2
  printf '%s\n' "$gitlinks" >&2
  exit 1
fi

git -C "$work" \
  -c user.name='github-actions[bot]' \
  -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
  commit --quiet --allow-empty -m "$(cat <<EOF
Publish skills from catalog.

Replace skills/ with the submodules pinned at ${catalog_sha}. Merge every other path.

[skip ci]
EOF
)"

if [[ "${DRY_RUN:-}" == 1 ]]; then
  echo "DRY_RUN: would publish $(git -C "$work" rev-parse --short HEAD) onto main."
  git -C "$work" ls-tree --name-only HEAD
  echo "files: $(git -C "$work" ls-files | wc -l | tr -d ' ')"
  echo "root skills: $(git -C "$work" ls-files 'skills/*/*/SKILL.md' | wc -l | tr -d ' ')"
  exit 0
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "error: GITHUB_TOKEN is required to push main" >&2
  exit 1
fi

header=$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')
git -C "$work" \
  -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${header}" \
  push origin HEAD:main

echo "Published catalog ${catalog_sha} to main."
