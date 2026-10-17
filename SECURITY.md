# Seguridad

JARVIS es un asistente de voz personal que se ejecuta en un PC con Windows y puede leer y enviar correo, tocar archivos, ejecutar tests y publicar en GitHub. Este documento cubre:

- el modelo de amenazas;
- qué defiende el gate de permisos y qué no;
- qué datos se guardan y dónde;
- cómo reportar un fallo.

El detalle de cada mitigación está en el [README](README.md#seguridad-y-privacidad), y las pruebas adversariales en [Seguridad: evals adversariales](README.md#seguridad-evals-adversariales).

## Modelo de amenazas

**Qué se protege:**
- el correo, la agenda y los contactos de Google;
- los archivos del usuario;
- los repos y la cuenta de GitHub;
- las claves de API;
- lo que se habla con JARVIS (turnos, memoria, capturas de pantalla).

**En qué se confía:**
- **El micrófono.** Lo que llega por él es la única fuente de confirmaciones.
- **El código del repo y la máquina local.** Incluye la carpeta de secretos.

**En qué no se confía:**
- **El modelo.** Se asume que puede caer en una inyección y pedir cualquier tool con cualquier argumento.
- **Todo contenido externo:** correos, resultados de búsqueda, páginas web, archivos y salidas de comandos, la agenda, los títulos de PRs y las capturas de pantalla.

**Atacante principal:** quien consigue meter texto en uno de esos canales, por ejemplo con un correo envenenado o una web con instrucciones ocultas. Su objetivo es que JARVIS envíe datos fuera, borre algo o ejecute código.

## Qué defiende el gate

Toda llamada a una tool pasa por `brain._gate`, sea del modelo, de un atajo o del sistema:

- **Confirmación por voz para lo irreversible.** Borrar archivos o correos, enviar correo, apagar el equipo, capturar la pantalla, olvidar toda la memoria y, con el gate estricto por defecto, 7 tools más (ver [Gate estricto](README.md#gate-estricto)). Para cada una, la frase:
  - solo cuenta si llega por el micrófono, nunca en un resultado de tool ni en los argumentos;
  - vale una vez y caduca a los 120 s;
  - queda ligada a esa acción con esos argumentos.

  El anuncio lo genera el sistema, no el modelo.
- **Destinatarios.** Solo puede enviarse correo:
  - a tu propia dirección;
  - a una dictada por el micrófono, que JARVIS deletrea antes;
  - a un contacto de Google cuyo nombre dijiste en ese turno.

  La confirmación queda ligada a un hash de destinatario, asunto, cuerpo y adjunto: si algo cambia tras el anuncio, no se envía.
- **Archivos.** Allowlists de rutas y árboles bloqueados. Los patrones de secretos (`.env*`, claves, tokens, credenciales, `.git`) se bloquean en cada componente de la ruta.
- **Secretos en lo que lee el modelo.** El código, los diffs, la salida de tests, el correo, la web, la agenda y GitHub pasan por un enmascarado antes de llegar al modelo, la UI o el log.
- **Comandos.** `subprocess` sin shell, con argumentos en lista y validados.
- **Interfaz local.** El WebSocket escucha solo en `127.0.0.1`, solo acepta el origen de Electron y solo atiende «parar» y «cerrar». Ningún mensaje de la UI confirma nada.

`tests/test_evals_inyeccion.py` lo comprueba con un modelo simulado que obedece todo: 72/72 casos en los tres modos del gate. Además, varios meta-tests rompen el gate a propósito para comprobar que la suite lo detecta.

## Qué no defiende

Estos límites son conocidos y ya estaban documentados en el README:

- **La voz no está autenticada.** Cualquiera que hable cerca del micrófono puede dar órdenes y confirmarlas. También vale un vídeo o una llamada que suene por los altavoces mientras hay una acción pendiente (120 s). JARVIS no reconoce tu voz.
- **Tools WRITE y EXTERNAL sin confirmación.** Una inyección puede dispararlas, por diseño, para no añadir fricción a órdenes inocuas:
  - con el gate por defecto: `nota`, `recordatorio`, `abrir_app`, `abrir_web`, `crear_carpeta`, el volumen y la música;
  - con `JARVIS_GATE_ESTRICTO=0`: también las 7 del modo por defecto. `buscar_en_navegador` es entonces un canal de salida hacia el buscador.
- **Cuerpos largos.** Se anuncian cortados. Lo que no se lee en voz alta solo lo protege el hash (no puede cambiar), no tu oído.
- **Secretos sin forma reconocible.** El enmascarado no detecta un secreto partido, cifrado o con un formato desconocido.
- **Qué hace el modelo con lo que lee.** Puede resumir mal o dejarse engañar en lo que dice. El gate solo controla las acciones, no la veracidad de las respuestas.
- **Eco y altavoces.** Con altavoces, una orden de parada dicha encima de un eco fuerte puede no reconocerse. `prueba_eco.py` mide el eco en cada montaje.
- **Acceso a la máquina.** Quien tenga acceso a tu sesión de Windows puede leer la carpeta de secretos, la memoria y los logs, o ejecutar código. Nada de esto está cifrado en reposo.
- **Dependencias y servicios externos.** No se auditan las dependencias de Python y Node ni los servicios de Anthropic, Google, ElevenLabs, GitHub y los buscadores.

## Qué datos se guardan y dónde

**En la carpeta de secretos** (`JARVIS_SECRETOS`, por defecto `C:\Jarvis-secrets`, fuera del repo):

| Archivo | Contenido |
|---|---|
| `.env` | Claves de API (Anthropic, GitHub, ElevenLabs, Tavily) y configuración. |
| `credentials.json`, `token*.json` | Cliente OAuth y tokens de Gmail, Calendar y Contactos. |
| `memoria_sesiones.db` | Un resumen enmascarado por sesión, con sus temas y tools: sin turnos privados ni direcciones, 90 días de retención y borrado por voz. |
| `rutas.json` | Rutas de esta máquina y perfil del usuario (nombre, usuario de GitHub…). No es un secreto, pero es personal. |

**En la carpeta del repo** (ignorados por git):

| Archivo | Contenido |
|---|---|
| `memoria.json` y `.bak` | Historial reciente, notas y tareas. Las direcciones salen como `<email>` y los turnos privados, sin texto. |
| `logs/turnos.jsonl` | Métricas por turno y el texto truncado y enmascarado. Sin texto en los turnos privados, ni en ninguno con `JARVIS_LOG_TEXTO=0`. Rota a los 5 MB. |
| `jarvis.log` | La consola, con las direcciones como `<email>`. |
| `logs/backup-*` | Originales que guardan `limpiar_logs.py` y `limpiar_memoria.py` antes de limpiar. |
| `logs/eco.jsonl` | Solo métricas, si ejecutas `prueba_eco.py` con `--json`. |

**Nunca en disco:**
- **Las capturas de pantalla.** Solo viven en memoria.
- **El audio del micrófono.** Whisper usa un WAV temporal que se borra en cuanto transcribe; la escucha de parada trabaja en memoria.
- **El texto de los turnos privados** (correo, agenda, pantalla, direcciones dictadas).
- **El texto provisional del streaming.**

**Fuera de la máquina:**

| Servicio | Qué le llega |
|---|---|
| Anthropic | El texto de cada turno que va a Claude, los resultados de tools (enmascarados) y las capturas confirmadas. |
| Google | Lo que pidas a Gmail, Calendar y Contactos. |
| ElevenLabs (opcional) | El texto que se va a decir, nunca el de un turno privado. |
| GitHub | Lecturas del CI y los PRs, y el push de `siguiente_racha`. |
| Buscadores y YouTube | Las consultas. |
| Ollama | Nada: es local. |

## Cómo reportar un fallo

No abras un issue público con los detalles de una vulnerabilidad.

1. **Si el repositorio tiene activado el reporte privado de GitHub:** usa *Security → Report a vulnerability*.
2. **Si no lo tiene:** abre un issue que diga solo «Tengo un fallo de seguridad que reportar». Sin detalles, sin pasos para reproducirlo y sin datos. Así se acuerda un canal privado.

Incluye en el reporte privado:
- la versión (hash del commit);
- el modo del gate (`JARVIS_GATE_ESTRICTO`);
- los pasos mínimos para reproducirlo;
- qué esperabas y qué ocurrió.

No incluyas claves, tokens, el contenido de tu `.env`, logs con texto de turnos, direcciones de correo ni capturas con datos personales. Si hace falta un ejemplo, usa valores inventados.

Es un proyecto personal sin plazos garantizados: se responderá en cuanto sea posible y el arreglo irá con un test que reproduzca el caso.
