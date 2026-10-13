#!/usr/bin/env python3
"""atos.py — trilhas de raciocínio do interlocutor (parte do Personal Cortex).

Mapeia o TIPO de cada pedido (o ato: viabilidade, desenho, avaliação, execução, publicação…) e a
SEQUÊNCIA entre atos em cada conversa, para a próxima resposta mirar o degrau certo. Registra a linha
de raciocínio, não o resultado.

Subcomandos:
  hook-prompt                 hook UserPromptSubmit: lê o JSON do stdin, registra o ato e devolve uma
                              linha de contexto (ato provável, ato anterior, forma esperada, trilha)
  contexto [--project P]      bloco curto para o SessionStart (trilhas aprendidas e formas por ato)
  destilar [--quiet]          recalcula trilhas.json e trilhas.md a partir dos registros
  importar [pasta…]           semeia com o histórico de transcripts (padrão: ~/.claude/projects)
  classificar "<texto>"       mostra o ato de um texto (para ajustar padrões)
  status                      contagens

Dados (só nesta máquina, nunca no repositório): $CORTEX_HOME/atos/<projeto>.jsonl, trilhas.json,
trilhas.md. Padrões e formas próprios: $CORTEX_HOME/atos-regras.json
  {"padroes": {"<ato>": ["regex", …]}, "formas": {"<ato>": "…"}}
Prever não é autorizar: a trilha calibra a resposta; nunca executa o próximo degrau sem pedido.
"""
import glob
import hashlib
import json
import os
import re
import sys

from collections import Counter, defaultdict
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
ROOT = os.environ.get("CORTEX_HOME", os.path.join(HOME, ".claude", "cortex"))
DIR = os.path.join(ROOT, "atos")
REGRAS = os.path.join(ROOT, "atos-regras.json")
TRILHAS_JSON = os.path.join(ROOT, "trilhas.json")
TRILHAS_MD = os.path.join(ROOT, "trilhas.md")
JANELA_DIAS = 180          # registros mais antigos não entram nas trilhas
MIN_TRANSICAO = 2          # mesma regra de promoção do Cortex: 2 ocorrências
TEXTO_MAX = 600

# ordem = precedência: o primeiro ato que casar é o principal
ATOS = [
    ("correcao", "correção de rumo",
     [r"\bn[aã]o (era|é) (isso|assim)\b", r"\best[aá] errad", r"\bvoc[eê] errou\b", r"\bn[aã]o foi isso que\b",
      r"\bdesfa(z|ça)\b", r"\bvolt(a|e) (atr[aá]s|como estava)\b", r"\bna verdade,? (eu )?quer"],
     "parar, nomear o desvio em uma frase, refazer pelo pedido original e registrar a lição como correção"),
    ("preferencia", "preferência declarada",
     [r"\b(sempre|nunca) (use|fa[çc]a|responda|escreva|coloque|deixe)\b", r"\bde agora em diante\b",
      r"\bdaqui (pra|para) frente\b", r"\ba partir de agora\b", r"\bprefiro\b"],
     "aplicar já e registrar como preferência explícita"),
    ("publicacao", "publicação ou deploy",
     [r"\bpush\b", r"\bdeploy\b", r"\bpubli(ca|que|car|quem)\b", r"\bsub(a|ir|ir) (para|no|pro|pra)\b",
      r"\b(envi(a|e|ar)|mand(a|e)) (para|pro|ao|pra) (o )?(github|reposit|site|servidor|produ)"],
     "conferir destino e conteúdo, executar, confirmar com evidência (hash, URL, status); ação externa só com pedido explícito"),
    ("delegacao", "delegação a outro agente",
     [r"@\[", r"\benvi(e|a|ar) (um )?(comando|mensagem|pedido) (para|pro|ao|à)\b", r"\bpe(ça|de|dir) (para|pra) (a|o) (sess|agente)",
      r"\bdelegu?e\b"],
     "mensagem autocontida (contexto, tarefa, travas) e relatar que foi entregue, não que foi feito"),
    ("viabilidade", "pergunta de viabilidade",
     [r"\b(é|e|seria|ser[aá]) poss[ií]vel\b[^?]*\?", r"\bd[aá] (pra|para)\b[^?]*\?", r"\btem como\b[^?]*\?",
      r"\bconsegue[m]?\b[^?]*\?", r"\bfaz sentido\b[^?]*\?"],
     "sim ou não com o porquê, desenho curto, custo e travas; oferecer o próximo degrau sem executá-lo"),
    ("desenho", "desenho ou plano",
     [r"\bdesenh(a|e|ar)\b", r"\bplanej(a|e|ar)\b", r"\besbo[cç]", r"\barquitet", r"\bprop(onha|or|õe)\b",
      r"\bestrutur(a|e) (um|uma|o|a)\b"],
     "proposta concreta com decisões em aberto numeradas; documento quando for para outra pessoa; sem implementar"),
    ("avaliacao", "avaliação ou validação",
     [r"\bavali(a|e|ar)\b", r"\banalis(a|e|ar)\b", r"\bcompar(a|e|ar)\b", r"\brevis(a|e|ar)\b", r"\baudit(a|e|ar)\b",
      r"\bvalid(a|e|ar|ação)\b", r"\bconfir(a|e|ir)\b", r"\bverifi(ca|que|car)\b", r"\bo que (voc[eê] )?acha\b"],
     "veredito primeiro, evidência executada, achados por gravidade; separar o visto do inferido"),
    ("execucao", "execução",
     [r"\bimplement(a|e|ar)\b", r"\bfa(ça|z|zer)\b", r"\bcri(a|e|ar)\b", r"\bcorri(ja|ge|gir)\b", r"\bapli(ca|que|car)\b",
      r"\batualiz(a|e|ar)\b", r"\binstal(a|e|ar)\b", r"\brod(a|e|ar)\b", r"\bexecut(a|e|ar)\b", r"\bger(a|e|ar)\b",
      r"\bpode (fazer|implementar|aplicar|seguir|rodar|criar)\b", r"\bsegu(e|ir) com\b"],
     "executar, verificar com saída real e relatar o que mudou; perguntar só o que bloquear"),
    ("consulta", "consulta",
     [r"^\s*(o que|qual|quais|como|onde|quando|por ?que|quem|quanto)\b", r"\?\s*$"],
     "resposta direta primeiro; contexto só se mudar a decisão"),
]
MODIFICADORES = {
    "encadeado": r"\b(quando (finalizar|terminar|acabar)|depois disso|em seguida|e depois|depois,)\b",
    "escopo_amplo": r"\b(todos? (os|as)|tudo|qualquer|completa|completo)\b",
}
ROTULO = {a: r for a, r, _, _ in ATOS} | {"outro": "outro"}
TAG = re.compile(r"<([a-z_-]+)[^>]*>.*?</\1>", re.S | re.I)


# ------------------------------------------------------------------ classificação
def regras():
    try:
        with open(REGRAS, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        r = {}
    padroes, formas = {}, {}
    for a, _, pads, forma in ATOS:
        padroes[a] = list((r.get("padroes") or {}).get(a) or []) + pads
        formas[a] = (r.get("formas") or {}).get(a) or forma
    return padroes, formas


def limpar(texto):
    texto = TAG.sub(" ", texto or "")
    return re.sub(r"\s+", " ", texto).strip()


PEDIDO_EDUCADO = re.compile(r"\b(pode|poderia|consegue|conseguiria)s? (me )?(fazer|implementar|aplicar|seguir|rodar|criar|"
                            r"publicar|subir|atualizar|corrigir|instalar|gerar|executar|desenhar|avaliar|revisar|validar)\b"
                            r"|\bo que (voc[eê] )?acha\b|\bqual (a )?sua opini[aã]o\b")
SO_PERGUNTA = {"viabilidade", "consulta"}


def classificar(texto, padroes=None):
    """(ato principal, todos os atos que casaram, modificadores).

    Frase interrogativa só vale como viabilidade ou consulta — "está preparado para aplicar?" é pergunta,
    não ordem —, salvo pedido educado ("pode implementar?"), que conta como o ato do verbo.
    """
    padroes = padroes or regras()[0]
    t = limpar(texto).lower()
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+|\n+", t) if f.strip()]
    achados = set()
    for f in frases:
        pergunta = f.endswith("?")
        for a, _, _, _ in ATOS:
            if pergunta and a not in SO_PERGUNTA and not PEDIDO_EDUCADO.search(f):
                continue
            if any(re.search(p, f) for p in padroes[a]):
                achados.add(a)
    ordem = [a for a, _, _, _ in ATOS if a in achados]
    if "consulta" in ordem and len(ordem) > 1 and not any(f.endswith("?") for f in frases if not PEDIDO_EDUCADO.search(f)):
        ordem.remove("consulta")
    mods = [m for m, p in MODIFICADORES.items() if re.search(p, t)]
    return (ordem[0] if ordem else "outro"), ordem, mods


def slug(caminho):
    base = os.path.basename((caminho or "").rstrip("/")) or "global"
    s = re.sub(r"[^a-z0-9]+", "-", base.lower().encode("ascii", "ignore").decode()).strip("-")
    return s or "global"


# ------------------------------------------------------------------ armazenamento
def _arquivo(projeto):
    return os.path.join(DIR, projeto + ".jsonl")


def registrar(texto, sessao, cwd, ts=None, rid=None):
    ato, todos, mods = classificar(texto)
    reg = {"id": rid or hashlib.sha1(("%s|%s|%s" % (sessao, ts, texto)).encode()).hexdigest()[:16],
           "ts": ts or datetime.now().astimezone().isoformat(timespec="seconds"),
           "sessao": sessao or "", "projeto": slug(cwd), "ato": ato, "atos": todos, "mods": mods,
           "texto": limpar(texto)[:TEXTO_MAX]}
    os.makedirs(DIR, exist_ok=True)
    with open(_arquivo(reg["projeto"]), "a", encoding="utf-8") as f:
        f.write(json.dumps(reg, ensure_ascii=False) + "\n")
    return reg


def ler_todos():
    regs = []
    for p in glob.glob(os.path.join(DIR, "*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for linha in f:
                try:
                    regs.append(json.loads(linha))
                except ValueError:
                    continue
    return regs


def anterior_na_sessao(projeto, sessao, limite=200):
    """Último registro da mesma conversa (lê só o fim do arquivo)."""
    p = _arquivo(projeto)
    if not sessao or not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 120000))
        linhas = f.read().decode("utf-8", "ignore").splitlines()[-limite:]
    for linha in reversed(linhas):
        try:
            r = json.loads(linha)
        except ValueError:
            continue
        if r.get("sessao") == sessao:
            return r
    return None


# ------------------------------------------------------------------ trilhas
def destilar():
    corte = (datetime.now() - timedelta(days=JANELA_DIAS)).isoformat()
    regs = [r for r in ler_todos() if (r.get("ts") or "") >= corte[:19]]
    por_sessao = defaultdict(list)
    for r in regs:
        por_sessao[(r.get("projeto"), r.get("sessao"))].append(r)
    trans, trans_proj, tri = defaultdict(Counter), defaultdict(lambda: defaultdict(Counter)), Counter()
    for (proj, _), lista in por_sessao.items():
        lista.sort(key=lambda r: r.get("ts") or "")
        seq = [r["ato"] for r in lista if r.get("ato") != "outro"]
        for a, b in zip(seq, seq[1:]):
            if a != b:
                trans[a][b] += 1
                trans_proj[proj][a][b] += 1
        dedup = [a for i, a in enumerate(seq) if i == 0 or a != seq[i - 1]]
        for t in zip(dedup, dedup[1:], dedup[2:]):
            tri[t] += 1
    dados = {"gerado": datetime.now().astimezone().isoformat(timespec="seconds"), "registros": len(regs),
             "conversas": len(por_sessao), "por_ato": dict(Counter(r.get("ato") for r in regs)),
             "transicoes": {a: dict(c) for a, c in trans.items()},
             "por_projeto": {p: {a: dict(c) for a, c in d.items()} for p, d in trans_proj.items()},
             "sequencias": [{"trilha": list(t), "n": n} for t, n in tri.most_common(10) if n >= MIN_TRANSICAO]}
    os.makedirs(ROOT, exist_ok=True)
    with open(TRILHAS_JSON, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    with open(TRILHAS_MD, "w", encoding="utf-8") as f:
        f.write(render_md(dados))
    return dados


def provaveis(dados, ato, projeto=None, n=2):
    fonte = ((dados.get("por_projeto") or {}).get(projeto) or {}).get(ato) if projeto else None
    if not fonte or sum(fonte.values()) < MIN_TRANSICAO:
        fonte = (dados.get("transicoes") or {}).get(ato) or {}
    total = sum(fonte.values())
    return [(b, c, total) for b, c in Counter(fonte).most_common(n) if c >= MIN_TRANSICAO], total


def render_md(d):
    _, formas = regras()
    linhas = ["<!-- GERADO por atos.py destilar. Não edite: ajuste atos-regras.json ou observe uma regra. -->",
              "# Trilhas de raciocínio", "",
              "%d pedidos em %d conversas (últimos %d dias). Prever não é autorizar." % (d["registros"], d["conversas"], JANELA_DIAS), "",
              "| Ato | Pedidos | Depois costuma vir | Forma esperada da resposta |", "|---|---|---|---|"]
    for a, rot, _, _ in ATOS:
        nx, tot = provaveis(d, a)
        prox = ", ".join("%s (%d/%d)" % (ROTULO[b], c, t) for b, c, t in nx) or "—"
        linhas.append("| %s | %d | %s | %s |" % (rot, d["por_ato"].get(a, 0), prox, formas[a]))
    if d["sequencias"]:
        linhas += ["", "## Sequências frequentes", ""]
        linhas += ["- %s — %d×" % (" → ".join(ROTULO[x] for x in s["trilha"]), s["n"]) for s in d["sequencias"]]
    return "\n".join(linhas) + "\n"


def carregar_trilhas():
    try:
        with open(TRILHAS_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def contexto(projeto=None, limite=1400):
    d = carregar_trilhas()
    if not d.get("registros"):
        return ""
    _, formas = regras()
    linhas = ["<cortex-trilhas>Trilhas de raciocínio do interlocutor (%d pedidos). Use para mirar o degrau certo; prever não autoriza agir." % d["registros"]]
    for a, rot, _, _ in ATOS:
        nx, _ = provaveis(d, a, projeto)
        if d["por_ato"].get(a, 0) < MIN_TRANSICAO:
            continue
        prox = " / ".join("%s %d%%" % (ROTULO[b], round(100 * c / t)) for b, c, t in nx)
        linhas.append("- %s → %s%s" % (rot, formas[a], (" · depois: " + prox) if prox else ""))
    for s in (d.get("sequencias") or [])[:3]:
        linhas.append("- trilha frequente: %s (%d×)" % (" → ".join(ROTULO[x] for x in s["trilha"]), s["n"]))
    fim = "</cortex-trilhas>"
    out = []
    for linha in linhas:
        if len("\n".join(out + [linha, fim])) > limite:
            break
        out.append(linha)
    return "\n".join(out + [fim])


# ------------------------------------------------------------------ hook e importação
def hook_prompt(stdin):
    try:
        ev = json.loads(stdin or "{}")
    except ValueError:
        return ""
    texto = ev.get("prompt") or ""
    if not limpar(texto):
        return ""
    sessao, cwd = ev.get("session_id") or "", ev.get("cwd") or os.getcwd()
    projeto = slug(cwd)
    ant = anterior_na_sessao(projeto, sessao)
    reg = registrar(texto, sessao, cwd)
    if reg["ato"] == "outro":
        return ""
    _, formas = regras()
    nx, _ = provaveis(carregar_trilhas(), reg["ato"], projeto)
    partes = ["ato provável: %s" % ROTULO[reg["ato"]]]
    if ant and ant.get("ato") not in (None, "outro"):
        partes.append("anterior nesta conversa: %s" % ROTULO[ant["ato"]])
    if reg["mods"]:
        partes.append("modificadores: %s" % ", ".join(reg["mods"]))
    partes.append("forma esperada: %s" % formas[reg["ato"]])
    if nx:
        partes.append("costuma vir depois: %s" % " / ".join("%s (%d/%d)" % (ROTULO[b], c, t) for b, c, t in nx))
    return "<cortex-ato>%s. Calibra a resposta; não autoriza o próximo passo.</cortex-ato>" % "; ".join(partes)


def _texto_usuario(d):
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
        return None
    if d.get("turnOrigin") not in (None, "human") or (d.get("origin") or {}).get("kind") not in (None, "human"):
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    if not isinstance(c, str) or c.lstrip().startswith(("<command-", "<local-command", "Caveat:")):
        return None
    return c


def importar(pastas):
    existentes = {r.get("id") for r in ler_todos()}
    n = 0
    for pasta in pastas:
        for p in sorted(glob.glob(os.path.join(os.path.expanduser(pasta), "*", "*.jsonl"))):
            try:
                linhas = open(p, encoding="utf-8").read().splitlines()
            except OSError:
                continue
            for linha in linhas:
                try:
                    d = json.loads(linha)
                except ValueError:
                    continue
                texto = _texto_usuario(d)
                if not texto or not limpar(texto):
                    continue
                rid = (d.get("uuid") or "")[:16] or None
                if rid and rid in existentes:
                    continue
                registrar(texto, d.get("sessionId"), d.get("cwd"), d.get("timestamp"), rid)
                existentes.add(rid)
                n += 1
    return n


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "hook-prompt":
            out = hook_prompt(sys.stdin.read())
            if out:
                print(out)
            return 0
        if cmd == "contexto":
            proj = slug(args[args.index("--project") + 1]) if "--project" in args else None
            out = contexto(proj)
            if out:
                print(out)
            return 0
        if cmd == "destilar":
            d = destilar()
            if "--quiet" not in args:
                print("trilhas: %d pedidos, %d conversas → %s" % (d["registros"], d["conversas"], TRILHAS_MD))
            return 0
        if cmd == "importar":
            n = importar(args or [os.path.join(HOME, ".claude", "projects")])
            d = destilar()
            print("importados: %d · trilhas: %d pedidos em %d conversas" % (n, d["registros"], d["conversas"]))
            return 0
        if cmd == "classificar":
            a, todos, mods = classificar(" ".join(args))
            print(json.dumps({"ato": a, "atos": todos, "mods": mods}, ensure_ascii=False))
            return 0
        if cmd == "status":
            d = carregar_trilhas()
            print("registros: %d · conversas: %d · por ato: %s" % (d.get("registros", 0), d.get("conversas", 0),
                                                                  json.dumps(d.get("por_ato", {}), ensure_ascii=False)))
            return 0
    except Exception as e:  # hook nunca derruba a sessão
        if cmd in ("hook-prompt", "contexto"):
            return 0
        raise
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
