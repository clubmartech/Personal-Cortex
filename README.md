# Personal Cortex — memória evolutiva do interlocutor

Memória de trabalho para assistentes de IA que **aprende como uma pessoa pensa, decide e corrige o rumo**, e entrega isso a cada sessão. É global (vale em todo projeto), tem camada por projeto e se propaga para outras ferramentas da máquina (Codex, Gemini, Cursor, opencode, Antigravity).

Este repositório traz só o **motor**: CLI, hooks, protocolo e comandos. A memória de cada pessoa (`store.jsonl` e os renders) fica na máquina dela e nunca entra aqui.

## Princípio

Memória pequena, verdadeira e crescente em qualidade — não um log. Uma regra só existe se for:

1. **Durável** — vale para a próxima sessão, não só para esta tarefa;
2. **Não-derivável** — não está no código, no git ou no README do projeto;
3. **Acionável** — muda o que o assistente faria por padrão.

Ciclo de vida: sinal → candidato → (2ª ocorrência ou pedido explícito) → ativo. Candidato sem reforço morre em 30 dias; regra fraca sem reforço vai para o arquivo em 120; regra contradita é substituída (`supersede`), nunca acumulada.

## Instalação

Requisitos: Python 3.10+, sem dependências. macOS ou Linux.

```bash
bash install.sh
```

Copia o motor para `~/.claude/cortex/bin/`, a skill para `~/.claude/skills/cortex/` e os comandos para `~/.claude/commands/`. Não sobrescreve dados existentes. Depois:

1. Acrescente os hooks de `exemplos/settings.hooks.json` ao `~/.claude/settings.json`.
2. Opcional: `export CORTEX_USER_NAME="Seu nome"` no perfil do shell (padrão: "o usuário").
3. Opcional, macOS: curadoria semanal com `launchd/local.cortex.consolidar.plist` (instruções no arquivo).
4. Acrescente ao `~/.claude/CLAUDE.md` quando registrar (tabela abaixo).

| Gatilho | Comando |
|---|---|
| "sempre / nunca / de agora em diante / prefiro" | `cortex observe "<regra>" --kind preference --explicit --why "<motivo>"` |
| correção de rumo | `cortex observe "<lição>" --kind correction --why "<o que deu errado>"` |
| padrão do projeto | `cortex observe "<fato>" --kind project --scope project --project "$PWD" --explicit` |
| mudança de opinião | `cortex supersede <id> "<nova regra>" --why "<o que mudou>"` |
| regra que ficou falsa | `cortex forget <id> --reason "<motivo>"` |

## Como o loop roda

| Camada | Quando | O quê |
|---|---|---|
| Injeção | início de sessão (hook `SessionStart`) | `cortex context` entrega perfil, princípios e regras do projeto atual |
| Captura | durante o trabalho | o assistente chama `observe/supersede/forget` |
| Destilação | `/cortex-loop`, sob demanda ou com `/loop` | separa sinal de ruído da sessão |
| Manutenção | fim de sessão (hook `SessionEnd`) | `cortex maintain` = absorb + promote + prune + sync |
| Curadoria | semanal, `/cortex-consolidar` | funde, corrige, poda e registra a direção da evolução |

## Comandos

```bash
cortex observe "<regra>" [--kind K] [--scope global|project] [--project <path>] [--why "..."] [--explicit]
cortex supersede <id> "<nova regra>" --why "..."
cortex forget <id> --reason "..."
cortex context [--project <path>]
cortex list [--status candidate|active|archived|superseded] [--project P]
cortex status
cortex maintain
```

`CORTEX_HOME` muda a pasta da memória (padrão `~/.claude/cortex`).

## Memória pessoal × Cortex de projeto

Este sistema guarda **como colaborar com a pessoa**. Fatos de um negócio, marca e voz pública pertencem ao contexto do projeto — por exemplo, a [distribuição Cortex de documentação](https://github.com/clubmartech/Cortex-Martech). São programas e dados diferentes; não funda as bases pelo nome.

## Componentes

| Arquivo | Função |
|---|---|
| `bin/cortex.py` | CLI: store, promoção, poda, render e sync |
| `bin/cortex` | atalho de shell para o CLI |
| `bin/consolidar-semanal.sh` | curadoria semanal não interativa (`claude -p /cortex-consolidar`) + manutenção |
| `bin/chat-memoria.py` | opcional, Claude Code Desktop no macOS: memória e título por chat |
| `skills/cortex/SKILL.md` | protocolo completo de captura, destilação e revisão |
| `commands/cortex-*.md` | `/cortex-loop`, `/cortex-consolidar`, `/cortex-status` |
