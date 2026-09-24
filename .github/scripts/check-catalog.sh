#!/usr/bin/env bash
# Confirm every catalog submodule checks out and its skills lint.
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

if [[ ! -f .gitmodules ]]; then
  echo "error: .gitmodules not found" >&2
  exit 1
fi

git submodule update --init --recursive --depth 1

core=${CORE:-git+https://github.com/rhiza-research/weather-skills-core@main}
failed=0

while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  echo "::group::$path"
  if [[ ! -d "$repo_root/$path" ]]; then
    echo "error: submodule $path did not check out" >&2
    failed=1
    echo "::endgroup::"
    continue
  fi
  shopt -s nullglob
  skills=("$repo_root/$path"/skills/*/SKILL.md)
  shopt -u nullglob
  if [[ ${#skills[@]} -eq 0 ]]; then
    echo "error: $path has no skills/*/SKILL.md" >&2
    failed=1
    echo "::endgroup::"
    continue
  fi
  echo "${#skills[@]} skills"
  if ! uvx --from "$core" weather-skills-core lint "$repo_root/$path/skills"; then
    echo "error: weather-skills-core lint failed for $path" >&2
    failed=1
  fi
  echo "::endgroup::"
done < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' | awk '{print $2}' || true)

exit "$failed"
