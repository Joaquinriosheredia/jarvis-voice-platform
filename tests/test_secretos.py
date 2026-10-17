"""Enmascarado de secretos: skills/secretos.py y su aplicación en las tools de lectura de
código de skills/operador.py. Sin red ni git real: subprocess mockeado y proyecto temporal."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
from skills import operador, secretos  # noqa: E402
from skills.secretos import redactar, redactar_secretos  # noqa: E402

CLAVE = "sk-live-4f9a8b7c6d5e4f3a2b1c"
JWT   = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
PRIVADA = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAx4f9\nQm9yZSBkYXRhIGhlcmU=\n"
           "-----END RSA PRIVATE KEY-----")


def completado(salida, codigo=0):
    return subprocess.CompletedProcess([], codigo, salida, "")


# ── Patrones ──────────────────────────────────────────────────────
class TestPatrones(unittest.TestCase):
    def comprobar(self, texto, secreto, conservado, tipo):
        red, n = redactar(texto)
        self.assertNotIn(secreto, red, texto)
        self.assertIn(conservado, red, texto)
        self.assertIn(f"[SECRETO-REDACTADO:{tipo}]", red, texto)
        self.assertEqual(n, 1, texto)

    def test_prefijos_conocidos(self):
        for texto, secreto, conservado, tipo in (
            (f'API_KEY = "{CLAVE}"', CLAVE, 'API_KEY = "', "sk"),
            ('k = "sk-ant-api03-abcdefghijklmnop1234"', "sk-ant-api03-abcdefghijklmnop1234", 'k = "', "sk"),
            ("cliente = Anthropic(sk-abcdefghij0123456789)", "sk-abcdefghij0123456789", "cliente = Anthropic(", "sk"),
            ("aws_id = AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE", "aws_id = ", "aws"),
            ("sts ASIAABCDEFGHIJKLMNOP fin", "ASIAABCDEFGHIJKLMNOP", "sts ", "aws"),
            ('gh = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"', "ghp_abcdefghij", 'gh = "', "github"),
            ("gho_abcdefghijklmnopqrstuvwxyz0123 en uso", "gho_abcdefghij", " en uso", "github"),
            ("ghs_abcdefghijklmnopqrstuvwxyz0123 en uso", "ghs_abcdefghij", " en uso", "github"),
            ("pat: github_pat_11ABCDEFG0123456789_abcdefghijkl", "github_pat_11", "pat: ", "github"),
            ("SLACK = xoxb-123456789012-abcdefghij", "xoxb-1234", "SLACK = ", "slack"),
            ("hook xoxp-123456789012-abcdefghij", "xoxp-1234", "hook ", "slack"),
            ('g = "AIzaSyA1234567890abcdefghijklmnopqrstuv"', "AIzaSyA123", 'g = "', "google"),
            ("gl = glpat-abcdefghij0123456789", "glpat-abc", "gl = ", "gitlab"),
            (f"cookie {JWT} fin", JWT, "cookie ", "jwt"),
        ):
            self.comprobar(texto, secreto, conservado, tipo)

    def test_asignaciones_sospechosas_conservan_el_nombre(self):
        for texto, secreto, conservado, tipo in (
            ('password = "hunter2hunter2"', "hunter2hunter2", 'password = "', "password"),
            ("DB_PASSWORD=s3cr3tP4ss", "s3cr3tP4ss", "DB_PASSWORD=", "password"),
            ('db_passwd: "Zq9mK2pLx"', "Zq9mK2pLx", 'db_passwd: "', "password"),
            ("pwd = Qw3rtyUiop", "Qw3rtyUiop", "pwd = ", "password"),
            ("client_secret: abcd1234efgh", "abcd1234efgh", "client_secret: ", "client_secret"),
            ('"api-key": "a1b2c3d4e5f6g7h8"', "a1b2c3d4e5f6g7h8", '"api-key": "', "api_key"),
            ("APP_SECRET='9f8e7d6c5b4a'", "9f8e7d6c5b4a", "APP_SECRET='", "secret"),
            ("refresh_token => zxcv5678bnmq", "zxcv5678bnmq", "refresh_token => ", "token"),
            ("private_key = 'MIIEvQIBADANBg'", "MIIEvQIBADANBg", "private_key = '", "private_key"),
            ('"authorization": "tok3n-abcdef"', "tok3n-abcdef", '"authorization": "', "authorization"),
        ):
            self.comprobar(texto, secreto, conservado, tipo)

    def test_cabeceras_bearer_y_basic(self):
        self.comprobar("Authorization: Bearer abc123def456ghi", "abc123def456ghi",
                       "Authorization: Bearer ", "bearer")
        self.comprobar('headers = {"Authorization": "Bearer abc123def456ghi"}', "abc123def456ghi",
                       '{"Authorization": "Bearer ', "bearer")
        self.comprobar("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA==",
                       "Authorization: Basic ", "basic")

    def test_url_con_credenciales_solo_la_clave(self):
        self.comprobar("DATABASE_URL=postgres://admin:S3cretPass@db.local:5432/app", "S3cretPass",
                       "postgres://admin:", "url")
        red = redactar_secretos("git remote: https://usuario:ghx9Kd02LmQ@github.com/u/r.git")
        self.assertIn("https://usuario:[SECRETO-REDACTADO:url]@github.com/u/r.git", red)

    def test_url_clave_corta_siempre_se_enmascara(self):
        self.assertEqual(redactar("mysql://u:p@h"), ("mysql://u:[SECRETO-REDACTADO:url]@h", 1))
        self.assertEqual(redactar("redis://:abc@cache:6379/0"),  # solo contraseña, sin usuario
                         ("redis://:[SECRETO-REDACTADO:url]@cache:6379/0", 1))
        una, n = redactar("conn = 'ftp://ana:1234@srv/x' y 'mysql://u:p@h'")
        self.assertEqual(n, 2)
        self.assertEqual(redactar(una), (una, 0))  # idempotente

    def test_url_sin_credenciales_o_solo_usuario_no_se_toca(self):
        for texto in ("https://github.com/u/r.git", "https://usuario@host/ruta",
                      "http://localhost:8080/api", "ssh://git@github.com:22/u/r.git"):
            self.assertEqual(redactar(texto), (texto, 0), texto)

    def test_clave_privada_completa_un_solo_marcador_y_mismas_lineas(self):
        texto = f"antes\n{PRIVADA}\ndespués"
        red, n = redactar(texto)
        self.assertEqual(n, 1)
        self.assertEqual(red.count("[SECRETO-REDACTADO:clave_privada]"), 1)
        self.assertNotIn("MIIEowIBAAKCAQEAx4f9", red)
        self.assertNotIn("END RSA PRIVATE KEY", red)
        self.assertTrue(red.startswith("antes\n") and red.endswith("\ndespués"))
        self.assertEqual(red.count("\n"), texto.count("\n"))  # la numeración de líneas no cambia

    def test_clave_privada_cortada_hasta_el_final(self):
        red, n = redactar("x = 1\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0\nQm9yZSBk")
        self.assertEqual(n, 1)
        self.assertTrue(red.startswith("x = 1\n[SECRETO-REDACTADO:clave_privada]"))
        self.assertNotIn("MIIEvQ", red)
        self.assertNotIn("Qm9yZSBk", red)

    def test_falsos_positivos_no_se_enmascaran(self):
        for texto in ('password = "changeme"', 'token = "xxxxxxxx"', 'token = "<token>"',
                      'api_key = "${API_KEY}"', 'api_key = os.environ["API_KEY"]', "password = None",
                      'api_key = os.getenv("API_KEY")', "token = settings.API_TOKEN",
                      "El token de sesión caduca a los diez minutos y hay que pedir otro.",
                      "commit 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                      "id = 123e4567-e89b-12d3-a456-426614174000",
                      'headers={"Authorization": API_KEY}', "max_tokens=500", 'secret = "abc12"',
                      'TOKEN_PATH = r"C:\\Jarvis-secrets\\token.json"', 'token_env = "GITHUB_TOKEN"',
                      "tokenizer = AutoTokenizer.from_pretrained('x')"):
            self.assertEqual(redactar(texto), (texto, 0), texto)

    def test_idempotente(self):
        texto = (f'API_KEY = "{CLAVE}"\npassword = "hunter2hunter2"\nAuthorization: Bearer abc123def456ghi\n'
                 f"postgres://a:S3cretPass@h/x\n{PRIVADA}\n{JWT}\nAKIAIOSFODNN7EXAMPLE")
        una, n1 = redactar(texto)
        dos, n2 = redactar(una)
        self.assertEqual(una, dos)
        self.assertEqual((n1, n2), (7, 0))

    def test_aviso_solo_si_hay_secretos(self):
        self.assertEqual(secretos.aviso(0), "")
        self.assertEqual(secretos.aviso(2), "\n[2 secreto(s) enmascarado(s) en esta salida]")
        self.assertEqual(redactar(""), ("", 0))


# ── Aplicación en las tools de lectura de código ──────────────────
class ProyectoTemporal(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        p = patch.dict(operador.PROYECTOS, {"tmp": self.dir})
        p.start()
        self.addCleanup(p.stop)

    def escribir(self, ruta, contenido):
        with open(os.path.join(self.dir, ruta), "w", encoding="utf-8") as f:
            f.write(contenido)


class TestTools(ProyectoTemporal):
    def assertRedactado(self, msg, n=1):
        self.assertNotIn(CLAVE, msg)
        self.assertNotIn(CLAVE[8:], msg)
        self.assertIn("[SECRETO-REDACTADO:sk]", msg)
        self.assertTrue(msg.endswith(f"[{n} secreto(s) enmascarado(s) en esta salida]"), msg[-80:])

    def test_leer_archivo(self):
        self.escribir("app.py", f'import os\nAPI_KEY = "{CLAVE}"\nprint(1)\n')
        ok, msg = operador.leer_archivo("tmp", "app.py")
        self.assertTrue(ok)
        self.assertIn('API_KEY = "[SECRETO-REDACTADO:sk]"', msg)
        self.assertIn("líneas 1-3", msg)
        self.assertRedactado(msg)

    def test_leer_archivo_clave_privada_conserva_la_numeracion(self):
        self.escribir("k.txt", f"a\n{PRIVADA}\nb\nc\n")
        ok, msg = operador.leer_archivo("tmp", "k.txt", 1, 7)
        self.assertIn("líneas 1-7", msg)
        self.assertTrue(msg.split(":\n", 1)[1].startswith("a\n[SECRETO-REDACTADO:clave_privada]\n"))
        self.assertIn("\nb\nc\n", msg)
        self.assertNotIn("MIIEowIBAAKCAQEAx4f9", msg)

    def test_git_diff(self):
        diff = f'diff --git a/app.py b/app.py\n+API_KEY = "{CLAVE}"\n+x = 1\n'
        with patch("skills.operador.subprocess.run", return_value=completado(diff)):
            ok, msg = operador.git_diff("tmp")
        self.assertTrue(ok)
        self.assertIn('+API_KEY = "[SECRETO-REDACTADO:sk]"', msg)
        self.assertRedactado(msg)

    def test_git_log_mensajes_de_commit(self):
        log = f"a1b2c3d quita la clave {CLAVE} del código\n9f8e7d6 primer commit\n"
        with patch("skills.operador.subprocess.run", return_value=completado(log)):
            ok, msg = operador.git_log("tmp", n=2)
        self.assertTrue(ok)
        self.assertIn("a1b2c3d quita la clave [SECRETO-REDACTADO:sk] del código", msg)
        self.assertIn("9f8e7d6 primer commit", msg)  # hashes intactos
        self.assertRedactado(msg)

    def test_buscar_en_codigo(self):
        respuestas = [completado("true\n"), completado(f'app.py:2:API_KEY = "{CLAVE}"\n')]
        with patch("skills.operador.subprocess.run", side_effect=respuestas):
            ok, msg = operador.buscar_en_codigo("tmp", "API_KEY")
        self.assertTrue(ok)
        self.assertIn('app.py:2:API_KEY = "[SECRETO-REDACTADO:sk]"', msg)
        self.assertRedactado(msg)

    def test_buscar_en_codigo_el_patron_buscado_tampoco_sale(self):
        respuestas = [completado("true\n"), completado("", 1)]
        with patch("skills.operador.subprocess.run", side_effect=respuestas):
            ok, msg = operador.buscar_en_codigo("tmp", CLAVE)
        self.assertNotIn(CLAVE, msg)
        self.assertIn("sin coincidencias", msg)

    def test_ejecutar_tests(self):
        salida = f"FAIL: test_api\nAssertionError: '{CLAVE}' != None\nRan 3 tests\nFAILED (failures=1)\n"
        with patch("skills.operador.subprocess.run", return_value=completado(salida, 1)):
            ok, msg = operador.ejecutar_tests("jarvis")
        self.assertFalse(ok)
        self.assertIn("Ran 3 tests", msg)
        self.assertRedactado(msg)

    def test_sin_secretos_no_hay_linea_final(self):
        with patch("skills.operador.subprocess.run", return_value=completado("+x = 1\n")):
            ok, msg = operador.git_diff("tmp")
        self.assertNotIn("enmascarado", msg)
        self.escribir("a.py", "x = 1\n")
        self.assertNotIn("enmascarado", operador.leer_archivo("tmp", "a.py")[1])

    def test_varios_secretos_cuentan_en_la_linea_final(self):
        diff = f'+API_KEY = "{CLAVE}"\n+password = "hunter2hunter2"\n+{JWT}\n'
        with patch("skills.operador.subprocess.run", return_value=completado(diff)):
            ok, msg = operador.git_diff("tmp")
        self.assertTrue(msg.endswith("[3 secreto(s) enmascarado(s) en esta salida]"))

    def test_no_hay_parametro_para_saltarla(self):
        import inspect
        for f in (operador.leer_archivo, operador.git_diff, operador.git_log,
                  operador.buscar_en_codigo, operador.ejecutar_tests):
            self.assertTrue(hasattr(f, "__wrapped__"), f.__name__)  # pasa por _con_redaccion
            params = set(inspect.signature(f).parameters)
            self.assertFalse({p for p in params if "redact" in p or "secret" in p or "raw" in p}, f.__name__)

    def test_el_modelo_recibe_la_salida_redactada(self):
        diff = f'+API_KEY = "{CLAVE}"\n'
        with patch("skills.operador.subprocess.run", return_value=completado(diff)), \
             patch("builtins.print"):
            res = brain._ejecutar("git_diff", {"proyecto": "jarvis"}, {}, "revisa mis cambios")
        self.assertTrue(res["ok"])
        self.assertNotIn(CLAVE, json.dumps(res, ensure_ascii=False))
        self.assertIn("[SECRETO-REDACTADO:sk]", res["message"])


# ── Redacción antes del truncado ──────────────────────────────────
class TestAntesDelTruncado(ProyectoTemporal):
    def test_secreto_en_el_limite_de_bytes_del_diff_no_sale_a_medias(self):
        relleno = "+" + "a" * (operador.MAX_BYTES - 18) + "\n"  # MAX_BYTES - 16 bytes
        diff = relleno + f'+K="{CLAVE}"\n+fin\n'  # el corte cae 16 bytes dentro de la línea de la clave
        # Sin redacción, el truncado dejaría '+K="sk-live-4f9a' a la vista
        self.assertIn(CLAVE[:12], operador._truncar(diff.strip()))
        with patch("skills.operador.subprocess.run", return_value=completado(diff)):
            ok, msg = operador.git_diff("tmp")
        self.assertIn("(truncado)", msg)
        for i in range(4, len(CLAVE)):
            self.assertNotIn(CLAVE[:i], msg)  # ni un trozo del principio de la clave
        self.assertTrue(msg.endswith("[1 secreto(s) enmascarado(s) en esta salida]"))

    def test_secreto_al_final_de_una_linea_ancha_de_busqueda(self):
        linea = "app.py:1:" + "x" * (operador.MAX_ANCHO_LINEA - 20) + f' K="{CLAVE}"'
        with patch("skills.operador.subprocess.run", side_effect=[completado("true\n"), completado(linea)]):
            ok, msg = operador.buscar_en_codigo("tmp", "xxx")
        for i in range(4, len(CLAVE)):
            self.assertNotIn(CLAVE[:i], msg)

    def test_secreto_en_el_limite_de_la_cola_de_tests(self):
        salida = f"T={CLAVE}" + "b" * (operador.MAX_COLA_TEST - 10) + "\nRan 1 test\nOK\n"
        with patch("skills.operador.subprocess.run", return_value=completado(salida)):
            ok, msg = operador.ejecutar_tests("jarvis")
        for i in range(4, len(CLAVE)):
            self.assertNotIn(CLAVE[:i], msg)
        for i in range(4, len(CLAVE)):
            self.assertNotIn(CLAVE[-i:], msg)


# ── SYSTEM ────────────────────────────────────────────────────────
class TestSystem(unittest.TestCase):
    def test_system_review_explica_el_marcador(self):
        for texto in (brain.SYSTEM_REVIEW, brain.SYSTEM):
            self.assertIn("[SECRETO-REDACTADO", texto)
            self.assertIn("hallazgo de seguridad de alta gravedad", texto)
            self.assertIn("Nunca intentes reconstruir ni adivinar el valor", texto)
        self.assertNotIn("No repitas secretos", brain.SYSTEM_REVIEW)


if __name__ == "__main__":
    unittest.main()
