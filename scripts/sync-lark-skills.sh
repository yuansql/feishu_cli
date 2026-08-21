#!/usr/bin/env bash
# Keep repo skills/ as source of truth; refresh Cursor/Hermes links.
# Usage:
#   scripts/sync-lark-skills.sh           # refresh .cursor/skills → ../../skills/*
#   scripts/sync-lark-skills.sh --local   # also point ~/.cursor/skills + ~/.hermes/skills at repo
#   scripts/sync-lark-skills.sh --from-builtin  # re-copy from Memento.app builtin (catalog only)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_DIR="$ROOT/skills"
CURSOR_PROJ="$ROOT/.cursor/skills"
BUILTIN="${MEMENTO_BUILTIN_SKILLS:-/Applications/Memento-S.app/Contents/Resources/python/_internal/builtin/skills}"
LOCAL=0
FROM_BUILTIN=0
for arg in "$@"; do
  case "$arg" in
    --local) LOCAL=1 ;;
    --from-builtin) FROM_BUILTIN=1 ;;
    -h|--help)
      echo "Usage: $0 [--local] [--from-builtin]"
      exit 0
      ;;
  esac
done

LIST=(
  lark-approval lark-apps lark-attendance lark-base lark-calendar lark-contact
  lark-doc lark-drive lark-event lark-im lark-mail lark-markdown lark-minutes
  lark-okr lark-openapi-explorer lark-shared lark-sheets lark-skill-maker
  lark-slides lark-task lark-vc lark-vc-agent lark-whiteboard lark-wiki
  lark-workflow-meeting-summary lark-workflow-standup-report
)

if [[ "$FROM_BUILTIN" == "1" ]]; then
  mkdir -p "$SKILLS_DIR"
  for name in "${LIST[@]}"; do
    src="$BUILTIN/$name"
    if [[ ! -f "$src/SKILL.md" ]]; then
      echo "MISSING builtin $name" >&2
      continue
    fi
    rm -rf "$SKILLS_DIR/$name"
    cp -R "$src" "$SKILLS_DIR/$name"
    echo "copied $name"
  done
fi

mkdir -p "$CURSOR_PROJ"
missing=0
for name in "${LIST[@]}"; do
  if [[ ! -f "$SKILLS_DIR/$name/SKILL.md" ]]; then
    echo "MISSING $SKILLS_DIR/$name" >&2
    missing=$((missing + 1))
    continue
  fi
  link="$CURSOR_PROJ/$name"
  rm -rf "$link"
  ln -s "../../skills/$name" "$link"
done

if [[ "$LOCAL" == "1" ]]; then
  mkdir -p "$HOME/.cursor/skills" "$HOME/.hermes/skills"
  for name in "${LIST[@]}"; do
    [[ -f "$SKILLS_DIR/$name/SKILL.md" ]] || continue
    for dest in "$HOME/.cursor/skills/$name" "$HOME/.hermes/skills/$name"; do
      rm -rf "$dest"
      ln -s "$SKILLS_DIR/$name" "$dest"
    done
  done
  echo "local Cursor + Hermes → $SKILLS_DIR"
fi

echo "project .cursor/skills → $SKILLS_DIR (missing=$missing)"
exit "$missing"
