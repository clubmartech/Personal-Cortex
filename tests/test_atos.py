"""Trilhas de raciocínio: classificação, registro, destilação, hook e importação."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"


class Atos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        os.environ["CORTEX_HOME"] = self.tmp.name
        # a ponte nunca pode tocar o AGENTS.md real de quem roda os testes
        os.environ["CORTEX_PONTE_ALVOS"] = os.path.join(self.tmp.name, "ponte-AGENTS.md")
        self.addCleanup(os.environ.pop, "CORTEX_PONTE_ALVOS", None)
        sys.path.insert(0, str(BIN))
        import importlib
        import atos
        self.a = importlib.reload(atos)

    def test_classificacao(self):
        casos = {
            "é possível criar um mecanismo assim?": "viabilidade",
            "desenha o pack de áudio": "desenho",
            "pode implementar?": "execucao",
            "está preparado para aplicar? o que faz?": "consulta",
            "faça o push e o deploy": "publicacao",
            "rode a validação completa": "avaliacao",
            "não era isso, volta como estava": "correcao",
            "de agora em diante responda em tópicos": "preferencia",
            "envie um comando para a outra sessão": "delegacao",
            "bom dia": "outro",
        }
        for texto, esperado in casos.items():
            self.assertEqual(self.a.classificar(texto)[0], esperado, texto)
        self.assertIn("encadeado", self.a.classificar("quando finalizar, publique")[2])

    def test_tags_e_regras_proprias(self):
        self.assertEqual(self.a.classificar("<system-reminder>faça tudo</system-reminder> bom dia")[0], "outro")
        Path(self.a.REGRAS).write_text(json.dumps({"padroes": {"desenho": [r"\bwireframe\b"]}, "formas": {"desenho": "X"}}))
        self.assertEqual(self.a.classificar("monta um wireframe")[0], "desenho")
        self.assertEqual(self.a.regras()[1]["desenho"], "X")

    def test_trilha_e_hook(self):
        for i in range(3):
            s = "s%d" % i
            self.a.registrar("desenha o fluxo", s, "/p/Projeto X", "2026-10-1%dT10:00:00" % i)
            self.a.registrar("pode implementar", s, "/p/Projeto X", "2026-10-1%dT10:05:00" % i)
        d = self.a.destilar()
        self.assertEqual(d["transicoes"]["desenho"]["execucao"], 3)
        self.assertIn("desenho ou plano", Path(self.a.TRILHAS_MD).read_text())
        out = self.a.hook_prompt(json.dumps({"session_id": "nova", "cwd": "/p/Projeto X", "prompt": "desenha a tela"}))
        self.assertIn("costuma vir depois: execução (3/3)", out)
        out = self.a.hook_prompt(json.dumps({"session_id": "nova", "cwd": "/p/Projeto X", "prompt": "pode seguir com isso"}))
        self.assertIn("anterior nesta conversa: desenho", out)
        self.assertIn("não autoriza", out)
        ctx = self.a.contexto("projeto-x", limite=300)
        self.assertTrue(ctx.endswith("</cortex-trilhas>") and len(ctx) <= 300)
        self.assertEqual(self.a.hook_prompt("não é json"), "")

    def test_importar_so_humano(self):
        proj = Path(self.tmp.name) / "projects" / "p1"
        proj.mkdir(parents=True)
        linhas = [
            {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/a", "timestamp": "2026-10-12T10:00:00Z",
             "turnOrigin": "human", "origin": {"kind": "human"}, "message": {"content": "desenha a tela"}},
            {"type": "user", "uuid": "u2", "sessionId": "s", "cwd": "/a", "timestamp": "2026-10-12T10:01:00Z",
             "isMeta": True, "message": {"content": "meta"}},
            {"type": "user", "uuid": "u3", "sessionId": "s", "cwd": "/a", "timestamp": "2026-10-12T10:02:00Z",
             "message": {"content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "assistant", "uuid": "u4", "message": {"content": "ok"}},
        ]
        (proj / "t.jsonl").write_text("\n".join(json.dumps(x) for x in linhas))
        self.assertEqual(self.a.importar([str(proj.parent)]), 1)
        self.assertEqual(self.a.importar([str(proj.parent)]), 0)
        self.assertTrue(self.a.ler_todos()[0]["transcript"].endswith("t.jsonl"))


    def test_dominio_e_riscos(self):
        _, _, dominios, _ = self.a.regras()
        self.assertEqual(set(self.a.dominios_de("roteiro do reels com legenda", dominios)), {"audiovisual", "textual"})
        self.assertIn("referencia_vaga", self.a.classificar("arruma isso aí")[2])
        self.assertIn("pedidos_multiplos", self.a.classificar("avalie, implemente e faça o deploy")[2])
        self.assertEqual(self.a.classificar("faca o deploy da publicacao")[0], "publicacao")

    def test_digitacao_pelo_vocabulario(self):
        vocab = [("implementa", 40), ("carrossel", 30), ("cortex", 50)]
        self.assertEqual(self.a.digitacao("implemnta o carossel no cortex", vocab), [("implemnta", "implementa"), ("carossel", "carrossel")])
        self.assertEqual(self.a.digitacao("implementa", vocab), [])
        self.assertTrue(self.a._dist1("cortxe", "cortex"))
        self.assertEqual(self.a.classificar(self.a.corrigir("implemnta o carossel", self.a.digitacao("implemnta o carossel", vocab)))[0], "execucao")

    def test_falhas_linha_e_ponte(self):
        t = tempfile.mkdtemp(dir=self.tmp.name)
        tr = os.path.join(t, "s.jsonl")
        Path(tr).write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Entreguei o roteiro."}]}}))
        for i in range(2):
            s = "c%d" % i
            self.a.registrar("roteiro do vídeo sobre isso", s, "/p/X", "2026-10-1%dT10:00:00" % i, transcript=tr)
            self.a.registrar("não era isso, quis dizer o reels", s, "/p/X", "2026-10-1%dT10:05:00" % i, transcript=tr)
        alvo = os.path.join(t, "AGENTS.md")
        Path(alvo).write_text("# Regras do Codex\n")
        os.environ["CORTEX_PONTE_ALVOS"] = alvo
        d = self.a.destilar()
        self.assertEqual(d["falhas"]["antes_por_dominio"].get("audiovisual"), 2)
        self.assertIn("referencia_vaga", d["falhas"]["antes_por_sinal"])
        texto = Path(alvo).read_text()
        self.assertTrue(texto.startswith("# Regras do Codex"))
        self.assertIn("Conteúdo audiovisual", texto)
        self.a.destilar()
        self.assertEqual(Path(alvo).read_text().count(self.a.MARCA_INI), 1)
        l = self.a.linha(desde="2026-10-10", dominio="audiovisual")
        self.assertIn("Output final:** Entreguei o roteiro.", l)
        self.assertIn("Desvio corrigido", l)
        out = json.loads(self.a.hook_prompt(json.dumps({"session_id": "n", "cwd": "/p/X", "prompt": "roteiro do vídeo novo"})))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("domínio: audiovisual", ctx)

    def test_codex_ignora_exec_e_contexto_injetado(self):
        base = Path(self.tmp.name) / "codex" / "2026"
        base.mkdir(parents=True)
        def sessao(nome, originator, textos):
            linhas = [{"type": "session_meta", "payload": {"id": nome, "cwd": "/p/Y", "originator": originator}}]
            linhas.append({"type": "response_item", "timestamp": "2026-10-12T10:00:00Z", "payload": {"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": t} for t in textos]}})
            (base / (nome + ".jsonl")).write_text("\n".join(json.dumps(x) for x in linhas))
            return str(base / (nome + ".jsonl"))
        sessao("humano", "Codex Desktop", ["# AGENTS.md instructions\n<INSTRUCTIONS>x", "<environment_context>y</environment_context>", "gera o carrossel do post"])
        exec_ = sessao("auto", "codex_exec", ["You are already Codex. gera a imagem"])
        self.assertEqual(self.a.importar(claude=[], codex=[str(base.parent)]), 1)
        regs = self.a.ler_todos()
        self.assertEqual([r["texto"] for r in regs], ["gera o carrossel do post"])
        self.assertEqual(regs[0]["fonte"], "codex")
        self.assertEqual(self.a.hook_prompt(json.dumps({"session_id": "auto", "cwd": "/p/Y", "prompt": "gera", "transcript_path": exec_}), "codex"), "")


if __name__ == "__main__":
    unittest.main()
