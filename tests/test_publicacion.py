"""Archivos de publicación: SECURITY.md, CHANGELOG.md y plantillas de issue."""
import os
import re
import subprocess
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _leer(*partes):
    with open(os.path.join(RAIZ, *partes), encoding="utf-8") as f:
        return f.read()


class TestPublicacion(unittest.TestCase):
    def test_security_cubre_lo_necesario(self):
        texto = _leer("SECURITY.md")
        for seccion in ("## Modelo de amenazas", "## Qué defiende el gate", "## Qué no defiende",
                        "## Qué datos se guardan y dónde", "## Cómo reportar un fallo"):
            self.assertIn(seccion, texto)
        self.assertIn("La voz no está autenticada", texto)
        self.assertNotRegex(texto, r"[\w.+-]+@[\w-]+\.\w+")   # ninguna dirección de contacto

    def test_changelog_desde_el_commit_inicial(self):
        texto = _leer("CHANGELOG.md")
        try:
            primero = subprocess.run(["git", "log", "--reverse", "--format=%ad", "--date=short"], cwd=RAIZ,
                                     capture_output=True, text=True, timeout=30, check=True).stdout.split()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            self.skipTest("sin git")
        self.assertIn(f"### {primero}", texto)
        self.assertNotRegex(texto, r"(?m)^## \[?\d+\.\d+")   # sin versiones: el proyecto no las publica

    def test_plantillas_de_issue_avisan_de_no_pegar_datos(self):
        carpeta = os.path.join(RAIZ, ".github", "ISSUE_TEMPLATE")
        self.assertIn("blank_issues_enabled: false", _leer(".github", "ISSUE_TEMPLATE", "config.yml"))
        for nombre in ("error.yml", "mejora.yml"):
            with self.subTest(nombre):
                texto = _leer(".github", "ISSUE_TEMPLATE", nombre)
                self.assertRegex(texto, r"No pegues")
                self.assertTrue(re.search(r"(?m)^body:", texto))
        self.assertIn("SECURITY.md", _leer(".github", "ISSUE_TEMPLATE", "config.yml"))
        self.assertTrue(os.path.isdir(carpeta))


if __name__ == "__main__":
    unittest.main()
