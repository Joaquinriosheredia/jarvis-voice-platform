"""buscar_en_navegador: abre una URL de búsqueda en el navegador por defecto.
Sin abrir nada real: webbrowser.open mockeado."""
import os
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
from skills import webs  # noqa: E402

BASES = {  # motor -> (esquema+host+ruta, parámetro de la consulta)
    "google":    ("https://www.google.com/search", "q"),
    "amazon":    ("https://www.amazon.es/s", "k"),
    "youtube":   ("https://www.youtube.com/results", "search_query"),
    "wikipedia": ("https://es.wikipedia.org/w/index.php", "search"),
    "maps":      ("https://www.google.com/maps/search/", "query"),
    "github":    ("https://github.com/search", "q"),
}


def buscar(motor, consulta):
    with patch("skills.webs.webbrowser.open") as abrir:
        ok, msg = webs.buscar_en_navegador(motor, consulta)
    url = abrir.call_args.args[0] if abrir.called else None
    return ok, msg, url, abrir


def partes(url):
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}", parse_qs(u.query, keep_blank_values=True)


class TestUrls(unittest.TestCase):
    def test_cada_motor_produce_su_url_con_la_consulta_codificada(self):
        consulta = 'colchón "viscoelástico" 90x190 & #oferta'
        for motor, (base, param) in BASES.items():
            ok, msg, url, abrir = buscar(motor, consulta)
            self.assertTrue(ok, msg)
            abrir.assert_called_once()
            self.assertEqual(partes(url)[0], base, motor)
            self.assertEqual(partes(url)[1][param], [consulta], motor)
            self.assertNotIn(" ", url)
            self.assertNotIn('"', url)
            self.assertNotIn("#", url)  # el # no corta la URL en un fragmento
            self.assertEqual(urlsplit(url).fragment, "")

    def test_url_exacta_de_ejemplo(self):
        _, _, url, _ = buscar("google", "recetas de lentejas")
        self.assertEqual(url, "https://www.google.com/search?q=recetas+de+lentejas")
        _, _, url, _ = buscar("maps", "farmacia & guardia")
        self.assertEqual(url, "https://www.google.com/maps/search/?api=1&query=farmacia+%26+guardia")
        _, _, url, _ = buscar("amazon", "café")
        self.assertEqual(url, "https://www.amazon.es/s?k=caf%C3%A9")

    def test_mensaje_sin_url(self):
        ok, msg, url, _ = buscar("amazon", "colchones")
        self.assertEqual((ok, msg), (True, "Buscando colchones en Amazon."))
        self.assertNotIn("http", msg)
        self.assertEqual(buscar("maps", "Sevilla")[1], "Buscando Sevilla en Google Maps.")
        self.assertTrue(buscar(" YouTube ", "lofi")[0])  # mayúsculas y espacios en el motor


class TestEntradasInvalidas(unittest.TestCase):
    def test_motor_invalido_no_abre_nada(self):
        for motor in ("bing", "https://evil.com", "", None, 3, "google.com"):
            ok, msg, url, abrir = buscar(motor, "algo")
            self.assertFalse(ok, motor)
            abrir.assert_not_called()
            self.assertIn("google, amazon, youtube, wikipedia, maps, github", msg)

    def test_consulta_vacia_no_abre_nada(self):
        for consulta in ("", "   ", "\n\t\x00", None, 42):
            ok, msg, url, abrir = buscar("google", consulta)
            self.assertFalse(ok, repr(consulta))
            abrir.assert_not_called()
            self.assertIn("vacía", msg)

    def test_caracteres_de_control_se_limpian(self):
        ok, msg, url, _ = buscar("google", "hola\x00\x07  mundo\r\n\tadiós\x1b")
        self.assertEqual(partes(url)[1]["q"], ["hola mundo adiós"])
        self.assertEqual(msg, "Buscando hola mundo adiós en Google.")

    def test_consulta_larga_se_trunca_a_200(self):
        ok, msg, url, _ = buscar("google", "a" * 500)
        self.assertTrue(ok)
        self.assertEqual(partes(url)[1]["q"], ["a" * webs.MAX_CONSULTA])
        self.assertEqual(webs.MAX_CONSULTA, 200)


class TestInyeccion(unittest.TestCase):
    def test_la_consulta_queda_dentro_del_parametro_y_el_dominio_no_cambia(self):
        for consulta in ("x&site=evil.com", "javascript:alert(1)", "a\nLocation: https://evil.com",
                         "https://evil.com/?q=1", "../../evil", "x?q=evil#frag", "@evil.com", "%0d%0a"):
            for motor, (base, param) in BASES.items():
                ok, _, url, _ = buscar(motor, consulta)
                self.assertTrue(ok)
                self.assertEqual(partes(url)[0], base, (motor, consulta))
                self.assertEqual(urlsplit(url).netloc, urlsplit(base).netloc)
                query = partes(url)[1]
                self.assertEqual(query[param], [webs._limpiar_consulta(consulta)], (motor, consulta))
                self.assertNotIn("site", query)
                self.assertNotIn("\n", url)
                self.assertTrue(url.startswith("https://"))


class TestBrain(unittest.TestCase):
    def test_riesgo_y_schema(self):
        self.assertEqual(brain.TOOL_RISK["buscar_en_navegador"], brain.TOOL_RISK["abrir_web"])
        self.assertEqual(brain.TOOL_RISK["buscar_en_navegador"], "EXTERNAL")
        tool = next(t for t in brain.TOOLS if t["name"] == "buscar_en_navegador")
        props = tool["input_schema"]["properties"]
        self.assertEqual(props["motor"]["enum"], ["google", "amazon", "youtube", "wikipedia", "maps", "github"])
        self.assertEqual(tool["input_schema"]["required"], ["motor", "consulta"])
        self.assertNotIn("url", props)

    def test_ejecutar_contrato_ok_y_error(self):
        with patch("skills.webs.webbrowser.open") as abrir, patch("builtins.print"):
            res = brain._ejecutar("buscar_en_navegador", {"motor": "amazon", "consulta": "colchones"},
                                  {"webs": webs}, "busca colchones en amazon")
            self.assertEqual(res, {"ok": True, "message": "Buscando colchones en Amazon.", "error": None})
            abrir.assert_called_once_with("https://www.amazon.es/s?k=colchones")
            res = brain._ejecutar("buscar_en_navegador", {"motor": "bing", "consulta": "x"},
                                  {"webs": webs}, "busca x en bing")
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "busqueda_rechazada")
            abrir.assert_called_once()  # el motor inválido no abrió nada más

    def test_no_se_envia_a_ningun_servicio_ni_se_imprime(self):
        with patch("skills.webs.webbrowser.open"), patch("skills.webs.requests.get") as get, \
             patch("skills.webs.urllib.request.urlopen") as urlopen, patch("builtins.print") as imprimir:
            brain._ejecutar("buscar_en_navegador", {"motor": "google", "consulta": "consulta privada xyz"},
                            {"webs": webs}, "busca en google consulta privada xyz")
        get.assert_not_called()
        urlopen.assert_not_called()
        self.assertNotIn("consulta privada xyz", " ".join(str(c) for c in imprimir.call_args_list))

    def test_system_distingue_navegador_y_web(self):
        self.assertIn("Usa buscar_en_navegador cuando el usuario pida buscar en Google", brain.SYSTEM)
        self.assertIn("buscar_en_web para preguntas de datos", brain.SYSTEM)


class TestRouter(unittest.TestCase):
    def test_busquedas_en_un_sitio_van_a_claude(self):
        for texto in ("busca en google recetas de lentejas", "busca colchones en amazon",
                      "búscame zapatillas en amazon", "abre una búsqueda de vuelos a Roma",
                      "ponme lofi en youtube", "busca en wikipedia la revolución francesa",
                      "busca farmacias en maps", "busca en github spring boot starter",
                      "busca en google qué tiempo hace en Sevilla"):
            self.assertEqual(brain._clasificar_peticion(texto), "claude", texto)

    def test_frases_de_buscar_en_web_y_atajos_no_cambian(self):
        for texto, ruta in (("busca información sobre el imperio romano", "claude"),
                            ("busca noticias de hoy", "claude"),
                            ("qué tiempo hace", "atajo"), ("qué hora es", "atajo"),
                            ("quién es Cervantes", "claude"),
                            ("qué es una variable", "local")):
            self.assertEqual(brain._clasificar_peticion(texto), ruta, texto)


if __name__ == "__main__":
    unittest.main()
