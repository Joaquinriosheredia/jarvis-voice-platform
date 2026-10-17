"""Vuelve a autorizar a JARVIS en Google (Gmail, Calendar o Contactos), sin pasos manuales.

    venv\\Scripts\\python.exe reautorizar.py gmail
    venv\\Scripts\\python.exe reautorizar.py calendar
    venv\\Scripts\\python.exe reautorizar.py contacts

Contactos pide un permiso nuevo (contacts.readonly, "Ver tus contactos") con su propio
token (token_contacts.json). Antes de la primera vez hay que habilitar la People API en el
proyecto de Google Cloud del credentials.json; el script lo recuerda (AVISOS).

1. Renombra el token actual con la fecha (token-caducado-AAAAMMDD-HHMMSS.json, en la
   misma carpeta C:\\Jarvis-secrets). No lo borra.
2. Abre el navegador con el flujo OAuth de Google (credentials.json) y pide el
   consentimiento de nuevo, para que Google entregue un refresh_token nuevo.
3. Guarda el token nuevo y confirma que funciona renovándolo con ese refresh_token
   (la misma comprobación que el arranque y doctor.py --live).
Si el flujo falla o se cancela, el token anterior vuelve a su nombre. Nunca imprime
tokens ni direcciones. Código de salida: 0 si queda autorizado, 1 si no.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skills import google_auth  # noqa: E402

ESPERA = 300  # s máximos esperando a que el usuario autorice en el navegador

# Lo que hay que saber antes de abrir el navegador, por servicio
AVISOS = {
    "contacts": ("Aviso: Google te pedirá un permiso nuevo, «Ver tus contactos» (solo lectura). "
                 "Si falla con accessNotConfigured o 403, habilita la People API en el proyecto de "
                 "Google Cloud de tu credentials.json y repite. Si la app OAuth está en modo Testing, "
                 "este token caduca a los 7 días como los demás."),
}


def _imprimir(texto):
    texto = google_auth.limpio(texto)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(texto.encode(enc, "replace").decode(enc))


def nombre_caducado(token_file, ahora):
    carpeta, archivo = os.path.split(token_file)
    base, ext = os.path.splitext(archivo)
    return os.path.join(carpeta, f"{base}-caducado-{ahora:%Y%m%d-%H%M%S}{ext}")


def flujo_oauth(credentials_file, scopes):
    """Flujo de escritorio: abre el navegador y espera la redirección en un puerto libre."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes)
    return flow.run_local_server(port=0, open_browser=True, prompt="consent", timeout_seconds=ESPERA,
                                 authorization_prompt_message="     Si no se abre el navegador, visita: {url}",
                                 success_message="JARVIS ya está autorizado. Puedes cerrar esta pestaña.")


def _escribir(token_file, contenido):
    temporal = token_file + ".nuevo"
    with open(temporal, "w", encoding="utf-8") as f:
        f.write(contenido)
    os.replace(temporal, token_file)


def reautorizar(servicio, token_file, scopes, credentials_file=google_auth.CREDENTIALS_FILE,
                flujo=flujo_oauth, comprobar=google_auth.comprobar, ahora=None):
    """Proceso completo para un servicio. Devuelve el resultado de google_auth.comprobar
    tras autorizar (o el estado que impidió hacerlo)."""
    nombre = os.path.basename(token_file)
    if not os.path.isfile(credentials_file):
        r = google_auth._resultado(servicio, google_auth.SIN_CREDENCIALES)
        _imprimir(f"ERROR  {google_auth.mensaje(r)}\n       → {google_auth.accion(r)}")
        return r

    anterior = None
    if os.path.isfile(token_file):
        anterior = nombre_caducado(token_file, ahora or datetime.now())
        os.replace(token_file, anterior)
        _imprimir(f"1/3  {nombre} renombrado a {os.path.basename(anterior)}")
    else:
        _imprimir(f"1/3  No había {nombre}: se crea uno nuevo")

    if servicio in AVISOS:
        _imprimir(f"     {AVISOS[servicio]}")
    _imprimir("2/3  Autoriza a JARVIS en el navegador con tu cuenta de Google...")
    try:
        creds = flujo(credentials_file, scopes)
        if not getattr(creds, "refresh_token", None):
            raise ValueError("Google no ha entregado refresh_token")
        _escribir(token_file, creds.to_json())
    except BaseException as e:  # también Ctrl+C: el token anterior vuelve a su sitio
        if anterior and not os.path.isfile(token_file):
            os.replace(anterior, token_file)
            _imprimir(f"     Restaurado el {nombre} anterior")
        _imprimir(f"ERROR  La autorización no se ha completado ({type(e).__name__})")
        if isinstance(e, KeyboardInterrupt):
            raise
        return google_auth._resultado(servicio, google_auth.ERROR)

    r = comprobar(servicio, token_file, scopes, credentials_file)
    estado = "OK" if r["ok"] else "ERROR"
    _imprimir(f"3/3  {estado}  {google_auth.mensaje(r)}")
    if not r["ok"]:
        _imprimir(f"       → {google_auth.accion(r) or 'Vuelve a ejecutar este comando'}")
    return r


def main(argv=None):
    parser = argparse.ArgumentParser(description="Vuelve a autorizar a JARVIS en Gmail, Calendar o Contactos")
    parser.add_argument("servicio", choices=["gmail", "calendar", "contacts"])
    args = parser.parse_args(argv)
    _, token_file, scopes = google_auth.servicios()[args.servicio]
    return 0 if reautorizar(args.servicio, token_file, scopes)["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
