#!/usr/bin/env python3
"""
Cortex - memoria de trabalho evolutiva, global e multi-agente.

Fonte de verdade unica: ~/.claude/cortex/store.jsonl
Tudo o mais (profile.md, principles.md, projects/*.md, EVOLUTION.md, bridge/*)
e' RENDER gerado a partir do store. Nao edite os renders a mao.

Ciclo:  observe -> promote (com evidencia) -> render -> sync (ensina outras apps)
        absorb (aprende de outras apps) -> prune (decai o que nao se sustenta)
"""
import argparse, fcntl, glob, hashlib, json, os, re, shutil, sys, time, unicodedata, uuid
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
ROOT = os.environ.get("CORTEX_HOME", os.path.join(HOME, ".claude", "cortex"))
STORE = os.path.join(ROOT, "store.jsonl")
EVENTS = os.path.join(ROOT, "events.jsonl")
PROJECTS_DIR = os.path.join(ROOT, "projects")
JOURNAL_DIR = os.path.join(ROOT, "journal")
INBOX_DIR = os.path.join(ROOT, "inbox")
BRIDGE_DIR = os.path.join(ROOT, "bridge")
LOCK = os.path.join(ROOT, "store.lock")
SNAP_DIR = os.path.join(ROOT, "backups")
USER_NAME = os.environ.get("CORTEX_USER_NAME", "o usuário")  # como o perfil chama o interlocutor

KINDS = ["identity", "preference", "principle", "workflow", "project", "reference", "correction"]
KIND_LABEL = {
    "identity": "Quem e' " + USER_NAME,
    "preference": "Preferencias de trabalho",
    "principle": "Principios (destilados)",
    "workflow": "Modo de operar",
    "project": "Comportamento do projeto",
    "reference": "Referencias",
    "correction": "Correcoes aprendidas",
}
PROMOTE_THRESHOLD = 2          # ocorrencias independentes p/ virar regra ativa
CANDIDATE_TTL_DAYS = 30        # candidato sem reforco morre
ACTIVE_TTL_DAYS = 120          # regra ativa fraca sem reforco vai p/ arquivo
STRONG_SEEN = 4                # a partir daqui a regra e' considerada consolidada
CONTEXT_BUDGET = 14000          # chars injetados por sessao (global)
PROJECT_BUDGET = 6000

# Trava contra a perda de 20/10/2026: varias sessoes encerrando juntas rodavam
# `maintain` em paralelo, gravando pelo mesmo store.jsonl.tmp; uma truncava o
# arquivo da outra e o store virava 0 byte sem erro visivel.
LOCK_WAIT_S = 20               # espera maxima pela trava (o hook de SessionEnd tem 30 s)
SHRINK_MAX = 0.20              # gravacao que encolhe o store mais que isso e' recusada
SNAP_KEEP = 14                 # snapshots diarios do store mantidos em backups/
PERMITIR_ENCOLHER = False      # ligado por `cortex --permitir-encolher <cmd>`

MARK_BEGIN = "<!-- CORTEX:BEGIN (gerado por ~/.claude/cortex/bin/cortex.py sync - nao editar dentro do bloco) -->"
MARK_END = "<!-- CORTEX:END -->"


# ---------------------------------------------------------------- utilidades
def today():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def days_since(datestr):
    try:
        d = datetime.strptime(datestr[:10], "%Y-%m-%d")
    except Exception:
        return 0
    return (datetime.now() - d).days


def norm(text):
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    stop = {"de","do","da","o","a","os","as","um","uma","que","e","em","no","na","para","por","com",
            "the","to","of","and","in","for","is","be","should","always","sempre"}
    words = [w for w in t.split() if w not in stop]
    return " ".join(sorted(set(words)))


def slugify(path):
    if not path:
        return None
    base = os.path.basename(os.path.abspath(path)) or "root"
    s = unicodedata.normalize("NFKD", base.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "root"


class StoreError(Exception):
    """Store ilegivel ou gravacao perigosa: o comando para sem gravar nada."""


def read_jsonl(path, estrito=False):
    """estrito: linha invalida derruba a leitura em vez de sumir em silencio."""
    if not os.path.exists(path):
        return []
    out, ruins = [], 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    ruins += 1
    if estrito and ruins:
        raise StoreError(f"{path}: {ruins} linha(s) invalida(s) de {ruins + len(out)}; nada foi gravado. "
                         f"Restaure de {SNAP_DIR}/ ou reconstrua a partir de {EVENTS}.")
    return out


def write_jsonl(path, rows):
    # temporario unico por processo: com nome fixo, dois gravadores truncavam o arquivo um do outro
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def trava(esperar=True):
    """Exclusao mutua entre processos para o ciclo ler->gravar inteiro de um comando.
    Devolve o descritor (solto quando o processo termina) ou None se nao conseguiu."""
    os.makedirs(ROOT, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    limite = time.monotonic() + (LOCK_WAIT_S if esperar else 0)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= limite:
                os.close(fd)
                return None
            time.sleep(0.1)


def log_event(kind, rec, note=""):
    os.makedirs(ROOT, exist_ok=True)
    with open(EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": now_iso(), "event": kind, "id": rec.get("id"),
            "scope": rec.get("scope"), "project": rec.get("project"),
            "kind_of": rec.get("kind"), "text": rec.get("text"),
            "why": rec.get("why", ""), "note": note,
        }, ensure_ascii=False) + "\n")


def rid(scope, project, text):
    key = f"{scope}|{project or ''}|{norm(text)}"
    return "r_" + hashlib.sha1(key.encode()).hexdigest()[:8]


def load():
    rows = read_jsonl(STORE, estrito=True)
    # store vazio com historico no log e' a assinatura de 20/10, nao uma instalacao nova
    if not rows and not PERMITIR_ENCOLHER and os.path.exists(EVENTS) and os.path.getsize(EVENTS) > 0:
        raise StoreError(f"{STORE} esta vazio mas {EVENTS} tem historico; nada foi gravado. "
                         f"Restaure de {SNAP_DIR}/ ou reconstrua a partir do log.")
    return rows


def _snapshot():
    """Uma copia do store por dia, antes da primeira gravacao do dia; guarda SNAP_KEEP dias."""
    if not os.path.exists(STORE) or os.path.getsize(STORE) == 0:
        return
    os.makedirs(SNAP_DIR, exist_ok=True)
    dest = os.path.join(SNAP_DIR, f"store-{today()}.jsonl")
    if not os.path.exists(dest):
        tmp = f"{dest}.{os.getpid()}.tmp"
        shutil.copy2(STORE, tmp)
        os.replace(tmp, dest)
    for velho in sorted(glob.glob(os.path.join(SNAP_DIR, "store-*.jsonl")))[:-SNAP_KEEP]:
        os.remove(velho)


def save(rows):
    antigo = len(read_jsonl(STORE, estrito=True))
    novo = len(rows)
    if antigo and not PERMITIR_ENCOLHER and (novo == 0 or novo < antigo * (1 - SHRINK_MAX)):
        log_event("save_recusado", {}, f"{antigo} -> {novo} linhas")
        raise StoreError(f"gravacao recusada: o store cairia de {antigo} para {novo} linha(s). "
                         "Se for intencional, repita com `cortex --permitir-encolher <comando>`.")
    _snapshot()
    write_jsonl(STORE, rows)


# ------------------------------------------------------------------ observe
def cmd_observe(a):
    rows = load()
    project = slugify(a.project) if a.scope == "project" else None
    if a.scope == "project" and not project:
        print("erro: --scope project exige --project <caminho>", file=sys.stderr)
        return 1
    _id = rid(a.scope, project, a.text)
    existing = next((r for r in rows if r["id"] == _id), None)
    if existing:
        existing["seen"] = existing.get("seen", 1) + 1
        existing["updated"] = today()
        existing["source"] = sorted(set(existing.get("source", []) + [a.source]))
        if a.evidence:
            ev = existing.get("evidence", [])
            if a.evidence not in ev:
                ev.append(a.evidence)
            existing["evidence"] = ev[-3:]
        if a.why and not existing.get("why"):
            existing["why"] = a.why
        if existing.get("status") == "archived":
            existing["status"] = "active"
            log_event("reactivated", existing)
        elif existing.get("status") == "candidate" and (
            existing["seen"] >= PROMOTE_THRESHOLD or a.explicit
        ):
            existing["status"] = "active"
            log_event("promoted", existing, f"seen={existing['seen']}")
        save(rows)
        print(f"reforcado {_id} (seen={existing['seen']}, status={existing['status']})")
        return 0

    rec = {
        "id": _id, "scope": a.scope, "project": project, "kind": a.kind,
        "text": a.text.strip(), "why": (a.why or "").strip(),
        "seen": 1, "explicit": bool(a.explicit),
        "status": "active" if a.explicit else "candidate",
        "created": today(), "updated": today(),
        "source": [a.source], "evidence": [a.evidence] if a.evidence else [],
        "supersedes": a.supersedes or None,
    }
    rows.append(rec)
    if a.supersedes:
        for r in rows:
            if r["id"] == a.supersedes and r["id"] != _id:
                r["status"] = "superseded"
                r["superseded_by"] = _id
                r["updated"] = today()
                log_event("superseded", r, f"por {_id}")
    save(rows)
    log_event("observed" if not a.explicit else "promoted", rec)
    print(f"{'registrado (ativo)' if a.explicit else 'candidato'} {_id}")
    return 0


def cmd_supersede(a):
    rows = load()
    old = next((r for r in rows if r["id"] == a.old_id), None)
    if not old:
        print(f"erro: id {a.old_id} nao encontrado", file=sys.stderr)
        return 1
    ns = argparse.Namespace(
        scope=old["scope"], project=(a.project or old.get("project")), kind=old["kind"],
        text=a.text, why=a.why or f"substitui '{old['text']}'", evidence=None,
        explicit=True, source=a.source, supersedes=a.old_id,
    )
    if ns.scope == "project" and ns.project and not os.path.isabs(str(ns.project)):
        rows2 = rows  # project ja e' slug
        _id = rid(ns.scope, ns.project, ns.text)
        rec = {"id": _id, "scope": ns.scope, "project": ns.project, "kind": ns.kind,
               "text": ns.text.strip(), "why": ns.why, "seen": 1, "explicit": True,
               "status": "active", "created": today(), "updated": today(),
               "source": [ns.source], "evidence": [], "supersedes": a.old_id}
        rows2.append(rec)
        old["status"] = "superseded"; old["superseded_by"] = _id; old["updated"] = today()
        save(rows2); log_event("superseded", old, f"por {_id}"); log_event("promoted", rec)
        print(f"substituido {a.old_id} -> {_id}")
        return 0
    return cmd_observe(ns)


def cmd_forget(a):
    rows = load()
    hit = next((r for r in rows if r["id"] == a.id), None)
    if not hit:
        print("erro: id nao encontrado", file=sys.stderr)
        return 1
    hit["status"] = "archived"
    hit["updated"] = today()
    hit["why"] = (hit.get("why", "") + f" | arquivado: {a.reason or 'manual'}").strip(" |")
    save(rows)
    log_event("archived", hit, a.reason or "manual")
    print(f"arquivado {a.id}")
    return 0


# ------------------------------------------------------------------ promote
def cmd_promote(a):
    rows, changed = load(), 0
    for r in rows:
        if r.get("status") == "candidate" and r.get("seen", 1) >= PROMOTE_THRESHOLD:
            r["status"] = "active"
            r["updated"] = today()
            log_event("promoted", r, f"seen={r['seen']}")
            changed += 1
    if changed:
        save(rows)
    if not a.quiet:
        print(f"promovidos: {changed}")
    return 0


# -------------------------------------------------------------------- prune
def cmd_prune(a):
    rows = load()
    kept, dropped, archived, eventos = [], 0, 0, []
    for r in rows:
        st, age = r.get("status"), days_since(r.get("updated", r.get("created", today())))
        if st == "candidate" and age > CANDIDATE_TTL_DAYS and r.get("seen", 1) < PROMOTE_THRESHOLD:
            eventos.append(("dropped", r, f"candidato sem reforco ha {age}d"))
            dropped += 1
            continue
        if st == "active" and age > ACTIVE_TTL_DAYS and r.get("seen", 1) < STRONG_SEEN and not r.get("explicit"):
            r["status"] = "archived"
            r["updated"] = today()
            eventos.append(("archived", r, f"sem reforco ha {age}d"))
            archived += 1
        kept.append(r)
    if dropped or archived:
        save(kept)   # o log so registra o que a gravacao efetivou
        for ev in eventos:
            log_event(*ev)
    if not a.quiet:
        print(f"podados: {dropped} candidatos descartados, {archived} regras arquivadas")
    return 0


# ------------------------------------------------------------------- render
def _active(rows, scope=None, project=None):
    out = [r for r in rows if r.get("status") == "active"]
    if scope:
        out = [r for r in out if r.get("scope") == scope]
    if project:
        out = [r for r in out if r.get("project") == project]
    return sorted(out, key=lambda r: (-r.get("seen", 1), r.get("updated", "")), reverse=False)


def _bullets(items, with_why=True):
    lines = []
    for r in sorted(items, key=lambda x: (-x.get("seen", 1), x.get("updated", ""))):
        mark = "!" if r.get("explicit") else ("*" if r.get("seen", 1) >= STRONG_SEEN else "")
        why = f" — _{r['why']}_" if with_why and r.get("why") else ""
        lines.append(f"- {r['text']}{why} <!-- {r['id']} seen:{r.get('seen',1)} {mark} -->")
    return lines


# ordem de leitura do bloco injetado: quem ele e' e como corrigir vem antes do
# detalhe de infra, para que o truncamento por orcamento caia sempre no menos critico
CONTEXT_ORDER = ["identity", "preference", "correction", "principle",
                 "workflow", "project", "reference"]


def _group(rows):
    g = {}
    for r in rows:
        g.setdefault(r.get("kind", "principle"), []).append(r)
    return {k: g[k] for k in CONTEXT_ORDER if k in g}


def render_all():
    rows = load()
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    header = ("<!-- GERADO por cortex.py render. Nao edite a mao: use "
              "`cortex observe/supersede/forget`. -->\n")

    # profile.md — quem eu sou / como penso
    g = _group(_active(rows, scope="global"))
    prof = [header, "# Perfil operacional — %s\n" % USER_NAME]
    for k in ["identity", "preference", "workflow"]:
        if g.get(k):
            prof.append(f"\n## {KIND_LABEL[k]}\n")
            prof += _bullets(g[k])
    open(os.path.join(ROOT, "profile.md"), "w", encoding="utf-8").write("\n".join(prof) + "\n")

    # principles.md — o que virou lei
    pr = [header, "# Principios destilados\n",
          "> `!` = diretriz explicita do usuario · `*` = reforcada 4x ou mais\n"]
    # "project" global = fato da maquina/ambiente: sem isto a regra ativa nao chega aos renders
    for k in ["principle", "correction", "project", "reference"]:
        if g.get(k):
            pr.append(f"\n## {KIND_LABEL[k]}\n")
            pr += _bullets(g[k])
    open(os.path.join(ROOT, "principles.md"), "w", encoding="utf-8").write("\n".join(pr) + "\n")

    # projects/<slug>.md
    projects = sorted({r.get("project") for r in rows
                       if r.get("scope") == "project" and r.get("project")})
    for p in projects:
        items = _active(rows, scope="project", project=p)
        body = [header, f"# Projeto: {p}\n"]
        if not items:
            body.append("_sem regras ativas_\n")
        for k, v in _group(items).items():
            body.append(f"\n## {KIND_LABEL.get(k, k)}\n")
            body += _bullets(v)
        open(os.path.join(PROJECTS_DIR, f"{p}.md"), "w", encoding="utf-8").write("\n".join(body) + "\n")

    render_evolution()


def render_evolution(limit=120):
    events = read_jsonl(EVENTS)[-limit:]
    icon = {"observed": "·", "promoted": "+", "superseded": "~", "archived": "-",
            "dropped": "x", "reactivated": "^", "absorbed": "<"}
    out = ["<!-- GERADO por cortex.py -->", "# Evolucao do raciocinio\n",
           "Linha do tempo do que foi aprendido, revisado e descartado.",
           "`+` promovido · `~` substituido (mudanca de opiniao) · `-` arquivado por desuso · "
           "`x` descartado sem evidencia · `<` veio de outra ferramenta\n"]
    cur = None
    for e in reversed(events):
        day = e["ts"][:10]
        if day != cur:
            out.append(f"\n## {day}\n")
            cur = day
        scope = e.get("project") or "global"
        note = f" ({e['note']})" if e.get("note") else ""
        out.append(f"- `{icon.get(e['event'],'?')}` **[{scope}]** {e.get('text','')}{note}")
    open(os.path.join(ROOT, "EVOLUTION.md"), "w", encoding="utf-8").write("\n".join(out) + "\n")


def cmd_render(a):
    render_all()
    if not a.quiet:
        print("renders atualizados")
    return 0


# ------------------------------------------------------------------ context
def cmd_context(a):
    rows = load()
    project = slugify(a.project) if a.project else None
    glob_items = _active(rows, scope="global")
    proj_items = _active(rows, scope="project", project=project) if project else []
    if not glob_items and not proj_items:
        if a.always:
            print("<cortex>Memoria Cortex vazia. Capture aprendizados com "
                  "`cortex observe` conforme ~/.claude/skills/cortex/SKILL.md.</cortex>")
        return 0

    def block(items, budget, title):
        # o `why` e' evidencia para revisao (fica nos renders .md e no store),
        # nao texto de inferencia: injetar so a regra.
        buf, used, shown = [f"### {title}"], 0, 0
        for k, v in _group(items).items():
            chunk = [f"**{KIND_LABEL.get(k,k)}**"] + [f"- {r['text']}" for r in
                     sorted(v, key=lambda x: (-x.get("seen", 1), x.get("updated","")))]
            text = "\n".join(chunk)
            if used + len(text) > budget:
                keep = []
                for line in chunk:
                    if used + len(line) > budget:
                        break
                    keep.append(line); used += len(line)
                if len(keep) > 1:
                    buf.append("\n".join(keep)); shown += len(keep) - 1
                break
            buf.append(text); used += len(text); shown += len(chunk) - 1
        # truncamento nunca e' silencioso: o excesso precisa aparecer na revisao
        if shown < len(items):
            buf.append(f"_({len(items) - shown} regra(s) deste bloco nao couberam no "
                       f"orcamento de {budget} chars — rode `cortex list` ou consolide.)_")
        return "\n".join(buf)

    out = ["<cortex-memoria>",
           "Memoria de trabalho persistente do usuario (fonte: ~/.claude/cortex). "
           "Aplique como preferencia forte; nao repita de volta ao usuario."]
    if glob_items:
        out.append(block(glob_items, CONTEXT_BUDGET, "Global"))
    if proj_items:
        out.append(block(proj_items, PROJECT_BUDGET, f"Projeto: {project}"))
    pend = [r for r in rows if r.get("status") == "candidate"]
    if pend:
        out.append(f"_{len(pend)} sinal(is) em observacao aguardando confirmacao._")
    out.append("</cortex-memoria>")
    print("\n".join(out))
    return 0


# --------------------------------------------------------------------- sync
def bridge_markdown():
    rows = load()
    g = _group(_active(rows, scope="global"))
    out = ["# Cortex — perfil operacional de %s (fonte unica: ~/.claude/cortex)" % USER_NAME,
           "",
           "Regras aprendidas com o uso real. Trate como preferencia forte do usuario.",
           ""]
    for k in ["identity", "preference", "workflow", "principle", "correction", "project", "reference"]:
        if g.get(k):
            out.append(f"## {KIND_LABEL[k]}")
            out += [f"- {r['text']}" + (f" — {r['why']}" if r.get("why") else "")
                    for r in sorted(g[k], key=lambda x: -x.get("seen", 1))]
            out.append("")
    projects = sorted({r.get("project") for r in rows
                       if r.get("scope") == "project" and r.get("status") == "active" and r.get("project")})
    if projects:
        out.append("## Projetos com regras proprias")
        for p in projects:
            out.append(f"- `{p}` → detalhes em `~/.claude/cortex/projects/{p}.md`")
        out.append("")
    out += [
        "## Como contribuir de volta (loop de aprendizado)",
        "Ao aprender algo novo e durável sobre como %s pensa, trabalha ou decide," % USER_NAME,
        "acrescente UMA linha JSON em `~/.claude/cortex/inbox/<sua-ferramenta>.jsonl`:",
        "",
        '```json',
        '{"kind":"preference","scope":"global","text":"regra em uma frase","why":"evidencia curta"}',
        '```',
        "",
        "Escopos: `global` ou `project` (com `\"project\":\"<slug-da-pasta>\"`).",
        "Tipos: identity | preference | principle | workflow | project | reference | correction.",
        "Nao escreva nos arquivos .md do Cortex: eles sao gerados.",
        "Regra de ouro: so registre o que se repetiu ou o que o usuario pediu explicitamente.",
    ]
    return "\n".join(out) + "\n"


def inject(path, content, comment_style="html"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    block = f"{MARK_BEGIN}\n{content}{MARK_END}\n"
    old = ""
    if os.path.exists(path):
        old = open(path, "r", encoding="utf-8").read()
    if MARK_BEGIN in old and MARK_END in old:
        pre = old.split(MARK_BEGIN)[0]
        post = old.split(MARK_END, 1)[1]
        new = pre + block + post
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + block
    if new != old:
        open(path, "w", encoding="utf-8").write(new)
        return True
    return False


def cmd_sync(a):
    rows = load()
    # nao espalhar para as outras ferramentas um perfil que encolheu de repente
    ativas = sum(1 for r in rows if r.get("status") == "active")
    try:
        antes = len(json.load(open(os.path.join(BRIDGE_DIR, "cortex.json"), encoding="utf-8")).get("rules", []))
    except (OSError, ValueError):
        antes = 0
    if antes and not PERMITIR_ENCOLHER and ativas < antes * (1 - SHRINK_MAX):
        log_event("sync_recusado", {}, f"{antes} -> {ativas} regras ativas")
        raise StoreError(f"sync recusado: as regras ativas cairiam de {antes} para {ativas}. "
                         "Se for intencional, repita com `cortex --permitir-encolher sync`.")
    render_all()
    md = bridge_markdown()
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    open(os.path.join(BRIDGE_DIR, "CORTEX.md"), "w", encoding="utf-8").write(md)
    json.dump({
        "generated": now_iso(),
        "source": "~/.claude/cortex/store.jsonl",
        "rules": [{k: r.get(k) for k in ("id","scope","project","kind","text","why","seen","explicit","updated")}
                  for r in rows if r.get("status") == "active"],
    }, open(os.path.join(BRIDGE_DIR, "cortex.json"), "w", encoding="utf-8"),
        ensure_ascii=False, indent=2)

    targets = [
        (os.path.join(HOME, ".codex", "AGENTS.md"), os.path.join(HOME, ".codex")),
        (os.path.join(HOME, ".gemini", "GEMINI.md"), os.path.join(HOME, ".gemini")),
        (os.path.join(HOME, ".config", "opencode", "AGENTS.md"), os.path.join(HOME, ".config", "opencode")),
        (os.path.join(HOME, ".cursor", "rules", "cortex.mdc"), os.path.join(HOME, ".cursor")),
        (os.path.join(HOME, ".antigravity", "AGENTS.md"), os.path.join(HOME, ".antigravity")),
        (os.path.join(HOME, ".agents", "AGENTS.md"), os.path.join(HOME, ".agents")),
    ]
    touched = []
    for path, guard in targets:
        if not os.path.isdir(guard):
            continue
        body = md
        if path.endswith(".mdc") and not os.path.exists(path):
            body = "---\ndescription: Perfil operacional do usuario (Cortex)\nalwaysApply: true\n---\n\n" + md
        if inject(path, body):
            touched.append(path.replace(HOME, "~"))
    if not a.quiet:
        print("bridge: " + (", ".join(touched) if touched else "nada mudou"))
    return 0


# ------------------------------------------------------------------- absorb
def cmd_absorb(a):
    os.makedirs(INBOX_DIR, exist_ok=True)
    total = 0
    for name in sorted(os.listdir(INBOX_DIR)):
        if not name.endswith(".jsonl"):
            continue
        src = os.path.splitext(name)[0]
        path = os.path.join(INBOX_DIR, name)
        for item in read_jsonl(path):
            text = (item.get("text") or "").strip()
            if not text:
                continue
            ns = argparse.Namespace(
                scope=item.get("scope", "global"),
                project=item.get("project"),
                kind=item.get("kind", "preference") if item.get("kind") in KINDS else "preference",
                text=text, why=item.get("why", ""),
                evidence=f"via {src}", explicit=False, source=src, supersedes=None,
            )
            if ns.scope == "project" and ns.project:
                # inbox manda slug direto
                rows = load()
                _id = rid("project", ns.project, text)
                ex = next((r for r in rows if r["id"] == _id), None)
                if ex:
                    ex["seen"] = ex.get("seen", 1) + 1; ex["updated"] = today()
                    ex["source"] = sorted(set(ex.get("source", []) + [src]))
                    if ex["status"] == "candidate" and ex["seen"] >= PROMOTE_THRESHOLD:
                        ex["status"] = "active"; log_event("promoted", ex, f"via {src}")
                    save(rows)
                else:
                    rec = {"id": _id, "scope": "project", "project": ns.project, "kind": ns.kind,
                           "text": text, "why": ns.why, "seen": 1, "explicit": False,
                           "status": "candidate", "created": today(), "updated": today(),
                           "source": [src], "evidence": [f"via {src}"], "supersedes": None}
                    rows.append(rec); save(rows); log_event("absorbed", rec, f"via {src}")
            else:
                ns.scope = "global"; ns.project = None
                cmd_observe(ns)
            total += 1
        os.makedirs(os.path.join(ROOT, "archive"), exist_ok=True)
        os.replace(path, os.path.join(ROOT, "archive", f"{src}-{today()}-{os.getpid()}.jsonl"))
    if not a.quiet:
        print(f"absorvidos: {total} sinal(is) de outras ferramentas")
    return 0


# ------------------------------------------------------------------ status
def cmd_status(a):
    rows = load()
    by = {}
    for r in rows:
        by[r.get("status", "?")] = by.get(r.get("status", "?"), 0) + 1
    events = read_jsonl(EVENTS)
    last7 = [e for e in events if days_since(e["ts"][:10]) <= 7]
    ev7 = {}
    for e in last7:
        ev7[e["event"]] = ev7.get(e["event"], 0) + 1
    projects = sorted({r.get("project") for r in rows
                       if r.get("scope") == "project" and r.get("status") == "active" and r.get("project")})
    print("Cortex — saude da memoria")
    print(f"  regras ativas .......... {by.get('active',0)}")
    print(f"  candidatas (observando)  {by.get('candidate',0)}")
    print(f"  substituidas ........... {by.get('superseded',0)}")
    print(f"  arquivadas ............. {by.get('archived',0)}")
    print(f"  projetos mapeados ...... {len(projects)}" + (f"  ({', '.join(projects)})" if projects else ""))
    print(f"  eventos nos ultimos 7d . {sum(ev7.values())} " +
          (str(ev7) if ev7 else ""))
    strong = [r for r in rows if r.get("status") == "active" and r.get("seen", 1) >= STRONG_SEEN]
    print(f"  regras consolidadas .... {len(strong)}")
    ratio = (by.get("active", 0) / max(1, len(rows))) * 100
    print(f"  sinal/ruido ............ {ratio:.0f}% do store e' regra ativa")
    return 0


def cmd_list(a):
    rows = load()
    for r in rows:
        if a.status and r.get("status") != a.status:
            continue
        if a.project and r.get("project") != slugify(a.project) and r.get("project") != a.project:
            continue
        print(f"{r['id']}  [{r.get('status')}] [{r.get('scope')}"
              f"{'/'+r['project'] if r.get('project') else ''}] "
              f"({r.get('kind')}, seen={r.get('seen',1)}) {r['text']}")
    return 0


def cmd_maintain(a):
    ns = argparse.Namespace(quiet=True)
    cmd_absorb(ns); cmd_promote(ns); cmd_prune(ns); cmd_sync(ns)
    if not a.quiet:
        cmd_status(a)
    return 0


# --------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(prog="cortex", description="Memoria evolutiva multi-agente")
    p.add_argument("--permitir-encolher", action="store_true",
                   help="aceita gravacao ou sync que reduza o store/perfil mais que o limite (intencional)")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("observe", help="registra um sinal aprendido")
    o.add_argument("text")
    o.add_argument("--kind", default="preference", choices=KINDS)
    o.add_argument("--scope", default="global", choices=["global", "project"])
    o.add_argument("--project", default=None, help="caminho do projeto (para --scope project)")
    o.add_argument("--why", default="", help="por que isso e' verdade (evidencia curta)")
    o.add_argument("--evidence", default=None)
    o.add_argument("--explicit", action="store_true", help="pedido direto do usuario: vira ativo na hora")
    o.add_argument("--source", default="claude-code")
    o.add_argument("--supersedes", default=None)
    o.set_defaults(func=cmd_observe)

    s = sub.add_parser("supersede", help="substitui uma regra por outra (mudanca de opiniao)")
    s.add_argument("old_id"); s.add_argument("text")
    s.add_argument("--why", default=""); s.add_argument("--project", default=None)
    s.add_argument("--source", default="claude-code")
    s.set_defaults(func=cmd_supersede)

    f = sub.add_parser("forget", help="arquiva uma regra")
    f.add_argument("id"); f.add_argument("--reason", default="")
    f.set_defaults(func=cmd_forget)

    c = sub.add_parser("context", help="imprime o bloco de contexto da sessao")
    c.add_argument("--project", default=None)
    c.add_argument("--always", action="store_true")
    c.set_defaults(func=cmd_context)

    for name, fn, helptxt in [
        ("promote", cmd_promote, "promove candidatos com evidencia suficiente"),
        ("prune", cmd_prune, "descarta o que nao se sustentou"),
        ("render", cmd_render, "regenera os arquivos .md"),
        ("sync", cmd_sync, "ensina as outras ferramentas locais"),
        ("absorb", cmd_absorb, "aprende com o inbox de outras ferramentas"),
        ("maintain", cmd_maintain, "absorb + promote + prune + sync"),
        ("status", cmd_status, "saude da memoria"),
    ]:
        sp = sub.add_parser(name, help=helptxt)
        sp.add_argument("--quiet", action="store_true")
        sp.set_defaults(func=fn)

    l = sub.add_parser("list", help="lista regras")
    l.add_argument("--status", default=None)
    l.add_argument("--project", default=None)
    l.add_argument("--quiet", action="store_true")
    l.set_defaults(func=cmd_list)

    a = p.parse_args()
    global PERMITIR_ENCOLHER
    PERMITIR_ENCOLHER = a.permitir_encolher
    os.makedirs(ROOT, exist_ok=True)
    if a.func not in (cmd_context, cmd_status, cmd_list):
        # todo comando que grava segura a trava do inicio ao fim; o maintain do fim de
        # sessao nao espera: se outra sessao ja esta mantendo, esta rodada pula
        if trava(esperar=a.func is not cmd_maintain) is None:
            if a.func is cmd_maintain:
                sys.exit(0)
            print(f"cortex: outro processo segura {LOCK} ha mais de {LOCK_WAIT_S}s; nada foi gravado",
                  file=sys.stderr)
            sys.exit(4)
    try:
        rc = a.func(a) or 0
    except StoreError as e:
        print(f"cortex: {e}", file=sys.stderr)
        rc = 3
    sys.exit(rc)


if __name__ == "__main__":
    main()
