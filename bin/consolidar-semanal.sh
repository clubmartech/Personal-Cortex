#!/bin/zsh
# Consolidacao semanal do Cortex (disparada pelo LaunchAgent local.cortex.consolidar, ver launchd/).
# Roda o /cortex-consolidar em modo nao-interativo, na propria maquina, com acesso ao store local.
# Trocar de modelo: defina CORTEX_MODEL (padrao abaixo).

# launchd nao herda o PATH do shell: inclua onde o `claude` CLI estiver instalado
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
CORTEX="$HOME/.claude/cortex"
LOG="$CORTEX/journal/_agendador.log"
RUNLOG="$CORTEX/journal/run-log.jsonl"
MODEL="${CORTEX_MODEL:-claude-opus-5-5}"
BIN="$CORTEX/bin/cortex"
START=$(date +%s)
ATIVAS=0
CAND=0

# registra uma linha por rodada em run-log.jsonl (duracao + resultado), pra nao
# descobrir so pelo log de texto que a curadoria semanal parou de funcionar.
registrar_run() {
  local outcome="$1"
  local dur=$(( $(date +%s) - START ))
  python3 -c "
import json, sys
json.dump({
    'run_id': '$(date -u '+%Y-%m-%dT%H:%M:%SZ')',
    'ativas_antes': $ATIVAS,
    'candidatas_antes': $CAND,
    'model': '$MODEL',
    'duration_s': $dur,
    'outcome': '$outcome',
}, sys.stdout)
print()
" >> "$RUNLOG"
}

mkdir -p "$CORTEX/journal"
exec >> "$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') consolidacao semanal ==="

if [ ! -s "$CORTEX/store.jsonl" ]; then
  echo "store vazio — nada a consolidar, saindo."
  registrar_run "store_vazio"
  exit 0
fi

ATIVAS=$(python3 "$CORTEX/bin/cortex.py" list --status active | wc -l | tr -d ' ')
CAND=$(python3 "$CORTEX/bin/cortex.py" list --status candidate | wc -l | tr -d ' ')
if [ "$ATIVAS" -lt 3 ] && [ "$CAND" -eq 0 ]; then
  echo "memoria pequena demais ($ATIVAS ativas, 0 candidatas) — pulando para nao gastar a toa."
  python3 "$CORTEX/bin/cortex.py" maintain --quiet
  registrar_run "pulado_memoria_pequena"
  exit 0
fi

cd "$CORTEX" || exit 1
command -v claude >/dev/null 2>&1 || {
  echo "ERRO: claude CLI nao encontrado no PATH"
  registrar_run "sem_claude_cli"
  exit 1
}

OUT=$(claude -p "/cortex-consolidar" \
  --model "$MODEL" \
  --allowedTools "Bash($BIN:*)" "Bash(python3:*)" "Read" "Write" "Edit" "Glob" "Grep" 2>&1)
STATUS=$?
echo "$OUT"

OUTCOME="ok"
case "$OUT" in
  *"Not logged in"*|*"Please run /login"*)
    OUTCOME="auth_falhou"
    echo ">>> ATENCAO: o CLI 'claude' nao esta autenticado para uso headless."
    echo ">>> Corrija uma vez no Terminal:  claude setup-token"
    echo ">>> (ou 'claude' e depois /login). A manutencao mecanica segue rodando abaixo."
    ;;
  *)
    [ "$STATUS" -ne 0 ] && OUTCOME="erro_status_$STATUS"
    ;;
esac
registrar_run "$OUTCOME"

# manutencao mecanica sempre roda, mesmo se a curadoria pelo modelo falhar
python3 "$CORTEX/bin/cortex.py" maintain --quiet
python3 "$CORTEX/bin/atos.py" destilar --quiet 2>/dev/null || true
echo "--- saida do cortex status ---"
python3 "$CORTEX/bin/cortex.py" status
echo "=== fim (exit=$STATUS) ==="

# mantem o log enxuto
tail -n 800 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
