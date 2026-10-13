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


if __name__ == "__main__":
    unittest.main()
