#!/usr/bin/env bash
# Instala o motor do Personal Cortex sem tocar em memória existente.
set -euo pipefail
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DEST="${CORTEX_HOME:-$HOME/.claude/cortex}"
mkdir -p "$DEST/bin" "$DEST/projects" "$DEST/journal" "$DEST/inbox" "$DEST/bridge" \
         "$HOME/.claude/skills/cortex" "$HOME/.claude/commands"
cp "$ORIGEM"/bin/cortex "$ORIGEM"/bin/cortex.py "$ORIGEM"/bin/consolidar-semanal.sh "$ORIGEM"/bin/chat-memoria.py "$DEST/bin/"
chmod +x "$DEST/bin/cortex" "$DEST/bin/cortex.py" "$DEST/bin/consolidar-semanal.sh"
cp "$ORIGEM/skills/cortex/SKILL.md" "$HOME/.claude/skills/cortex/SKILL.md"
for c in "$ORIGEM"/commands/cortex-*.md; do cp "$c" "$HOME/.claude/commands/"; done
[ -f "$DEST/store.jsonl" ] || : > "$DEST/store.jsonl"
python3 "$DEST/bin/cortex.py" status
echo
echo "Instalado em $DEST. Próximo passo: acrescentar os hooks de exemplos/settings.hooks.json ao ~/.claude/settings.json."
