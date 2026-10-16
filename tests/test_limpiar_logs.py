"""limpiar_logs.py: recuento antes de aplicar, confirmación, copia de seguridad con
fecha, direcciones → <email> y turnos privados sin texto. Todo en una carpeta temporal."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import limpiar_logs as ll  # noqa: E402

RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL     = "pepe.garcia@example.com"


def turno(texto=None, respuesta=None, tools=(), privado=False, **extra):
    t = {"ts": "2026-10-16T10:15:00", "ruta": "claude", "tools": list(tools), "privado": privado,
         "total_ms": 900, "coste_usd": 0.001, **extra}
    if texto is not None:
        t["texto_usuario"] = texto
    if respuesta is not None:
        t["respuesta"] = respuesta
    return t


TURNOS = [
    turno("¿cuál es mi correo?", f"Tu correo es {EMAIL}.", tools=["get_mi_email"]),     # tool privada
    turno("escribe a pepe arroba gmail punto com", "Vale, preparo el correo."),          # dictada
    turno("qué hora es", "Son las diez."),                                               # normal
    turno("dime mi dirección", f"Es {EMAIL}, la de siempre."),                            # solo redactar
    turno(tools=["enviar_email"], privado=True),                                         # ya privado
    turno("lee la agenda", "Tienes una reunión.", tools=["ver_agenda"]),                  # tool privada
]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = tmp.name
        shutil.copy(os.path.join(RAIZ_REPO, "brain.py"), self.raiz)  # TOOLS_PRIVADAS reales
        os.makedirs(os.path.join(self.raiz, "logs"))
        self.turnos = os.path.join(self.raiz, "logs", "turnos.jsonl")
        with open(self.turnos, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(t, ensure_ascii=False) + "\n" for t in TURNOS)
        self.log = os.path.join(self.raiz, "jarvis.log")
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(f"🤖 Cargando Jarvis...\n🗣️ Tú: manda esto a {EMAIL}\n[GMAIL] token=\"FALSOFALSO1234\"\nlisto\n")

    def leer(self, ruta):
        with open(ruta, encoding="utf-8") as f:
            return f.read()

    def main(self, respuesta):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            codigo = ll.main(entrada=lambda _: respuesta, raiz=self.raiz)
        self.salida = salida.getvalue()
        return codigo


class TestRecuento(Base):
    def test_cuenta_sin_escribir(self):
        antes = self.leer(self.turnos), self.leer(self.log)
        plan = {os.path.basename(r): (n, c) for r, n, c in ll.analizar(self.raiz)}
        _, c = plan["turnos.jsonl"]
        self.assertEqual(c["turnos_privados"], 3)  # get_mi_email, dictada, ver_agenda
        self.assertEqual(c["emails"], 1)           # el de "dime mi dirección" (los otros se van con el texto)
        self.assertEqual(c["lineas"], 4)
        self.assertEqual(plan["jarvis.log"][1]["emails"], 1)
        self.assertEqual(antes, (self.leer(self.turnos), self.leer(self.log)))

    def test_el_informe_no_muestra_el_contenido(self):
        self.main("n")
        self.assertIn("3 turno(s) privado(s) pierden el texto", self.salida)
        for privado in (EMAIL, "pepe arroba", "Tienes una reunión", "FALSOFALSO"):
            self.assertNotIn(privado, self.salida)

    def test_lee_brain_sin_importarlo(self):
        privadas = ll.tools_privadas(self.raiz)
        self.assertIn("enviar_email", privadas)
        self.assertIn("ver_agenda", privadas)


class TestConfirmacion(Base):
    def test_sin_confirmar_no_cambia_nada(self):
        antes = self.leer(self.turnos), self.leer(self.log)
        for respuesta in ("", "n", "no", "vale"):
            self.assertEqual(self.main(respuesta), 1)
        self.assertEqual(antes, (self.leer(self.turnos), self.leer(self.log)))
        self.assertEqual(os.listdir(os.path.join(self.raiz, "logs")), ["turnos.jsonl"])

    def test_aplica_con_copia_de_seguridad(self):
        original = self.leer(self.turnos), self.leer(self.log)
        self.assertEqual(self.main("s"), 0)
        copias = [d for d in os.listdir(os.path.join(self.raiz, "logs")) if d.startswith("backup-")]
        self.assertEqual(len(copias), 1)
        datetime.strptime(copias[0], "backup-%Y%m%d-%H%M%S")
        copia = os.path.join(self.raiz, "logs", copias[0])
        self.assertEqual((self.leer(os.path.join(copia, "turnos.jsonl")), self.leer(os.path.join(copia, "jarvis.log"))),
                         original)
        self.assertIn(copias[0], self.salida)

    def test_resultado_redactado(self):
        self.main("sí")
        texto = self.leer(self.turnos)
        self.assertNotIn(EMAIL, texto)
        self.assertNotIn("arroba", texto)
        turnos = [json.loads(l) for l in texto.splitlines()]
        self.assertEqual(len(turnos), len(TURNOS))
        for i in (0, 1, 5):   # privados hoy: sin texto y marcados
            self.assertNotIn("texto_usuario", turnos[i])
            self.assertNotIn("respuesta", turnos[i])
            self.assertTrue(turnos[i]["privado"])
            self.assertEqual(turnos[i]["total_ms"], 900)      # las métricas se conservan
        self.assertEqual(turnos[2], TURNOS[2])                 # sin nada privado: intacto
        self.assertEqual(turnos[3]["respuesta"], "Es <email>, la de siempre.")
        self.assertEqual(turnos[4], TURNOS[4])
        log = self.leer(self.log)
        self.assertIn("manda esto a <email>", log)
        self.assertNotIn("FALSOFALSO1234", log)
        self.assertTrue(log.startswith("🤖 Cargando Jarvis...\n"))

    def test_idempotente(self):
        self.main("s")
        self.assertEqual(self.main("s"), 0)
        self.assertIn("Nada que limpiar", self.salida)


class TestRedaccion(unittest.TestCase):
    def test_direcciones_escritas_y_dictadas(self):
        for texto in (f"de {EMAIL} hoy", "a pepe arroba gmail punto com ya", "Para:<a.b-c@d.es>"):
            nuevo, n = ll.redactar(texto)
            self.assertIn("<email>", nuevo)
            self.assertGreaterEqual(n, 1)
            self.assertNotIn("@d.es", nuevo)

    def test_linea_no_json_tambien_se_redacta(self):
        nuevo, c = ll.limpiar_jsonl(f'{{"roto": "{EMAIL}"\n', frozenset())
        self.assertNotIn(EMAIL, nuevo)
        self.assertEqual(c["ilegibles"], 1)

    def test_otros_archivos_de_logs(self):
        with tempfile.TemporaryDirectory() as raiz:
            shutil.copy(os.path.join(RAIZ_REPO, "brain.py"), raiz)
            os.makedirs(os.path.join(raiz, "logs", "backup-20260101-000000"))
            for nombre in ("turnos.1.jsonl", "otro.txt"):
                with open(os.path.join(raiz, "logs", nombre), "w", encoding="utf-8") as f:
                    f.write(json.dumps(turno("hola", EMAIL)) + "\n")
            with open(os.path.join(raiz, "logs", "binario.bin"), "wb") as f:
                f.write(b"\xff\xfe\x00")
            plan = {os.path.basename(r): c for r, _, c in ll.analizar(raiz)}
        self.assertEqual(set(plan), {"turnos.1.jsonl", "otro.txt", "binario.bin"})  # sin la copia
        self.assertEqual(plan["turnos.1.jsonl"]["emails"], 1)
        self.assertEqual(plan["otro.txt"]["emails"], 1)
        self.assertIn("omitido", plan["binario.bin"])


if __name__ == "__main__":
    unittest.main()
