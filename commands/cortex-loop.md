---
description: Destila os aprendizados da sessão atual para a memória Cortex (feito para rodar com /loop)
allowed-tools: Bash, Read, Grep, Glob
---

Destile a sessão atual para a memória Cortex. Siga a skill `cortex`.

1. Releia o que aconteceu desde a última destilação nesta sessão e separe:
   - direcionamentos explícitos meus ("sempre", "nunca", "prefiro", correções de rumo);
   - padrões do meu raciocínio que se repetiram;
   - fatos do projeto atual que não estão no repositório;
   - regras existentes que esta sessão **contradisse**.
2. Aplique os três testes (durável, não-derivável, acionável). Descarte o resto sem dó.
3. Registre com `~/.claude/cortex/bin/cortex observe|supersede|forget`. Use
   `--scope project --project "$PWD"` para o que é do projeto.
4. Rode `~/.claude/cortex/bin/cortex maintain`.
5. Responda em no máximo 5 linhas: o que entrou, o que mudou, o que foi descartado.
   **Se nada passou nos testes, diga apenas "nada novo consolidado" e pare** — não
   invente aprendizado para justificar a rodada.
