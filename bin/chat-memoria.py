#!/usr/bin/python3
"""Memória por chat do Claude Code Desktop (parte do Cortex).

Cada chat do app tem um id estável (local_...) que sobrevive ao /clear; o id da sessão de CLI
muda. Este script liga os dois, guarda por chat: título canônico, regras e contexto, e:
  - SessionStart (startup/resume/clear/compact): injeta título, regras e contexto do chat;
  - UserPromptSubmit (1º prompt de cada sessão de CLI, inclusive após /clear): se o título do chat
    divergir do canônico, manda restaurá-lo; senão fica em silêncio.
Subcomandos para o Claude registrar o aprendizado: regra, contexto, titulo, mostrar, seed.
"""
import glob
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
SESS = os.path.join(HOME, "Library/Application Support/Claude/claude-code-sessions")
BASE = os.path.join(HOME, ".claude/cortex/chats")
VISTOS = os.path.join(BASE, ".vistos")
LIMITE = 7000


def ler(p, padrao):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def sessoes():
    for p in glob.glob(os.path.join(SESS, "*", "*", "local_*.json")):
        d = ler(p, None)
        if d and d.get("sessionId"):
            yield d


def achar_chat(cli_id):
    """Chat do app cuja sessão de CLI atual ou anterior é cli_id; None se não achar."""
    melhor = None
    for d in sessoes():
        ids = [d.get("cliSessionId")] + list(d.get("priorCliSessionIds") or [])
        if cli_id in ids and (melhor is None or d.get("lastActivityAt", 0) > melhor.get("lastActivityAt", 0)):
            melhor = d
    return melhor


def achar_por_id(local_id):
    return next((d for d in sessoes() if d["sessionId"] == local_id), None)


def caminho(local_id):
    return os.path.join(BASE, local_id + ".json")


def carregar(chat):
    rec = ler(caminho(chat["sessionId"]), None)
    if rec is None:
        rec = {"id": chat["sessionId"], "titulo": chat.get("title", ""), "cwd": chat.get("cwd", ""),
               "regras": [], "contexto": []}
        gravar(caminho(rec["id"]), rec)
    return rec


def montar(rec):
    linhas = [f'<chat-memoria chat="{rec["id"]}" titulo="{rec["titulo"]}">',
              "Memória persistente DESTE chat (sobrevive ao /clear). Aplique as regras como preferência forte "
              "e não repita este bloco ao usuário.",
              f"Título canônico do chat: \"{rec['titulo']}\" — é o nome do que o chat trata; mantenha-o."]
    if rec["regras"]:
        linhas.append("Regras do chat:")
        linhas += [f"- {r['texto']}" + (f" (porque: {r['porque']})" if r.get("porque") else "") for r in rec["regras"]]
    if rec["contexto"]:
        linhas.append("Contexto relevante:")
        linhas += [f"- {c}" for c in rec["contexto"][-25:]]
    linhas.append(
        "Aprendizado contínuo: quando o usuário der uma regra, correção ou preferência que valha para este "
        "chat (ou quando uma decisão/estado mudar), registre na hora, sem avisar:\n"
        f'  python3 ~/.claude/cortex/bin/chat-memoria.py regra --chat {rec["id"]} "<regra>" [--why "<motivo>"]\n'
        f'  python3 ~/.claude/cortex/bin/chat-memoria.py contexto --chat {rec["id"]} "<estado/decisão>"\n'
        f'  python3 ~/.claude/cortex/bin/chat-memoria.py esquecer --chat {rec["id"]} "<trecho da regra que caducou>"\n'
        "Só o que sobrevive à conversa; nada que o git ou o código já digam. Antes de agir, confira se o que vai "
        "fazer respeita as regras acima.")
    linhas.append("</chat-memoria>")
    return "\n".join(linhas)[:LIMITE]


def emitir(evento, texto):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": evento, "additionalContext": texto}},
                     ensure_ascii=False))


def chat_do_evento(ev):
    sid = ev.get("session_id", "")
    for _ in range(4):  # o app pode gravar o novo cliSessionId logo após o /clear
        chat = achar_chat(sid)
        if chat:
            return chat
        time.sleep(0.4)
    return None


def hook_start():
    ev = ler("/dev/stdin", {})
    if ev.get("source") not in ("startup", "resume", "clear", "compact"):
        return
    chat = chat_do_evento(ev)
    if chat:
        emitir("SessionStart", montar(carregar(chat)))


def hook_prompt():
    ev = ler("/dev/stdin", {})
    sid = ev.get("session_id", "")
    vistos = ler(VISTOS, {})
    if not sid or sid in vistos:
        return
    vistos[sid] = int(time.time())
    vistos = dict(sorted(vistos.items(), key=lambda kv: kv[1])[-500:])
    gravar(VISTOS, vistos)
    chat = achar_chat(sid)
    if not chat:
        return
    rec = carregar(chat)
    atual = chat.get("title", "")
    if atual == rec["titulo"]:
        return
    if chat.get("titleSource") == "user":  # renomeou à mão: a vontade do usuário vira o canônico
        rec["titulo"] = atual
        gravar(caminho(rec["id"]), rec)
        return
    emitir("UserPromptSubmit",
           f'<chat-memoria-titulo>O título deste chat ("{atual}") divergiu do canônico ("{rec["titulo"]}"). '
           f'Restaure com set_session_title (session_id "self", title "{rec["titulo"]}") antes de responder, '
           "sem comentar com o usuário.</chat-memoria-titulo>")


def arg(nome, padrao=None):
    a = sys.argv
    return a[a.index(nome) + 1] if nome in a else padrao


def texto_posicional():
    a, saida, pular = sys.argv[2:], [], False
    for i, x in enumerate(a):
        if pular:
            pular = False
        elif x in ("--chat", "--why"):
            pular = True
        else:
            saida.append(x)
    return " ".join(saida).strip()


def cmd_registro(cmd):
    chat = achar_por_id(arg("--chat", ""))
    if not chat:
        sys.exit("chat não encontrado (use --chat local_...)")
    rec = carregar(chat)
    t = texto_posicional()
    hoje = time.strftime("%d/%m/%Y")
    if cmd == "regra":
        if not any(r["texto"] == t for r in rec["regras"]):
            rec["regras"].append({"texto": t, "porque": arg("--why", ""), "quando": hoje})
    elif cmd == "contexto":
        rec["contexto"].append(f"[{hoje}] {t}")
        rec["contexto"] = rec["contexto"][-40:]
    elif cmd == "esquecer":
        rec["regras"] = [r for r in rec["regras"] if t.lower() not in r["texto"].lower()]
    elif cmd == "titulo":
        rec["titulo"] = t
    gravar(caminho(rec["id"]), rec)
    print("ok")


def cmd_mostrar():
    chat = achar_por_id(arg("--chat", ""))
    print(montar(carregar(chat)) if chat else "chat não encontrado")


def cmd_seed(docs):
    """Cria o registro de todos os chats com o título atual e o contexto dos documentos _CONTEXTO-CHATS."""
    por_id = {}
    for doc in docs:
        txt = open(doc, encoding="utf-8").read()
        for bloco in re.split(r"(?m)^## ", txt)[1:]:
            m = re.search(r"(local_[0-9a-f-]{36})", bloco.split("\n", 1)[0])
            if m:
                por_id[m.group(1)] = bloco
    n = 0
    for chat in sessoes():
        rec = ler(caminho(chat["sessionId"]), None) or carregar(chat)
        rec["titulo"] = chat.get("title", rec["titulo"])
        bloco = por_id.get(chat["sessionId"])
        if bloco and not rec["contexto"]:
            linhas = [l.strip("-* ").strip() for l in bloco.split("\n")[1:] if l.strip()]
            rec["contexto"] = [l for l in linhas if len(l) > 3][:25]
        gravar(caminho(rec["id"]), rec)
        n += 1
    print(f"{n} chats")


def main():
    if os.environ.get("CLAUDE_DESKTOP_SYNC"):
        return
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c == "hook-start":
        hook_start()
    elif c == "hook-prompt":
        hook_prompt()
    elif c in ("regra", "contexto", "esquecer", "titulo"):
        cmd_registro(c)
    elif c == "mostrar":
        cmd_mostrar()
    elif c == "seed":
        cmd_seed(sys.argv[2:])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - hook nunca pode travar a sessão
        pass
