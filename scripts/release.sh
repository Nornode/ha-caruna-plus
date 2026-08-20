#!/usr/bin/env bash

# Release helper for the Caruna+ Home Assistant custom integration.
#
# What it does:
#   - Reads the current version from manifest.json and finds the latest tag.
#   - Collects commit and file-change information for the release range.
#   - Generates release notes using AI_RELEASE_SUMMARY_CMD, Copilot CLI,
#     OpenAI, or a draft made from commit subjects.
#   - Optionally updates the manifest, creates an annotated tag, and pushes the
#     current branch and tag to origin.
#
# How to use:
#   1. Run it from a clean release branch.
#   2. A locally authenticated Copilot CLI is used automatically when present;
#      otherwise set OPENAI_API_KEY or AI_RELEASE_SUMMARY_CMD if desired.
#   3. Execute: ./scripts/release.sh
#   4. Review the proposed tag and notes, then confirm the final prompt.
#
# Requirements: git and python3. Existing local or remote tags are protected,
# and confirmation is required before release changes are made and pushed.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

die() {
  echo "Error: $*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

prompt_default() {
  local prompt="$1"
  local default_value="$2"
  local answer

  read -r -p "$prompt [$default_value]: " answer
  echo "${answer:-$default_value}"
}

need git
need python3

MANIFEST_CANDIDATES=()
while IFS= read -r manifest; do
  MANIFEST_CANDIDATES+=("$manifest")
done < <(find custom_components -mindepth 2 -maxdepth 2 -type f -name manifest.json -print | sort)

if [ "${#MANIFEST_CANDIDATES[@]}" -ne 1 ]; then
  die "Expected exactly one custom integration manifest under custom_components; found ${#MANIFEST_CANDIDATES[@]}"
fi
MANIFEST="${MANIFEST_CANDIDATES[0]}"

VERSION="$(python3 - "$MANIFEST" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as manifest:
    print(json.load(manifest)["version"])
PY
)"

DEFAULT_TAG="v$VERSION"
CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || true)"
[ -n "$CURRENT_BRANCH" ] || die "Releases must be made from a branch, not a detached HEAD."

echo "Preparing release from $CURRENT_BRANCH."
echo "Manifest version: $VERSION"
echo

git fetch --tags origin >/dev/null 2>&1 || echo "Warning: could not fetch tags from origin; using local tags only." >&2

LATEST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [ -n "$LATEST_TAG" ]; then
  echo "Latest reachable tag: $LATEST_TAG"
else
  echo "No existing tags found. The release summary will use the full history."
fi

echo
NEW_TAG="$(prompt_default "New tag" "$DEFAULT_TAG")"
PREVIOUS_TAG="$(prompt_default "Compare from tag" "${LATEST_TAG:-<root>}")"

[[ "$NEW_TAG" =~ ^v20[0-9]{2}\.(0[1-9]|1[0-2])\.[0-9]{2}([-+][0-9A-Za-z.-]+)?$ ]] || die "Tag must look like v2026.08.01; got $NEW_TAG"
RELEASE_VERSION="${NEW_TAG#v}"

if [ "$PREVIOUS_TAG" = "<root>" ]; then
  RANGE="HEAD"
  DIFF_ARGS=("$(git hash-object -t tree /dev/null)" "HEAD")
else
  git rev-parse -q --verify "refs/tags/$PREVIOUS_TAG" >/dev/null || die "Previous tag does not exist locally: $PREVIOUS_TAG"
  RANGE="$PREVIOUS_TAG..HEAD"
  DIFF_ARGS=("$RANGE")
fi

if git rev-parse -q --verify "refs/tags/$NEW_TAG" >/dev/null; then
  die "Local tag already exists: $NEW_TAG"
fi

if git ls-remote --exit-code --tags origin "refs/tags/$NEW_TAG" >/dev/null 2>&1; then
  die "Remote tag already exists on origin: $NEW_TAG"
fi

if [ -n "$(git status --porcelain)" ]; then
  die "Working tree has uncommitted changes. Commit or stash them before releasing."
fi

if [ "$VERSION" != "$RELEASE_VERSION" ]; then
  echo "Manifest will be updated from $VERSION to $RELEASE_VERSION before tagging."
fi

COMMITS_FILE="$(mktemp)"
CHANGES_FILE="$(mktemp)"
PROMPT_FILE="$(mktemp)"
SUMMARY_FILE="$(mktemp)"
cleanup() {
  rm -f "$COMMITS_FILE" "$CHANGES_FILE" "$PROMPT_FILE" "$SUMMARY_FILE"
}
trap cleanup EXIT

git log --no-merges --pretty=format:'- %s (%h)' "$RANGE" > "$COMMITS_FILE"
git diff --stat "${DIFF_ARGS[@]}" > "$CHANGES_FILE"

if [ ! -s "$COMMITS_FILE" ]; then
  die "No commits found in range $RANGE"
fi

cat > "$PROMPT_FILE" <<EOF
Write concise release notes for $NEW_TAG of a Home Assistant custom integration.

Focus on the largest user-facing changes between ${PREVIOUS_TAG} and $NEW_TAG.
Return only 3 to 6 markdown bullet points. Do not invent changes.

Commit subjects:
$(cat "$COMMITS_FILE")

Changed files summary:
$(cat "$CHANGES_FILE")
EOF

generate_with_openai() {
  python3 - "$PROMPT_FILE" <<'PY'
import json
import os
import sys
import urllib.request

prompt_path = sys.argv[1]
api_key = os.environ["OPENAI_API_KEY"]
model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
with open(prompt_path, encoding="utf-8") as prompt_file:
    prompt = prompt_file.read()

payload = {
    "model": model,
    "input": prompt,
    "temperature": 0.2,
}
request = urllib.request.Request(
    "https://api.openai.com/v1/responses",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    method="POST",
)

with urllib.request.urlopen(request, timeout=60) as response:
    data = json.load(response)

text = data.get("output_text", "").strip()
if not text:
    parts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                parts.append(content.get("text", ""))
    text = "\n".join(parts).strip()

if not text:
    raise SystemExit("OpenAI response did not include release notes")

print(text)
PY
}

generate_with_copilot() {
  local prompt
  prompt="$(cat "$PROMPT_FILE")"

  # The standalone Copilot CLI uses its existing local authentication, so no
  # API key is required in the environment.
  if command -v copilot >/dev/null 2>&1; then
    copilot -p "$prompt"
    return
  fi

  # Support the GitHub CLI Copilot extension as an alternative installation.
  if command -v gh >/dev/null 2>&1 && gh copilot --help >/dev/null 2>&1; then
    gh copilot suggest "$prompt"
    return
  fi

  return 1
}

generate_summary() {
  if [ -n "${AI_RELEASE_SUMMARY_CMD:-}" ]; then
    eval "$AI_RELEASE_SUMMARY_CMD" < "$PROMPT_FILE"
    return
  fi

  if command -v copilot >/dev/null 2>&1 || {
    command -v gh >/dev/null 2>&1 && gh copilot --help >/dev/null 2>&1
  }; then
    echo "Using the locally installed Copilot CLI for release notes." >&2
    if generate_with_copilot; then
      return
    fi
    echo "Warning: Copilot CLI failed; trying the next configured provider." >&2
  fi

  if [ -n "${OPENAI_API_KEY:-}" ]; then
    generate_with_openai
    return
  fi

  echo "No AI provider configured; using commit subjects as a draft summary." >&2
  echo "Set OPENAI_API_KEY or AI_RELEASE_SUMMARY_CMD to generate AI release notes." >&2
  sed -n '1,6p' "$COMMITS_FILE"
}

generate_summary > "$SUMMARY_FILE"

echo
echo "Release notes for $NEW_TAG:"
echo "----------------------------------------"
cat "$SUMMARY_FILE"
echo "----------------------------------------"
echo

read -r -p "Update manifest if needed, create annotated tag $NEW_TAG, and push to origin? [y/N]: " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || die "Release cancelled."

if [ "$VERSION" != "$RELEASE_VERSION" ]; then
  python3 - "$MANIFEST" "$RELEASE_VERSION" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
version = sys.argv[2]
text = path.read_text(encoding="utf-8")
data = json.loads(text)
data["version"] = version
updated, count = re.subn(
    r'("version"\s*:\s*")[^"]*(")',
    rf'\g<1>{version}\2',
    text,
    count=1,
)
if count != 1:
    raise SystemExit(f"Could not update version in {path}")
json.loads(updated)
path.write_text(updated, encoding="utf-8")
PY
  git add "$MANIFEST"
  git commit -m "Bump version to $RELEASE_VERSION"
fi

git tag -a "$NEW_TAG" -F "$SUMMARY_FILE"
git push origin "$CURRENT_BRANCH"
git push origin "$NEW_TAG"

echo
echo "Pushed $CURRENT_BRANCH and $NEW_TAG to origin. The release workflow will use the tag annotation as the release description."
