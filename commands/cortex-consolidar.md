---
description: Revisão crítica completa da memória Cortex — funde, corrige, poda e registra a evolução
allowed-tools: Bash, Read, Write, Grep, Glob
---

Faça a revisão profunda da memória Cortex seguindo a seção "Revisão periódica" da
skill `cortex`. Não é um resumo: é curadoria. Espera-se que você **remova e reescreva**
regras, não só acrescente.

Comece por `~/.claude/cortex/bin/cortex status` e
`~/.claude/cortex/bin/cortex list --status active`.

Depois, rode `~/.claude/cortex/bin/atos.py destilar` e leia `~/.claude/cortex/trilhas.md`:
transições fortes e repetidas viram regra `workflow` (com a evidência); classificação errada vira
padrão em `~/.claude/cortex/atos-regras.json`; forma de resposta que deixou de valer é trocada ali.

Ao final, atualize o journal do mês e me diga, em português, qual a **direção da
evolução** — o que mudou no meu modo de pensar/trabalhar desde a última consolidação.
