#!/usr/bin/env bash
# Instala o motor do Personal Cortex sem tocar em memória existente.
set -euo pipefail
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DEST="${CORTEX_HOME:-$HOME/.claude/cortex}"
mkdir -p "$DEST/bin" "$DEST/projects" "$DEST/journal" "$DEST/inbox" "$DEST/bridge" \
         "$HOME/.claude/skills/cortex" "$HOME/.claude/commands"
cp "$ORIGEM"/bin/cortex "$ORIGEM"/bin/cortex.py "$ORIGEM"/bin/atos.py "$ORIGEM"/bin/consolidar-semanal.sh "$ORIGEM"/bin/chat-memoria.py "$DEST/bin/"
chmod +x "$DEST/bin/cortex" "$DEST/bin/cortex.py" "$DEST/bin/atos.py" "$DEST/bin/consolidar-semanal.sh"
mkdir -p "$DEST/atos"
cp "$ORIGEM/skills/cortex/SKILL.md" "$HOME/.claude/skills/cortex/SKILL.md"
for c in "$ORIGEM"/commands/cortex-*.md; do cp "$c" "$HOME/.claude/commands/"; done
[ -f "$DEST/store.jsonl" ] || : > "$DEST/store.jsonl"
python3 "$DEST/bin/cortex.py" status
# trilhas de raciocínio: semeia com o histórico local de conversas (só leitura; nada sai da máquina)
if [ -d "$HOME/.claude/projects" ] && [ "${CORTEX_SEM_HISTORICO:-0}" != "1" ]; then
  python3 "$DEST/bin/atos.py" importar "$HOME/.claude/projects" || true
fi
# hooks no ~/.claude/settings.json: mescla sem duplicar, com backup (CORTEX_SEM_HOOKS=1 desliga)
if [ "${CORTEX_SEM_HOOKS:-0}" != "1" ]; then
  python3 - "$ORIGEM/exemplos/settings.hooks.json" "$HOME/.claude/settings.json" <<'PY'
import json, os, shutil, sys
modelo, alvo = sys.argv[1], sys.argv[2]
novos = json.load(open(modelo, encoding="utf-8"))["hooks"]
atual = json.load(open(alvo, encoding="utf-8")) if os.path.exists(alvo) else {}
existentes = {h.get("command") for ev in (atual.get("hooks") or {}).values() for g in ev for h in g.get("hooks", [])}
add = 0
for evento, grupos in novos.items():
    for g in grupos:
        faltam = [h for h in g["hooks"] if h["command"] not in existentes]
        if faltam:
            atual.setdefault("hooks", {}).setdefault(evento, []).append(dict(g, hooks=faltam))
            add += len(faltam)
if add:
    if os.path.exists(alvo):
        shutil.copy2(alvo, alvo + ".bak-cortex")
    os.makedirs(os.path.dirname(alvo), exist_ok=True)
    tmp = alvo + ".tmp-cortex"
    json.dump(atual, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, alvo)
print("hooks: %d acrescentado(s) em %s%s" % (add, alvo, " (backup .bak-cortex)" if add else ""))
PY
fi
echo
echo "Instalado em $DEST. Reinicie o Claude Code para os hooks valerem."
