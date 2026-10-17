"""instalar.ps1 / instalar.cmd / env.example: lo que se puede comprobar sin instalar nada.

La instalación real (venv, pip, descargas) se prueba a mano en un directorio limpio (ver README).
Aquí: el script parsea sin errores en PowerShell, no toca secretos, la plantilla no lleva valores
y requirements.txt se puede instalar sin compilador (sin paquetes que solo existen como sdist)."""
import os
import re
import shutil
import subprocess
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _leer(nombre):
    with open(os.path.join(RAIZ, nombre), encoding="utf-8-sig") as f:
        return f.read()


class TestInstalar(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell"), "sin PowerShell")
    def test_powershell_lo_parsea_sin_errores(self):
        ruta = os.path.join(RAIZ, "instalar.ps1")
        orden = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{ruta}', [ref]$null, [ref]$e) | Out-Null; $e.Count; $e | ForEach-Object {{ $_.Message }}")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", orden], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.stdout.strip().splitlines()[0], "0", r.stdout)

    def test_nunca_escribe_env_ni_tokens(self):
        script = _leer("instalar.ps1")
        # Lo único que crea en la carpeta de secretos es la plantilla
        escrituras = re.findall(r"(?:Copy-Item|Set-Content|Out-File|New-Item|WriteAllText)[^\n]*", script)
        for linea in escrituras:
            self.assertNotRegex(linea, r"\.env(?!\.example)|token|credentials", linea)
        self.assertIn('Join-Path $Secretos ".env.example"', script)
        self.assertNotIn("Get-Content", script)   # tampoco lee secretos

    def test_rutas_json_solo_si_no_existe_y_con_las_carpetas_reales(self):
        script = _leer("instalar.ps1")
        self.assertIn('if (Test-Path $RutasJson) { Bien "rutas.json ya existe (no se toca)" }', script)
        self.assertIn('NameSpace("shell:Downloads")', script)   # la carpeta Descargas real, aunque esté movida
        self.assertIn('GetFolderPath("Desktop")', script)

    def test_plantilla_sin_valores_secretos(self):
        for linea in _leer("env.example").splitlines():
            if linea.strip() and not linea.startswith("#"):
                clave, _, valor = linea.partition("=")
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD", clave):
                    self.assertEqual(valor.strip(), "", clave)

    def test_plantilla_cubre_lo_obligatorio(self):
        self.assertRegex(_leer("env.example"), r"(?m)^ANTHROPIC_API_KEY=$")

    def test_requirements_sin_paquetes_que_necesitan_compilador(self):
        # webrtcvad solo existe como sdist (sin wheel para Windows): no se usa y se quitó
        self.assertNotRegex(_leer("requirements.txt"), r"(?mi)^webrtcvad")

    def test_torch_con_cuda_usa_el_indice_de_pytorch(self):
        if "+cu" in _leer("requirements.txt"):
            self.assertIn("download.pytorch.org/whl/cu", _leer("instalar.ps1"))

    def test_lanzador_cmd(self):
        cmd = _leer("instalar.cmd")
        self.assertIn("-ExecutionPolicy Bypass", cmd)
        self.assertIn('"%~dp0instalar.ps1" %*', cmd)


if __name__ == "__main__":
    unittest.main()
