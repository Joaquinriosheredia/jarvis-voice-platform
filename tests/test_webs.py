"""Búsqueda web: DuckDuckGo primero, Tavily como fallback, "Sin resultados"
si fallan ambos. Sin red: urlopen y TavilyClient siempre sustituidos."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from skills import webs  # noqa: E402

QUERY = "tiempo en Sevilla"

RESULTADOS_TAVILY = {"results": [
    {"title": f"Título {i}", "content": f"Snippet {i}", "url": f"https://ej{i}.com",
     "score": 0.9, "raw_content": "NO DEBE SALIR"}
    for i in range(1, 6)
]}


def _respuesta_ddg(datos):
    """Context manager que imita la respuesta de urlopen."""
    r = MagicMock()
    r.read.return_value = json.dumps(datos).encode()
    r.__enter__.return_value = r
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tavily = MagicMock()
        self.tavily.return_value.search.return_value = RESULTADOS_TAVILY
        for p in (patch.object(webs, "TavilyClient", self.tavily),
                  patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test"})):
            p.start()
            self.addCleanup(p.stop)

    def _buscar(self, urlopen):
        salida = io.StringIO()
        with patch("urllib.request.urlopen", urlopen), contextlib.redirect_stdout(salida):
            res = webs.buscar_en_web(QUERY)
        return res, salida.getvalue()


class TestFallback(Base):
    def test_ddg_falla_usa_tavily(self):
        res, log = self._buscar(MagicMock(side_effect=OSError("sin red")))
        self.assertIn("Título 1: Snippet 1 (https://ej1.com)", res)
        self.assertIn("[WEB] tavily", log)
        self.tavily.return_value.search.assert_called_once()

    def test_ddg_vacio_usa_tavily(self):
        res, log = self._buscar(MagicMock(return_value=_respuesta_ddg(
            {"AbstractText": "", "RelatedTopics": []})))
        self.assertIn("Título 1", res)
        self.assertIn("[WEB] tavily", log)

    def test_ddg_funciona_tavily_no_se_llama(self):
        res, log = self._buscar(MagicMock(return_value=_respuesta_ddg(
            {"AbstractText": "Sevilla es una ciudad de España."})))
        self.assertEqual(res, "Sevilla es una ciudad de España.")
        self.assertIn("[WEB] ddg", log)
        self.tavily.assert_not_called()

    def test_ddg_related_topics_tavily_no_se_llama(self):
        res, log = self._buscar(MagicMock(return_value=_respuesta_ddg(
            {"AbstractText": "", "RelatedTopics": [{"Text": "A"}, {"Text": "B"}]})))
        self.assertEqual(res, "A | B")
        self.tavily.assert_not_called()

    def test_ambos_fallan_sin_resultados(self):
        self.tavily.return_value.search.side_effect = RuntimeError("401")
        res, log = self._buscar(MagicMock(side_effect=OSError("sin red")))
        self.assertTrue(res.startswith("Sin resultados"))
        self.assertIn("[WEB] sin resultados", log)

    def test_tavily_sin_resultados_sin_resultados(self):
        self.tavily.return_value.search.return_value = {"results": []}
        res, _ = self._buscar(MagicMock(side_effect=OSError("sin red")))
        self.assertTrue(res.startswith("Sin resultados"))


class TestTavily(Base):
    def test_formato_max_3_titulo_snippet_url(self):
        res = webs._buscar_tavily(QUERY)
        lineas = res.split("\n")
        self.assertEqual(lineas, [
            "Título 1: Snippet 1 (https://ej1.com)",
            "Título 2: Snippet 2 (https://ej2.com)",
            "Título 3: Snippet 3 (https://ej3.com)",
        ])
        self.assertNotIn("NO DEBE SALIR", res)
        self.assertNotIn("0.9", res)

    def test_pide_3_resultados_con_timeout(self):
        webs._buscar_tavily(QUERY)
        self.tavily.assert_called_once_with(api_key="tvly-test")
        _, kwargs = self.tavily.return_value.search.call_args
        self.assertEqual(kwargs["max_results"], 3)
        self.assertIn("timeout", kwargs)

    def test_snippet_truncado(self):
        self.tavily.return_value.search.return_value = {"results": [
            {"title": "T", "content": "x" * 1000, "url": "https://u"}]}
        self.assertLessEqual(len(webs._buscar_tavily(QUERY)), 300 + len("T:  (https://u)"))

    def test_sin_api_key_none_sin_llamar(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(webs._buscar_tavily(QUERY))
        self.tavily.assert_not_called()

    def test_sin_paquete_none(self):
        with patch.object(webs, "TavilyClient", None):
            self.assertIsNone(webs._buscar_tavily(QUERY))

    def test_excepcion_none(self):
        self.tavily.return_value.search.side_effect = TimeoutError()
        self.assertIsNone(webs._buscar_tavily(QUERY))


class TestBrainDelega(unittest.TestCase):
    def test_tool_buscar_en_web_usa_skill_webs(self):
        skill = MagicMock()
        skill.buscar_en_web.return_value = "resultado web"
        with contextlib.redirect_stdout(io.StringIO()):
            res = brain._ejecutar("buscar_en_web", {"query": QUERY}, {"webs": skill}, "busca el tiempo")
        skill.buscar_en_web.assert_called_once_with(QUERY)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "resultado web")


def _respuesta_wiki(status=200, datos=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = datos or {}
    return r


class TestAcademico(unittest.TestCase):
    """Flujo académico: Wikipedia antes que DuckDuckGo/Tavily."""
    def setUp(self):
        self.wiki = MagicMock()
        self.ddg  = MagicMock(return_value="resultado ddg")
        self.tav  = MagicMock(return_value=None)
        for p in (patch.object(webs, "_buscar_wikipedia", self.wiki),
                  patch.object(webs, "_buscar_ddg", self.ddg),
                  patch.object(webs, "_buscar_tavily", self.tav)):
            p.start()
            self.addCleanup(p.stop)

    def _buscar(self, query):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            res = webs.buscar_en_web(query)
        return res, salida.getvalue()

    def test_que_es_python_intenta_wikipedia_primero(self):
        self.wiki.return_value = "Python es un lenguaje de programación."
        res, log = self._buscar("qué es Python")
        self.wiki.assert_called_once_with("Python")
        self.assertEqual(res, "Python es un lenguaje de programación.")
        self.assertIn("[WEB] wikipedia OK", log)
        self.ddg.assert_not_called()
        self.tav.assert_not_called()

    def test_wikipedia_falla_continua_con_ddg(self):
        self.wiki.return_value = None
        res, log = self._buscar("qué es Python")
        self.wiki.assert_called_once()
        self.ddg.assert_called_once_with("qué es Python")
        self.assertEqual(res, "resultado ddg")
        self.assertIn("[WEB] ddg", log)
        self.assertNotIn("wikipedia", log)

    def test_wikipedia_y_ddg_fallan_continua_con_tavily(self):
        self.wiki.return_value = None
        self.ddg.return_value  = None
        self.tav.return_value  = "resultado tavily"
        res, _ = self._buscar("quién es Cervantes")
        self.assertEqual(res, "resultado tavily")

    def test_noticias_de_hoy_no_es_academico_va_a_ddg(self):
        res, log = self._buscar("noticias de hoy")
        self.wiki.assert_not_called()
        self.ddg.assert_called_once_with("noticias de hoy")
        self.assertEqual(res, "resultado ddg")

    def test_es_pregunta_academica(self):
        for q in ("qué es Python", "Quién es Messi", "qué fue la Guerra Fría",
                  "cuándo nació Cervantes", "cuándo murió Picasso", "define entropía",
                  "definición de algoritmo", "significado de efímero", "historia de Roma",
                  "biografía de Einstein", "inventor del teléfono", "descubridor de América"):
            with self.subTest(q=q):
                self.assertTrue(webs._es_pregunta_academica(q))
        for q in ("noticias de hoy", "tiempo en Sevilla", "precio del bitcoin"):
            with self.subTest(q=q):
                self.assertFalse(webs._es_pregunta_academica(q))

    def test_tema_academico(self):
        casos = {
            "qué es Python":                          "Python",
            "¿Quién es Albert Einstein?":             "Albert Einstein",
            "cuándo nació Cervantes":                 "Cervantes",
            "historia de la Revolución francesa":     "Revolución francesa",
            "biografía de Einstein":                  "Einstein",
            "inventor del teléfono":                  "teléfono",
            "qué es el ADN":                          "ADN",
        }
        for q, tema in casos.items():
            with self.subTest(q=q):
                self.assertEqual(webs._tema_academico(q), tema)


class TestWikipedia(unittest.TestCase):
    """_buscar_wikipedia con requests.get sustituido (sin red)."""
    def _wiki(self, respuesta=None, side_effect=None, query="Python"):
        get = MagicMock(return_value=respuesta, side_effect=side_effect)
        with patch.object(webs.requests, "get", get):
            return webs._buscar_wikipedia(query), get

    def test_devuelve_extract_max_400(self):
        res, get = self._wiki(_respuesta_wiki(datos={"type": "standard", "extract": "x" * 1000}))
        self.assertEqual(res, "x" * 400)
        url = get.call_args[0][0]
        self.assertEqual(url, "https://es.wikipedia.org/api/rest_v1/page/summary/Python")

    def test_titulo_con_espacios_y_tildes(self):
        _, get = self._wiki(_respuesta_wiki(datos={"extract": "ok"}), query="Revolución francesa")
        self.assertTrue(get.call_args[0][0].endswith("/Revoluci%C3%B3n_francesa"))

    def test_404_none(self):
        self.assertIsNone(self._wiki(_respuesta_wiki(status=404))[0])

    def test_desambiguacion_none(self):
        res, _ = self._wiki(_respuesta_wiki(datos={"type": "disambiguation",
                                                   "extract": "Mercurio puede referirse a:"}))
        self.assertIsNone(res)

    def test_extract_vacio_none(self):
        self.assertIsNone(self._wiki(_respuesta_wiki(datos={"type": "standard", "extract": ""}))[0])

    def test_excepcion_none(self):
        self.assertIsNone(self._wiki(side_effect=OSError("sin red"))[0])

    def test_query_vacia_none_sin_peticion(self):
        res, get = self._wiki(query="")
        self.assertIsNone(res)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
