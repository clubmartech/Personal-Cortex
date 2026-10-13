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

1. O `install.sh` mescla os hooks de `exemplos/settings.hooks.json` no `~/.claude/settings.json` (sem duplicar, com backup `.bak-cortex`; `CORTEX_SEM_HOOKS=1` desliga) e semeia as trilhas com o histórico local (`CORTEX_SEM_HISTORICO=1` desliga).
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

## Trilhas de raciocínio — instalado por padrão

Além de regras, o Cortex mapeia **o tipo de cada pedido** (viabilidade, desenho, avaliação, execução, publicação, delegação, consulta, correção, preferência) e **a sequência** entre eles em cada conversa. O objetivo é a próxima resposta mirar o degrau certo: uma pergunta de viabilidade recebe desenho e oferta, não execução; um desenho aprovado costuma virar execução — e o assistente fica pronto, sem se antecipar.

- **Captura** em toda mensagem (`atos.py hook-prompt`, ~50 ms, sem modelo), **destilação** no fim da sessão e na consolidação semanal, **injeção** no início da sessão (`<cortex-trilhas>`) e a cada pedido (`<cortex-ato>`).
- **Semente:** o `install.sh` importa o histórico local de conversas do Claude Code (`~/.claude/projects`) para as trilhas valerem desde o primeiro dia. `CORTEX_SEM_HISTORICO=1` desliga.
- **Regra de ouro:** prever não é autorizar. A trilha calibra a resposta; nunca executa o próximo passo sem pedido.
- **Privacidade:** textos e trilhas ficam em `~/.claude/cortex/atos/` e `trilhas.*`, nesta máquina; o `.gitignore` deste repositório os exclui.
- **Previsão de falha:** cada pedido leva domínio, riscos de entendimento (referência vaga, pedidos múltiplos) e provável erro de digitação contra o vocabulário real da pessoa; a classificação lê a intenção corrigida. As correções ensinam onde o entendimento costuma falhar.
- **Leitura de período:** `atos.py linha --desde AAAA-MM-DD [--dominio audiovisual|textual]` monta, por conversa, objetivo → degraus → desvios → output final.
- **Codex:** importa as sessões abertas por pessoa (ignora `codex exec` disparado por script), instala o hook `--fonte codex` em `~/.codex/hooks.json` e mantém o bloco `CORTEX-TRILHAS` no `~/.codex/AGENTS.md`, com seções de conteúdo audiovisual e textual. `CORTEX_SEM_CODEX=1` desliga.
- **Ajuste:** `atos-regras.json` acrescenta padrões e troca a forma esperada de cada ato. `atos.py classificar "texto"` mostra como um pedido seria lido.

Linha sugerida para o `~/.claude/CLAUDE.md`:

```markdown
## Trilhas de raciocínio (Cortex)
`<cortex-trilhas>` (início da sessão) e `<cortex-ato>` (a cada pedido) dizem o tipo do pedido, o anterior e o que costuma vir depois. Responda no degrau pedido, com a forma esperada; use a previsão para estar pronto, nunca para agir antes do pedido. Se o ato estiver mal classificado, siga o pedido real.
```

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
| `bin/atos.py` | trilhas de raciocínio: captura do ato por pedido, destilação das transições e injeção |
| `bin/consolidar-semanal.sh` | curadoria semanal não interativa (`claude -p /cortex-consolidar`) + manutenção |
| `bin/chat-memoria.py` | opcional, Claude Code Desktop no macOS: memória e título por chat |
| `skills/cortex/SKILL.md` | protocolo completo de captura, destilação e revisão |
| `commands/cortex-*.md` | `/cortex-loop`, `/cortex-consolidar`, `/cortex-status` |
