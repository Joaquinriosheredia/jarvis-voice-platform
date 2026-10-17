"""Avisos proactivos v1: JARVIS dice cuándo el último CI de un repo pasa a rojo.

Desactivado por defecto. Solo lee (GET de GitHub con el GITHUB_TOKEN de solo lectura del
briefing) y nunca ejecuta ninguna acción. Configuración en C:\\Jarvis-secrets\\.env:

    JARVIS_AVISOS_CI=1            activa los avisos (cualquier otro valor: desactivados)
    JARVIS_AVISOS_CI_MIN=5        minutos entre comprobaciones (de 2 a 60)
    JARVIS_AVISOS_CI_REPOS=a,b    repos a vigilar; si no, los del briefing (JARVIS_GITHUB_REPOS)

Cada evento (una ejecución de CI en rojo) se avisa una sola vez, por voz y en la UI. Lo que ya
estaba en rojo al arrancar no se avisa (lo dice el briefing). La voz solo sale entre turnos
(jarvis.py llama a entregar() antes de escuchar: ni tú ni JARVIS estáis hablando) y nunca
durante SILENCIO_MIN minutos tras una orden de silencio: entonces el aviso va solo a la UI.
Sin token o con el token rechazado no avisa nada y lo registra una sola vez.
"""
import collections
import os
import threading
import time

from skills import github

INTERVALO_MIN = 5
INTERVALO_MIN_MINIMO, INTERVALO_MIN_MAXIMO = 2, 60
SILENCIO_MIN = 15
# Sin token no tiene sentido seguir; con el token rechazado o sin red se sigue en silencio
CAUSAS_GLOBALES = (github.SIN_TOKEN, github.TOKEN_INVALIDO, github.SIN_RED)


def activado():
    return os.environ.get("JARVIS_AVISOS_CI", "").strip() == "1"


def minutos(valor=None):
    """Minutos entre comprobaciones: JARVIS_AVISOS_CI_MIN (o `valor`) acotado a [2, 60]."""
    valor = os.environ.get("JARVIS_AVISOS_CI_MIN", "") if valor is None else valor
    try:
        n = int(str(valor).strip())
    except ValueError:
        n = INTERVALO_MIN
    return max(INTERVALO_MIN_MINIMO, min(INTERVALO_MIN_MAXIMO, n))


def intervalo_s():
    return minutos() * 60


def repos():
    """JARVIS_AVISOS_CI_REPOS o, si no, los del briefing; misma validación de nombres."""
    return github.repos(os.environ.get("JARVIS_AVISOS_CI_REPOS", "").strip() or None)


def texto_aviso(repo, ci):
    corto = repo.split("/", 1)[-1]
    rama = f" en la rama {ci['rama']}" if ci.get("rama") else ""
    return f"Aviso: el CI de {corto} ha pasado a rojo ({ci.get('nombre') or 'CI'}{rama})."


class Vigilante:
    def __init__(self, lista=None, consultar=None, reloj=time.monotonic, registrar=print):
        self.lista = list(lista) if lista is not None else repos()
        self.consultar = consultar or github.ultimo_ci
        self.reloj = reloj
        self.registrar = registrar
        self.ultimo = {}                  # repo -> último ci visto
        self.avisados = set()             # (repo, id de la ejecución) ya avisados
        self.pendientes = collections.deque()
        self.registrados = set()          # causas de fallo ya registradas (una vez cada una)
        self.silencio_hasta = 0.0
        self.parar = False                # sin token: el hilo termina
        self._lock = threading.Lock()

    def _registrar_una_vez(self, clave, texto):
        if clave not in self.registrados:
            self.registrados.add(clave)
            self.registrar(f"[AVISOS] {texto}")

    def comprobar(self):
        """Una pasada por los repos. Nunca lanza ni deja texto del error (solo la causa)."""
        for repo in self.lista:
            try:
                ci = self.consultar(repo)
            except github.ErrorGitHub as e:
                if e.causa in CAUSAS_GLOBALES:
                    self._registrar_una_vez(e.causa, f"avisos de CI sin datos de GitHub ({e.causa}): no se avisa nada")
                    self.parar = e.causa == github.SIN_TOKEN
                    return
                self._registrar_una_vez((repo, e.causa), f"avisos de CI: sin acceso a {repo} ({e.causa})")
                continue
            except Exception as e:
                self._registrar_una_vez(type(e).__name__, f"avisos de CI: fallo inesperado ({type(e).__name__})")
                return
            if not ci:
                continue
            previo = self.ultimo.get(repo)
            self.ultimo[repo] = ci
            clave = (repo, ci.get("id") or (ci.get("nombre"), ci.get("rama")))
            if previo is None:          # primera pasada: lo que ya había no es un evento nuevo
                if ci.get("estado") == "rojo":
                    self.avisados.add(clave)
                continue
            if ci.get("estado") == "rojo" and clave not in self.avisados:
                self.avisados.add(clave)
                with self._lock:
                    self.pendientes.append(texto_aviso(repo, ci))

    def silenciar(self):
        """Orden de silencio: durante SILENCIO_MIN minutos los avisos van solo a la UI."""
        self.silencio_hasta = self.reloj() + SILENCIO_MIN * 60

    def silenciado(self):
        return self.reloj() < self.silencio_hasta

    def entregar(self, hablar, mostrar):
        """Saca los avisos pendientes: a la UI siempre y por voz si no hay silencio. Cada aviso
        sale una sola vez. Devuelve cuántos ha entregado."""
        entregados = 0
        while True:
            with self._lock:
                if not self.pendientes:
                    return entregados
                texto = self.pendientes.popleft()
            mostrar(texto)
            if not self.silenciado():
                hablar(texto)
            entregados += 1


_vigilante = None


def iniciar(registrar=print):
    """Arranca el hilo si JARVIS_AVISOS_CI=1. Devuelve el Vigilante o None."""
    global _vigilante
    if not activado():
        return None
    _vigilante = Vigilante(registrar=registrar)
    espera = intervalo_s()

    def bucle():
        while not _vigilante.parar:
            _vigilante.comprobar()
            if _vigilante.parar:
                break
            time.sleep(espera)
    threading.Thread(target=bucle, daemon=True, name="avisos-ci").start()
    registrar(f"[AVISOS] avisos de CI activos: {len(_vigilante.lista)} repo(s) cada {espera // 60} min")
    return _vigilante


def hay_pendientes():
    return bool(_vigilante and _vigilante.pendientes)


def silenciar():
    if _vigilante:
        _vigilante.silenciar()


def entregar(hablar, mostrar):
    return _vigilante.entregar(hablar, mostrar) if _vigilante else 0
