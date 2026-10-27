#!/usr/bin/env python3
"""atos.py — trilhas de raciocínio do interlocutor (parte do Personal Cortex).

Mapeia o TIPO de cada pedido (o ato: viabilidade, desenho, avaliação, execução, publicação…), o DOMÍNIO
(audiovisual, textual, código, dados, operação) e a SEQUÊNCIA entre atos em cada conversa, para a
próxima resposta mirar o degrau certo. Aprende também onde o entendimento costuma falhar (atos e sinais
que antecedem correções) e o vocabulário real do interlocutor, para apontar provável erro de digitação.
Registra a linha de raciocínio, não o resultado.

Subcomandos:
  hook-prompt [--fonte claude|codex]   hook UserPromptSubmit: registra e devolve o contexto do pedido
  contexto [--project P]               bloco curto para o SessionStart
  destilar [--quiet]                   recalcula trilhas, falhas e vocabulário; atualiza a ponte
  linha [--desde AAAA-MM-DD] [--ate …] [--projeto slug] [--dominio d] [--saida arq]
                                       lê um período como linha de raciocínio: objetivo → degraus →
                                       desvios → output final, por conversa
  ponte [--alvo arquivo …]             grava o bloco de trilhas no AGENTS.md do Codex (ou outro alvo)
  importar [--claude pasta] [--codex pasta]   semeia com o histórico local (só sessões abertas por pessoa)
  classificar "<texto>"                mostra ato, domínio e riscos de um texto
  status                               contagens

Dados (só nesta máquina): $CORTEX_HOME/atos/<projeto>.jsonl, trilhas.json, trilhas.md.
Ajustes: $CORTEX_HOME/atos-regras.json
  {"padroes": {"<ato>": ["regex"]}, "formas": {"<ato>": "…"}, "dominios": {"<dom>": ["regex"]}, "vocabulario": ["termo"]}
Prever não é autorizar: a trilha calibra a resposta; nunca executa o próximo degrau sem pedido.
"""
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
ROOT = os.environ.get("CORTEX_HOME", os.path.join(HOME, ".claude", "cortex"))
DIR = os.path.join(ROOT, "atos")
REGRAS = os.path.join(ROOT, "atos-regras.json")
TRILHAS_JSON = os.path.join(ROOT, "trilhas.json")
TRILHAS_MD = os.path.join(ROOT, "trilhas.md")
PONTE_PADRAO = [os.path.join(HOME, ".codex", "AGENTS.md")]
MARCA_INI = "<!-- CORTEX-TRILHAS:BEGIN (gerado por atos.py ponte - não editar dentro do bloco) -->"
MARCA_FIM = "<!-- CORTEX-TRILHAS:END -->"
JANELA_DIAS = 180
MIN_TRANSICAO = 2
TEXTO_MAX = 600
VOCAB_MAX = 3000

# ordem = precedência: o primeiro ato que casar é o principal
ATOS = [
    ("correcao", "correção de rumo",
     [r"^n[aã]o[,.!]", r"\bn[aã]o (era|é) (isso|assim|o que)\b", r"\best[aá] errad", r"\bvoc[eê] errou\b",
      r"\bn[aã]o foi (isso|o que) (que )?(eu )?pedi", r"\bentendeu errado\b", r"\bvoc[eê] n[aã]o entendeu\b",
      r"\bme expressei mal\b", r"\b(eu )?quis dizer\b", r"\bdesfa(z|ça)\b", r"\bvolt(a|e) (atr[aá]s|como estava)\b",
      r"\bna verdade,? (eu )?quer", r"\bpresta aten[cç][aã]o\b", r"\bleia de novo\b"],
     "parar, nomear o desvio em uma frase, refazer pelo pedido original e registrar a lição como correção"),
    ("preferencia", "preferência declarada",
     [r"\b(sempre|nunca) (use|fa[çc]a|responda|escreva|coloque|deixe)\b", r"\bde agora em diante\b",
      r"\bdaqui (pra|para) frente\b", r"\ba partir de agora\b", r"\bprefiro\b", r"\bconsidere isso como aprendizado\b"],
     "aplicar já e registrar como preferência explícita"),
    ("publicacao", "publicação ou deploy",
     [r"\bpush\b", r"\bdeploy\b", r"\bpubli(ca|que|car|quem)\b", r"\bsub(a|ir) (para|no|pro|pra)\b",
      r"\b(envi(a|e|ar)|mand(a|e)) (para|pro|ao|pra) (o )?(github|reposit|site|servidor|produ)"],
     "conferir destino e conteúdo, executar, confirmar com evidência (hash, URL, status); ação externa só com pedido explícito"),
    ("delegacao", "delegação a outro agente",
     [r"@\[", r"\benvi(e|a|ar) (um )?(comando|mensagem|pedido) (para|pro|ao|à)\b", r"\bpe(ça|de|dir) (para|pra) (a|o) (sess|agente)",
      r"\bdelegu?e\b", r"\bcompartilh(e|a|ar) .{0,40}\b(com|para) o codex\b"],
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
DOMINIOS = {
    "audiovisual": [r"\bv[ií]deos?\b", r"\breels?\b", r"\bshorts?\b", r"\bstoryboard", r"\bmotion\b", r"\banima[cç]",
                    r"\bedi[cç][aã]o\b", r"\bthumbnail", r"\bimage(m|ns)\b", r"\bfotos?\b", r"\bfotogra", r"\bilustra",
                    r"\brender", r"\bhyperframes\b", r"\blottie\b", r"\bcarross[eé]is\b|\bcarrossel\b", r"\bb-roll\b",
                    r"\btrilha sonora\b", r"\b[aá]udio\b", r"\bnarra[cç][aã]o\b", r"\blocu[cç][aã]o\b", r"\bestética\b",
                    r"\bdesign\b", r"\bcapa\b", r"\barte\b", r"\bpaleta\b"],
    "textual": [r"\bartigos?\b", r"\btextos?\b", r"\bcopy\b", r"\bposts?\b", r"\blegendas?\b", r"\be-?mails?\b",
                r"\bnewsletter", r"\bverbete", r"\bnot[ií]cias?\b", r"\breda[cç][aã]o\b", r"\bredig", r"\bescrev",
                r"\btom de voz\b", r"\bheadline", r"\bt[ií]tulos?\b", r"\broteiro", r"\bpauta", r"\bvoz da marca\b"],
    "codigo": [r"\bscript", r"\bc[oó]digo\b", r"\bbug\b", r"\btestes?\b", r"\bcommit", r"\bplugin", r"\bpython\b",
               r"\bhtml\b", r"\bcss\b", r"\bapi\b", r"\bmcp\b", r"\bhooks?\b", r"\bkernel\b", r"\bcli\b"],
    "dados": [r"\bm[eé]tricas?\b", r"\bdados\b", r"\brelat[oó]rio", r"\bga4\b", r"\bquery\b", r"\bplanilha",
              r"\bdashboard", r"\bconvers[aã]o\b", r"\bfunil\b"],
    "operacao": [r"\bservidor", r"\bdns\b", r"\bwordpress\b", r"\bsite\b", r"\bcache\b", r"\bbackup", r"\bseguran[cç]a\b",
                 r"\bdrive\b", r"\bhostinger\b", r"\bdeploy\b"],
}
MODIFICADORES = {
    "encadeado": r"\b(quando (finalizar|terminar|acabar)|depois disso|em seguida|e depois|depois,)\b",
    "escopo_amplo": r"\b(todos? (os|as)|tudo|qualquer|completa|completo)\b",
}
VAGOS = re.compile(r"\b(isso|isto|aquilo|esse|essa|esses|essas|aquele|aquela|ele|ela|l[aá])\b")
ROTULO = {a: r for a, r, _, _ in ATOS} | {"outro": "outro"}
TAG = re.compile(r"<([a-z_-]+)[^>]*>.*?</\1>", re.S | re.I)
PALAVRA = re.compile(r"[a-zà-ÿ][a-zà-ÿ0-9-]{3,}")
PEDIDO_EDUCADO = re.compile(r"\b(pode|poderia|consegue|conseguiria)s? (me )?(fazer|implementar|aplicar|seguir|rodar|criar|"
                            r"publicar|subir|atualizar|corrigir|instalar|gerar|executar|desenhar|avaliar|revisar|validar)\b"
                            r"|\bo que (voc[eê] )?acha\b|\bqual (a )?sua opini[aã]o\b")
SO_PERGUNTA = {"viabilidade", "consulta"}
INJETADO_CODEX = ("<", "#", "You are already Codex", "Voce JA E o Codex", "Você já é o Codex")


# ------------------------------------------------------------------ regras e normalização
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
    dominios = {d: list((r.get("dominios") or {}).get(d) or []) + p for d, p in DOMINIOS.items()}
    return padroes, formas, dominios, list(r.get("vocabulario") or [])


def limpar(texto):
    texto = TAG.sub(" ", texto or "")
    return re.sub(r"\s+", " ", texto).strip()


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c))


def dominios_de(t, dominios):
    cont = Counter({d: sum(1 for p in ps if re.search(p, t)) for d, ps in dominios.items()})
    return [d for d, n in cont.most_common() if n > 0]


def classificar(texto, padroes=None):
    """(ato principal, todos os atos, modificadores). Frase interrogativa só vale como viabilidade ou
    consulta, salvo pedido educado ("pode implementar?"). O texto sem acento também é testado, para que
    'faca' ou 'publicacao' digitados sem acento sejam lidos."""
    padroes = padroes or regras()[0]
    t = limpar(texto).lower()
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+|\n+", t) if f.strip()]
    achados = set()
    for f in frases:
        pergunta = f.endswith("?")
        variantes = {f, sem_acento(f)}
        for a, _, _, _ in ATOS:
            if pergunta and a not in SO_PERGUNTA and not PEDIDO_EDUCADO.search(f):
                continue
            if any(re.search(p, v) or re.search(sem_acento(p), v) for p in padroes[a] for v in variantes):
                achados.add(a)
    ordem = [a for a, _, _, _ in ATOS if a in achados]
    if "consulta" in ordem and len(ordem) > 1 and not any(f.endswith("?") for f in frases if not PEDIDO_EDUCADO.search(f)):
        ordem.remove("consulta")
    mods = [m for m, p in MODIFICADORES.items() if re.search(p, t)]
    palavras = t.split()
    if VAGOS.search(t) and len(palavras) <= 14:
        mods.append("referencia_vaga")
    if len([a for a in ordem if a != "consulta"]) >= 3:
        mods.append("pedidos_multiplos")
    return (ordem[0] if ordem else "outro"), ordem, mods


# ------------------------------------------------------------------ digitação
def _dist1(a, b):
    """Distância de edição ≤ 1 (inclui troca de letras vizinhas)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        dif = [i for i in range(la) if a[i] != b[i]]
        return len(dif) == 1 or (len(dif) == 2 and dif[1] == dif[0] + 1 and a[dif[0]] == b[dif[1]] and a[dif[1]] == b[dif[0]])
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def digitacao(texto, vocab, extra=()):
    """Palavras que nunca apareceram no histórico e estão a 1 edição de um termo frequente."""
    if not vocab:
        return []
    freq = dict(vocab)
    for e in extra:
        freq.setdefault(e.lower(), 50)
    sugestoes, vistos = [], set()
    for w in PALAVRA.findall(limpar(texto).lower()):
        if len(w) < 5 or w in freq or sem_acento(w) in freq or w in vistos:
            continue
        vistos.add(w)
        cand = [(n, v) for v, n in freq.items() if n >= 5 and v[0] == w[0] and abs(len(v) - len(w)) <= 1 and _dist1(w, v)]
        if cand:
            sugestoes.append((w, max(cand)[1]))
        if len(sugestoes) >= 3:
            break
    return sugestoes


def corrigir(texto, erros):
    """Texto com as correções prováveis aplicadas (só para classificar; nunca reescreve o pedido)."""
    for errado, certo in erros:
        texto = re.sub(r"(?i)\b%s\b" % re.escape(errado), certo, texto)
    return texto


def slug(caminho):
    base = os.path.basename((caminho or "").rstrip("/")) or "global"
    s = re.sub(r"[^a-z0-9]+", "-", sem_acento(base.lower())).strip("-")
    return s or "global"


# ------------------------------------------------------------------ armazenamento
def _arquivo(projeto):
    return os.path.join(DIR, projeto + ".jsonl")


def registrar(texto, sessao, cwd, ts=None, rid=None, fonte="claude", transcript=None):
    padroes, _, dominios, _ = regras()
    ato, todos, mods = classificar(texto, padroes)
    limpo = limpar(texto)
    reg = {"id": rid or hashlib.sha1(("%s|%s|%s" % (sessao, ts, texto)).encode()).hexdigest()[:16],
           "ts": ts or datetime.now().astimezone().isoformat(timespec="seconds"),
           "fonte": fonte, "sessao": sessao or "", "projeto": slug(cwd), "ato": ato, "atos": todos, "mods": mods,
           "dominios": dominios_de(limpo.lower(), dominios), "texto": limpo[:TEXTO_MAX]}
    if transcript:
        reg["transcript"] = transcript
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


# ------------------------------------------------------------------ destilação
def _sessoes(regs):
    por = defaultdict(list)
    for r in regs:
        por[(r.get("projeto"), r.get("sessao"))].append(r)
    for lista in por.values():
        lista.sort(key=lambda r: r.get("ts") or "")
    return por


def _transicoes(sessoes, filtro=None):
    trans, tri = defaultdict(Counter), Counter()
    for lista in sessoes:
        seq = [r["ato"] for r in lista if r.get("ato") != "outro" and (filtro is None or filtro(r))]
        for a, b in zip(seq, seq[1:]):
            if a != b:
                trans[a][b] += 1
        dedup = [a for i, a in enumerate(seq) if i == 0 or a != seq[i - 1]]
        for t in zip(dedup, dedup[1:], dedup[2:]):
            tri[t] += 1
    return trans, tri


def destilar(ponte_auto=True):
    corte = (datetime.now() - timedelta(days=JANELA_DIAS)).isoformat()[:19]
    regs = [r for r in ler_todos() if (r.get("ts") or "")[:19] >= corte]
    por = _sessoes(regs)
    trans, tri = _transicoes(por.values())
    trans_proj = defaultdict(dict)
    for (proj, _), lista in por.items():
        t, _ = _transicoes([lista])
        for a, c in t.items():
            trans_proj[proj].setdefault(a, Counter()).update(c)
    trans_dom = {}
    for dom in DOMINIOS:
        t, s = _transicoes(por.values(), lambda r, d=dom: d in (r.get("dominios") or []))
        trans_dom[dom] = {"transicoes": {a: dict(c) for a, c in t.items()},
                          "sequencias": [{"trilha": list(k), "n": n} for k, n in s.most_common(5) if n >= MIN_TRANSICAO],
                          "pedidos": sum(1 for r in regs if dom in (r.get("dominios") or []))}
    # onde o entendimento falha: o pedido anterior a cada correção
    antes_ato, antes_mod, antes_dom, total_ato = Counter(), Counter(), Counter(), Counter()
    exemplos = []
    for lista in por.values():
        for i, r in enumerate(lista):
            total_ato[r.get("ato")] += 1
            if r.get("ato") == "correcao" and i > 0:
                ant = lista[i - 1]
                antes_ato[ant.get("ato")] += 1
                antes_mod.update(ant.get("mods") or [])
                antes_dom.update(ant.get("dominios") or [])
                if len(exemplos) < 12:
                    exemplos.append({"pedido": (ant.get("texto") or "")[:160], "correcao": (r.get("texto") or "")[:160],
                                     "ato": ant.get("ato"), "mods": ant.get("mods") or []})
    falhas = {"antes_por_ato": {a: {"correcoes": n, "taxa": round(n / max(total_ato[a], 1), 3)} for a, n in antes_ato.items()},
              "antes_por_sinal": dict(antes_mod), "antes_por_dominio": dict(antes_dom), "exemplos": exemplos}
    vocab = Counter(w for r in regs for w in PALAVRA.findall((r.get("texto") or "").lower()))
    dados = {"gerado": datetime.now().astimezone().isoformat(timespec="seconds"), "registros": len(regs),
             "conversas": len(por), "por_fonte": dict(Counter(r.get("fonte", "claude") for r in regs)),
             "por_ato": dict(Counter(r.get("ato") for r in regs)),
             "por_dominio": dict(Counter(d for r in regs for d in (r.get("dominios") or []))),
             "transicoes": {a: dict(c) for a, c in trans.items()},
             "por_projeto": {p: {a: dict(c) for a, c in d.items()} for p, d in trans_proj.items()},
             "por_dominio_trilhas": trans_dom, "falhas": falhas,
             "sequencias": [{"trilha": list(t), "n": n} for t, n in tri.most_common(10) if n >= MIN_TRANSICAO],
             "vocabulario": vocab.most_common(VOCAB_MAX)}
    os.makedirs(ROOT, exist_ok=True)
    with open(TRILHAS_JSON, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    with open(TRILHAS_MD, "w", encoding="utf-8") as f:
        f.write(render_md(dados))
    if ponte_auto:
        ponte(dados)
    return dados


def provaveis(dados, ato, projeto=None, n=2, dominio=None, estrito=False):
    """Transições prováveis depois do ato. Ordem: domínio → projeto → global; `estrito` usa só o domínio."""
    fonte = None
    if dominio:
        fonte = (((dados.get("por_dominio_trilhas") or {}).get(dominio) or {}).get("transicoes") or {}).get(ato)
        if estrito:
            fonte = fonte or {}
            total = sum(fonte.values())
            return [(b, c, total) for b, c in Counter(fonte).most_common(n) if c >= MIN_TRANSICAO], total
    if not fonte and projeto:
        fonte = ((dados.get("por_projeto") or {}).get(projeto) or {}).get(ato)
    if not fonte or sum(fonte.values()) < MIN_TRANSICAO:
        fonte = (dados.get("transicoes") or {}).get(ato) or {}
    total = sum(fonte.values())
    return [(b, c, total) for b, c in Counter(fonte).most_common(n) if c >= MIN_TRANSICAO], total


def _falha_do_ato(dados, ato):
    f = (((dados.get("falhas") or {}).get("antes_por_ato") or {}).get(ato) or {})
    return f if f.get("correcoes", 0) >= 2 else None


def render_md(d):
    _, formas, _, _ = regras()
    linhas = ["<!-- GERADO por atos.py destilar. Não edite: ajuste atos-regras.json ou observe uma regra. -->",
              "# Trilhas de raciocínio", "",
              "%d pedidos em %d conversas (últimos %d dias; fontes: %s). Prever não é autorizar." % (
                  d["registros"], d["conversas"], JANELA_DIAS, ", ".join("%s %d" % kv for kv in d["por_fonte"].items())), "",
              "| Ato | Pedidos | Depois costuma vir | Correções logo depois | Forma esperada da resposta |", "|---|---|---|---|---|"]
    for a, rot, _, _ in ATOS:
        nx, _ = provaveis(d, a)
        prox = ", ".join("%s (%d/%d)" % (ROTULO[b], c, t) for b, c, t in nx) or "—"
        f = ((d.get("falhas") or {}).get("antes_por_ato") or {}).get(a) or {}
        linhas.append("| %s | %d | %s | %s | %s |" % (rot, d["por_ato"].get(a, 0), prox,
                                                    "%d (%.0f%%)" % (f["correcoes"], 100 * f["taxa"]) if f else "—", formas[a]))
    if d["sequencias"]:
        linhas += ["", "## Sequências frequentes", ""]
        linhas += ["- %s — %d×" % (" → ".join(ROTULO[x] for x in s["trilha"]), s["n"]) for s in d["sequencias"]]
    for dom in ("audiovisual", "textual"):
        info = (d.get("por_dominio_trilhas") or {}).get(dom) or {}
        if not info.get("pedidos"):
            continue
        linhas += ["", "## Domínio %s — %d pedidos" % (dom, info["pedidos"]), ""]
        for a, rot, _, _ in ATOS:
            nx, _ = provaveis(d, a, dominio=dom, estrito=True)
            if nx:
                linhas.append("- %s → depois: %s" % (rot, ", ".join("%s (%d/%d)" % (ROTULO[b], c, t) for b, c, t in nx)))
        for s in info.get("sequencias") or []:
            linhas.append("- trilha: %s (%d×)" % (" → ".join(ROTULO[x] for x in s["trilha"]), s["n"]))
    f = d.get("falhas") or {}
    if f.get("exemplos"):
        linhas += ["", "## Onde o entendimento falhou", "",
                   "Sinais no pedido anterior a uma correção: " + (", ".join("%s %d" % kv for kv in Counter(f.get("antes_por_sinal") or {}).most_common()) or "—"),
                   "Domínios: " + (", ".join("%s %d" % kv for kv in Counter(f.get("antes_por_dominio") or {}).most_common()) or "—"), ""]
        linhas += ["- [%s%s] \"%s\" → correção: \"%s\"" % (ROTULO.get(e["ato"], e["ato"]), (" · " + ", ".join(e["mods"])) if e["mods"] else "",
                                                       e["pedido"], e["correcao"]) for e in f["exemplos"]]
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
    _, formas, _, _ = regras()
    linhas = ["<cortex-trilhas>Trilhas de raciocínio do interlocutor (%d pedidos). Use para mirar o degrau certo; prever não autoriza agir." % d["registros"]]
    sinais = Counter(((d.get("falhas") or {}).get("antes_por_sinal")) or {})
    if sinais:
        linhas.append("- falhas de entendimento costumam vir depois de: %s — confirme o alvo antes de agir quando aparecerem" %
                      ", ".join(s for s, _ in sinais.most_common(3)))
    for a, rot, _, _ in ATOS:
        if d["por_ato"].get(a, 0) < MIN_TRANSICAO:
            continue
        nx, _ = provaveis(d, a, projeto)
        prox = " / ".join("%s %d%%" % (ROTULO[b], round(100 * c / t)) for b, c, t in nx)
        linhas.append("- %s → %s%s" % (rot, formas[a], (" · depois: " + prox) if prox else ""))
    fim = "</cortex-trilhas>"
    out = []
    for linha_ in linhas:
        if len("\n".join(out + [linha_, fim])) > limite:
            break
        out.append(linha_)
    return "\n".join(out + [fim])


# ------------------------------------------------------------------ ponte (Codex e outros)
def bloco_ponte(d):
    _, formas, _, _ = regras()
    l = ["## Trilhas de raciocínio do interlocutor (Cortex)", "",
         "Mapa de como o interlocutor pede, aprendido de %d pedidos (Claude Code e Codex). Use para responder no degrau certo:" % d.get("registros", 0),
         "viabilidade pede sim/não e desenho curto; desenho pede proposta; execução pede fazer e verificar. **Prever não autoriza agir:**",
         "nunca execute o próximo passo antes do pedido. Se o pedido vier ambíguo nos pontos de falha abaixo, confirme o alvo.", ""]
    for a, rot, _, _ in ATOS:
        if d.get("por_ato", {}).get(a, 0) >= MIN_TRANSICAO:
            nx, _ = provaveis(d, a)
            prox = " / ".join("%s %d%%" % (ROTULO[b], round(100 * c / t)) for b, c, t in nx)
            l.append("- **%s** → %s%s" % (rot, formas[a], (" · depois: " + prox) if prox else ""))
    for dom, titulo in (("audiovisual", "Conteúdo audiovisual (vídeo, imagem, foto, carrossel, motion)"),
                        ("textual", "Conteúdo textual (artigo, post, legenda, e-mail, roteiro, copy)")):
        info = (d.get("por_dominio_trilhas") or {}).get(dom) or {}
        if not info.get("pedidos"):
            continue
        l += ["", "### %s — %d pedidos" % (titulo, info["pedidos"])]
        for a, rot, _, _ in ATOS:
            nx, _ = provaveis(d, a, dominio=dom, estrito=True)
            if nx:
                l.append("- %s → depois: %s" % (rot, ", ".join("%s %d%%" % (ROTULO[b], round(100 * c / t)) for b, c, t in nx)))
        for s in info.get("sequencias") or []:
            l.append("- trilha frequente: %s (%d×)" % (" → ".join(ROTULO[x] for x in s["trilha"]), s["n"]))
        n_corr = ((d.get("falhas") or {}).get("antes_por_dominio") or {}).get(dom, 0)
        if n_corr:
            l.append("- %d correção(ões) vieram logo depois de pedidos deste domínio: confirme formato, destino e voz antes de produzir." % n_corr)
    sinais = Counter(((d.get("falhas") or {}).get("antes_por_sinal")) or {})
    if sinais:
        l += ["", "Sinais que antecedem falha de entendimento: %s." % ", ".join("%s (%d)" % kv for kv in sinais.most_common(4))]
    l += ["", "Ao ler um período de trabalho, monte a linha: objetivo inicial → degraus → desvios e correções → output final. Não resuma conteúdo solto.", ""]
    return "\n".join(l)


def ponte(d=None, alvos=None):
    d = d or carregar_trilhas()
    if not d.get("registros"):
        return []
    env = os.environ.get("CORTEX_PONTE_ALVOS")
    alvos = alvos or ([a for a in env.split(os.pathsep) if a] if env else PONTE_PADRAO)
    bloco = "%s\n%s%s\n" % (MARCA_INI, bloco_ponte(d), MARCA_FIM)
    tocados = []
    for alvo in alvos:
        alvo = os.path.expanduser(alvo)
        if not os.path.isdir(os.path.dirname(alvo)):
            continue
        antigo = open(alvo, encoding="utf-8").read() if os.path.exists(alvo) else ""
        if MARCA_INI in antigo and MARCA_FIM in antigo:
            novo = antigo.split(MARCA_INI)[0] + bloco + antigo.split(MARCA_FIM, 1)[1].lstrip("\n")
        else:
            novo = (antigo.rstrip() + "\n\n" if antigo.strip() else "") + bloco
        if novo != antigo:
            tmp = f"{alvo}.{os.getpid()}.tmp-trilhas"   # unico por processo: nome fixo deixa um gravador truncar o outro
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(novo)
            os.replace(tmp, alvo)
            tocados.append(alvo)
    return tocados


# ------------------------------------------------------------------ leitura de período
def _ultimo_output(transcript):
    """Última resposta textual do assistente no transcript (Claude Code ou Codex)."""
    if not transcript or not os.path.exists(transcript):
        return ""
    ultimo = ""
    try:
        with open(transcript, encoding="utf-8") as f:
            for l in f:
                try:
                    d = json.loads(l)
                except ValueError:
                    continue
                if d.get("type") == "assistant":
                    c = (d.get("message") or {}).get("content")
                    txt = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text") if isinstance(c, list) else (c or "")
                    if txt.strip():
                        ultimo = txt
                p = d.get("payload") or {}
                if d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "assistant":
                    txt = " ".join(x.get("text", "") for x in p.get("content") or [] if isinstance(x, dict))
                    if txt.strip():
                        ultimo = txt
    except OSError:
        return ""
    return limpar(ultimo)


def linha(desde=None, ate=None, projeto=None, dominio=None, max_conversas=40):
    regs = ler_todos()
    if desde:
        regs = [r for r in regs if (r.get("ts") or "")[:10] >= desde]
    if ate:
        regs = [r for r in regs if (r.get("ts") or "")[:10] <= ate]
    if projeto:
        regs = [r for r in regs if r.get("projeto") == projeto]
    por = _sessoes(regs)
    if dominio:
        por = {k: v for k, v in por.items() if any(dominio in (r.get("dominios") or []) for r in v)}
    conversas = sorted(por.items(), key=lambda kv: kv[1][0].get("ts") or "")[-max_conversas:]
    out = ["# Linha de raciocínio — %s a %s%s%s" % (desde or "início", ate or "hoje", " · projeto " + projeto if projeto else "",
                                                    " · domínio " + dominio if dominio else ""), "",
           "Leia como linha, não como conteúdo: objetivo → degraus → desvios → output final. A pergunta é *por que* cada degrau veio, e onde o entendimento se desviou.", ""]
    trilhas = Counter()
    for (proj, _), lista in conversas:
        seq = [r["ato"] for r in lista if r.get("ato") != "outro"]
        dedup = [a for i, a in enumerate(seq) if i == 0 or a != seq[i - 1]]
        trilhas[" → ".join(ROTULO[a] for a in dedup[:6])] += 1
        corr = [r for r in lista if r.get("ato") == "correcao"]
        final = _ultimo_output(next((r.get("transcript") for r in reversed(lista) if r.get("transcript")), None))
        out += ["## %s · %s · %d pedidos (%s)" % ((lista[0].get("ts") or "")[:10], proj, len(lista), lista[0].get("fonte", "claude")),
                "- **Objetivo inicial:** %s" % (lista[0].get("texto") or "")[:220],
                "- **Degraus:** %s" % (" → ".join(ROTULO[a] for a in dedup) or "—")]
        doms = Counter(d for r in lista for d in (r.get("dominios") or []))
        if doms:
            out.append("- **Domínios:** %s" % ", ".join(d for d, _ in doms.most_common(3)))
        for c in corr[:3]:
            out.append("- **Desvio corrigido:** %s" % (c.get("texto") or "")[:200])
        riscos = Counter(m for r in lista for m in (r.get("mods") or []) if m in ("referencia_vaga", "pedidos_multiplos"))
        if riscos:
            out.append("- **Sinais de risco no caminho:** %s" % ", ".join("%s %d" % kv for kv in riscos.items()))
        out.append("- **Último pedido:** %s" % (lista[-1].get("texto") or "")[:200])
        out.append("- **Output final:** %s" % (final[:400] if final else "(transcript indisponível)"))
        out.append("")
    if trilhas:
        out += ["## Trilhas do período", ""] + ["- %s — %d×" % kv for kv in trilhas.most_common(8)]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ hook e importação
def _origem_codex(transcript):
    try:
        with open(transcript, encoding="utf-8") as f:
            p = (json.loads(f.readline()).get("payload") or {})
        return p.get("originator"), p.get("thread_source")
    except (OSError, ValueError, TypeError):
        return None, None


def hook_prompt(stdin, fonte="claude"):
    try:
        ev = json.loads(stdin or "{}")
    except ValueError:
        return ""
    texto = ev.get("prompt") or ""
    if not limpar(texto):
        return ""
    transcript = ev.get("transcript_path")
    if fonte == "codex" and transcript:
        origem, thread = _origem_codex(transcript)
        if origem == "codex_exec" or thread == "subagent":
            return ""  # pedido escrito por script ou agente, não pela pessoa
    sessao, cwd = ev.get("session_id") or "", ev.get("cwd") or os.getcwd()
    projeto = slug(cwd)
    ant = anterior_na_sessao(projeto, sessao)
    d = carregar_trilhas()
    _, formas, _, extra = regras()
    erros = digitacao(texto, d.get("vocabulario") or [], extra)
    lido = corrigir(texto, erros)  # a intenção provável entra na classificação; o texto original fica no registro
    reg = registrar(lido, sessao, cwd, fonte=fonte, transcript=transcript)
    if erros:
        reg["mods"].append("digitacao")
    partes = []
    if reg["ato"] != "outro":
        partes.append("ato provável: %s" % ROTULO[reg["ato"]])
    if ant and ant.get("ato") not in (None, "outro"):
        partes.append("anterior nesta conversa: %s" % ROTULO[ant["ato"]])
    if reg["dominios"]:
        partes.append("domínio: %s" % ", ".join(reg["dominios"][:2]))
    riscos = [m for m in reg["mods"] if m in ("referencia_vaga", "pedidos_multiplos")]
    outros = [m for m in reg["mods"] if m not in riscos]
    if outros:
        partes.append("modificadores: %s" % ", ".join(outros))
    if riscos:
        partes.append("risco de entendimento: %s — confirme o alvo se não estiver claro na conversa" % ", ".join(riscos))
    if erros:
        partes.append("possível digitação: %s — leia pela intenção" % ", ".join("'%s'≈'%s'" % e for e in erros))
    if reg["ato"] != "outro":
        partes.append("forma esperada: %s" % formas[reg["ato"]])
        dom = next((x for x in reg["dominios"] if x in ("audiovisual", "textual")), None)
        nx, _ = provaveis(d, reg["ato"], projeto, dominio=dom)
        if nx:
            partes.append("costuma vir depois: %s" % " / ".join("%s (%d/%d)" % (ROTULO[b], c, t) for b, c, t in nx))
        f = _falha_do_ato(d, reg["ato"])
        if f:
            partes.append("atenção: %d correções vieram logo depois deste tipo de pedido" % f["correcoes"])
    if not partes:
        return ""
    texto_ctx = "<cortex-ato>%s. Calibra a resposta; não autoriza o próximo passo.</cortex-ato>" % "; ".join(partes)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": texto_ctx}}, ensure_ascii=False)


def _texto_usuario_claude(d):
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


def _importar_claude(pasta, existentes):
    n = 0
    for p in sorted(glob.glob(os.path.join(os.path.expanduser(pasta), "*", "*.jsonl"))):
        try:
            linhas = open(p, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        for l in linhas:
            try:
                d = json.loads(l)
            except ValueError:
                continue
            texto = _texto_usuario_claude(d)
            if not texto or not limpar(texto):
                continue
            rid = (d.get("uuid") or "")[:16] or None
            if rid and rid in existentes:
                continue
            registrar(texto, d.get("sessionId"), d.get("cwd"), d.get("timestamp"), rid, "claude", p)
            existentes.add(rid)
            n += 1
    return n


def _importar_codex(pasta, existentes):
    n = 0
    for p in sorted(glob.glob(os.path.join(os.path.expanduser(pasta), "**", "*.jsonl"), recursive=True)):
        try:
            linhas = open(p, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        meta = {}
        for i, l in enumerate(linhas):
            try:
                d = json.loads(l)
            except ValueError:
                continue
            pl = d.get("payload") or {}
            if d.get("type") == "session_meta":
                meta = pl
                if pl.get("originator") == "codex_exec" or pl.get("thread_source") == "subagent":
                    break
                continue
            if not (d.get("type") == "response_item" and pl.get("type") == "message" and pl.get("role") == "user"):
                continue
            blocos = [x.get("text", "") for x in pl.get("content") or [] if isinstance(x, dict)]
            texto = " ".join(b for b in blocos if b.strip() and not b.lstrip().startswith(INJETADO_CODEX) and not b.startswith("</"))
            if not limpar(texto):
                continue
            rid = hashlib.sha1(("%s|%d" % (p, i)).encode()).hexdigest()[:16]
            if rid in existentes:
                continue
            registrar(texto, meta.get("id") or meta.get("session_id"), meta.get("cwd"), d.get("timestamp"), rid, "codex", p)
            existentes.add(rid)
            n += 1
    return n


def importar(claude=None, codex=None):
    existentes = {r.get("id") for r in ler_todos()}
    n = 0
    for pasta in claude or []:
        n += _importar_claude(pasta, existentes)
    for pasta in codex or []:
        n += _importar_codex(pasta, existentes)
    return n


def _arg(args, nome, padrao=None):
    return args[args.index(nome) + 1] if nome in args and args.index(nome) + 1 < len(args) else padrao


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "hook-prompt":
            out = hook_prompt(sys.stdin.read(), _arg(args, "--fonte", "claude"))
            if out:
                print(out)
            return 0
        if cmd == "contexto":
            out = contexto(slug(_arg(args, "--project")) if "--project" in args else None)
            if out:
                print(out)
            return 0
        if cmd == "destilar":
            d = destilar()
            if "--quiet" not in args:
                print("trilhas: %d pedidos, %d conversas → %s" % (d["registros"], d["conversas"], TRILHAS_MD))
            return 0
        if cmd == "linha":
            texto = linha(_arg(args, "--desde"), _arg(args, "--ate"), _arg(args, "--projeto"), _arg(args, "--dominio"))
            saida = _arg(args, "--saida")
            if saida:
                with open(saida, "w", encoding="utf-8") as f:
                    f.write(texto)
                print("gravado: %s" % saida)
            else:
                sys.stdout.write(texto)
            return 0
        if cmd == "ponte":
            alvos = [args[i + 1] for i, a in enumerate(args) if a == "--alvo" and i + 1 < len(args)] or None
            print("ponte: %s" % (", ".join(ponte(alvos=alvos)) or "nada mudou"))
            return 0
        if cmd == "importar":
            claude = [args[i + 1] for i, a in enumerate(args) if a == "--claude"] or [os.path.join(HOME, ".claude", "projects")]
            codex = [args[i + 1] for i, a in enumerate(args) if a == "--codex"] or [os.path.join(HOME, ".codex", "sessions")]
            n = importar(claude, codex)
            d = destilar()
            print("importados: %d · trilhas: %d pedidos em %d conversas · fontes: %s" % (n, d["registros"], d["conversas"], d["por_fonte"]))
            return 0
        if cmd == "classificar":
            texto = " ".join(args)
            a, todos, mods = classificar(texto)
            _, _, dominios, extra = regras()
            erros = digitacao(texto, carregar_trilhas().get("vocabulario") or [], extra)
            if erros:
                a, todos, mods = classificar(corrigir(texto, erros))
            print(json.dumps({"ato": a, "atos": todos, "mods": mods, "dominios": dominios_de(limpar(texto).lower(), dominios),
                              "digitacao": erros}, ensure_ascii=False))
            return 0
        if cmd == "status":
            d = carregar_trilhas()
            print("registros: %d · conversas: %d · fontes: %s · por ato: %s" % (
                d.get("registros", 0), d.get("conversas", 0), d.get("por_fonte", {}), json.dumps(d.get("por_ato", {}), ensure_ascii=False)))
            return 0
    except Exception:
        if cmd in ("hook-prompt", "contexto"):
            return 0  # hook nunca derruba a sessão
        raise
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
