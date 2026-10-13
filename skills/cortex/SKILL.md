---
name: cortex
description: Protocolo da memória evolutiva Cortex — captar, destilar, revisar e propagar aprendizados sobre como o usuário pensa, direciona e evolui. Use quando for registrar/consolidar aprendizados, revisar a memória, resolver contradições entre regras, sincronizar o perfil com outras ferramentas (Codex, Gemini, Cursor, Antigravity, opencode) ou quando o usuário disser "cortex", "memória", "o que você aprendeu", "consolidar aprendizado".
allowed-tools: Bash, Read, Grep, Glob
---

# Cortex — protocolo de aprendizado contínuo

Objetivo: manter uma memória **pequena, verdadeira e crescente em qualidade**, não um log.
Toda regra precisa passar em três testes antes de existir:

1. **Durabilidade** — vale para a próxima sessão, não só para esta tarefa?
2. **Não-derivável** — não está no código, no git, no README nem no `CLAUDE.md` do projeto?
3. **Acionável** — muda o que eu faria por padrão?

Falhou em um? Não registre.

## Arquitetura

```
~/.claude/cortex/
  store.jsonl          ← FONTE DE VERDADE (única). Só o CLI escreve aqui.
  events.jsonl         ← trilha de auditoria do aprendizado
  profile.md           ← render: quem é / como pensa
  principles.md        ← render: o que virou lei
  projects/<slug>.md   ← render: comportamento por projeto
  EVOLUTION.md         ← render: linha do tempo do raciocínio (o que mudou e por quê)
  journal/YYYY-MM.md   ← narrativa livre (append-only, opcional)
  inbox/<app>.jsonl    ← entrada de outras ferramentas → absorvido pelo CLI
  bridge/              ← saída para outras ferramentas (CORTEX.md + cortex.json)
  bin/cortex           ← CLI
```

Os `.md` são **gerados**. Nunca edite à mão — a próxima renderização sobrescreve.

## Ciclo de vida de um aprendizado

```
sinal → candidato → (2ª ocorrência OU pedido explícito) → ativo
                                                    ↓
                     sem reforço em 120d e fraco → arquivado
                     contradito por regra nova   → substituído (supersede)
        candidato sem reforço em 30d → descartado (some do store)
```

Isso é o antilixo: nada entra por ser interessante, só por ser **reincidente ou pedido**.
E nada fica para sempre por inércia.

## Comandos

```bash
cortex observe "<regra>" [--kind K] [--scope global|project] [--project <path>]
                         [--why "<evidência>"] [--explicit] [--supersedes <id>]
cortex supersede <id_antigo> "<nova regra>" --why "<o que mudou>"
cortex forget <id> --reason "<motivo>"
cortex context [--project <path>]     # bloco injetado na sessão
cortex list [--status candidate|active|archived|superseded]
cortex status                          # saúde: sinal/ruído, consolidadas, atividade 7d
cortex maintain                        # absorb + promote + prune + sync
```

`--kind`: `identity` (quem é) · `preference` (gosto/formato) · `principle` (lei de
engenharia) · `workflow` (como conduz o trabalho) · `project` (fato do repositório)
· `reference` (onde fica a informação) · `correction` (lição de um erro).

## Como escrever uma boa regra

- **Uma frase imperativa**, sem contexto de sessão. Ruim: "hoje ele pediu para não
  usar emoji no README". Bom: "Não usar emoji em documentação técnica".
- **`--why` carrega a evidência**, não a justificativa retórica. É o que permite
  revisar depois se a regra ainda faz sentido.
- **Prefira reforçar a duplicar**: se já existe regra parecida, use o mesmo texto
  (o CLI deduplica por normalização e incrementa `seen`).

## Revisão periódica (o que fazer no `/cortex-consolidar`)

1. `cortex maintain` — mecânico: absorve, promove, poda, sincroniza.
2. `cortex list --status active` — leia tudo com olhos críticos:
   - **Contradições** → `cortex supersede`, registrando o porquê da mudança.
   - **Regras vagas** → substitua por uma versão acionável.
   - **Regras que viraram óbvias** (o modelo já faz sem lembrete) → `forget`.
   - **Regras que se sobrepõem** → funda em uma, mais forte.
3. `cortex list --status candidate` — algum candidato já é claramente verdade?
   Confirme com `observe --explicit`. Algum é ruído? `forget`.
4. Escreva 3–6 linhas em `~/.claude/cortex/journal/$(date +%Y-%m).md` sobre **a
   direção da evolução** (o que mudou no modo de pensar do usuário, não a lista de
   regras). Isso é o que dá leitura racional ao histórico.
5. Reporte: quantas regras entraram, saíram, mudaram — e o que isso indica.

## Trilhas de raciocínio (atos)

Além das regras, o Cortex mapeia **o tipo de cada pedido** — o ato — e a **sequência** entre atos em cada conversa: viabilidade, desenho, avaliação, execução, publicação, delegação, consulta, correção, preferência. Registra a linha de raciocínio, não o resultado. Motor: `bin/atos.py`.

| Camada | Quando | O quê |
|---|---|---|
| Captura | toda mensagem (hook `UserPromptSubmit`) | `atos.py hook-prompt` grava o ato em `atos/<projeto>.jsonl` e devolve `<cortex-ato>`: ato provável, ato anterior na conversa, forma esperada da resposta, o que costuma vir depois |
| Destilação | fim de sessão e consolidação semanal | `atos.py destilar` recalcula `trilhas.json` e `trilhas.md` (transições com 2+ ocorrências, por projeto e global) |
| Injeção | início de sessão (hook `SessionStart`) | `atos.py contexto` entrega `<cortex-trilhas>` (≤1.400 caracteres) |

Além do ato, cada pedido recebe:

- **domínio** (audiovisual, textual, código, dados, operação) — trilhas por domínio, sem misturar com a global;
- **riscos de entendimento** — referência vaga ("arruma isso"), pedidos múltiplos no mesmo turno;
- **provável digitação** — palavra nunca vista a 1 edição de um termo frequente do vocabulário real ("implemnta" ≈ "implementa"); a classificação usa o texto corrigido, o registro guarda o original;
- **pontos de falha aprendidos** — que tipo de pedido, sinal e domínio antecederam correções.

**Leitura de período** (`atos.py linha --desde AAAA-MM-DD [--dominio audiovisual]`): por conversa, objetivo inicial → degraus → desvios corrigidos → último pedido → **output final** (lido do transcript). Leia como linha de raciocínio — por que cada degrau veio e onde o entendimento se desviou —, não como resumo de conteúdo.

**Codex:** importação das sessões abertas por pessoa (as `codex exec` disparadas por script ficam fora), hook `--fonte codex` com o mesmo filtro, e ponte em `~/.codex/AGENTS.md` (bloco `CORTEX-TRILHAS`) com seções de conteúdo audiovisual e textual, atualizada a cada destilação.

Como usar o sinal:

- **Mire o degrau pedido.** Viabilidade pede sim/não, desenho curto e oferta do próximo passo — não a execução. Desenho pede proposta com decisões abertas — não código. Execução pede fazer e verificar.
- **Prever não é autorizar.** "Depois de desenho costuma vir execução" calibra a resposta; não permite executar antes do pedido.
- **Classificação é heurística.** Se errar de forma recorrente, acrescente padrão em `atos-regras.json` (`{"padroes": {"<ato>": ["regex"]}, "formas": {"<ato>": "…"}}`); se a forma esperada mudou, troque a forma ali.
- **Trilha estável vira regra.** Na consolidação, transições fortes e repetidas viram `cortex observe --kind workflow` com a evidência de `trilhas.md`.
- Os textos ficam só nesta máquina (`atos/`, fora de qualquer repositório).

## Multi-agente

`cortex sync` injeta o perfil entre marcadores `CORTEX:BEGIN/END` em
`~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`, `~/.config/opencode/AGENTS.md`,
`~/.cursor/rules/cortex.mdc`, `~/.antigravity/AGENTS.md` e `~/.agents/AGENTS.md`.
Conteúdo fora dos marcadores é preservado.

O caminho de volta é o `inbox/`: o bloco sincronizado ensina as outras ferramentas a
escreverem uma linha JSON em `~/.claude/cortex/inbox/<ferramenta>.jsonl`. Elas entram
como **candidatas** — ferramenta externa não promove regra sozinha, só propõe.

Para apps sem arquivo de instruções (Ollama, LM Studio, apps próprios), use
`~/.claude/cortex/bridge/cortex.json` como system prompt programático.

## Interface entre pessoa, negócio e tarefa

O Cortex pessoal guarda preferências de interação e decisões explícitas do interlocutor. Não é um perfil psicológico nem a voz editorial da marca. Registre uma crença somente quando declarada pelo usuário, com contexto e evidência; não infira crenças por profissão, comportamento ou assunto.

Fato da empresa pertence ao contexto canônico do projeto; orientação de execução pertence à skill; estado de uma tarefa pertence ao handoff. Vincule as fontes em vez de copiar o mesmo dado para os três lugares. Um pedido atual pode corrigir uma preferência anterior; preserve a evidência dessa substituição.

Ao redigir em nome da pessoa, consulte a ficha de voz autorizada do projeto. Não exponha memória pessoal em uma peça compartilhada nem use uma preferência da conversa como depoimento público. Para conversar, use `saida-executiva`; para transferir uma tarefa, `handoff-sessao`.

## Qual Cortex e qual fonte atualizar

Esta skill opera o Cortex pessoal: preferências e decisões explícitas do interlocutor. O Cortex dentro de um projeto organiza contexto, métodos e governança compartilhados e possui outro CLI. O nome em comum não implica duplicata nem autoriza fundir os armazenamentos. Identifique o sistema pelo caminho e pela finalidade antes de executar manutenção.

Por padrão, confira a fonte canônica antes de usar informação mutável; verifique evidência, data e escopo. Uma alteração durável corrige ou substitui a regra anterior, atualiza referências e regenera consumidores. Use o CLI pessoal para memória e os geradores do projeto para seu contexto. Não copie números do negócio para uma regra pessoal nem replique memórias privadas no Drive. O pedido atual pode corrigir uma preferência anterior; não infira uma nova crença.
