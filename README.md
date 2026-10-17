# JARVIS

[![CI](https://github.com/Joaquinriosheredia/jarvis-voice-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Joaquinriosheredia/jarvis-voice-platform/actions/workflows/ci.yml)

Asistente de voz personal en español para Windows: escucha por micrófono, decide qué capa debe responder y actúa con 46 tools (correo, calendario, archivos, música, git, tests y visión) detrás de un gate de permisos.

**Stack:** Python · Electron · Claude Haiku 4.5 · Ollama (qwen2.5:3b) · Whisper · Piper (ElevenLabs opcional)

## Demo

![Demo: JARVIS pide confirmación por voz antes de enviar un correo](docs/demo-jarvis.gif)

Pides por voz que te envíe un correo a tu propia dirección. JARVIS anuncia el destinatario real, que resuelve el sistema y no el modelo, y el correo solo sale cuando dices «confirmo envío» por el micrófono. En el GIF la dirección está difuminada a propósito.

## Qué hace

- **Voz de extremo a extremo:** Whisper `small` en local, con umbral de confianza; Piper para hablar, opcionalmente ElevenLabs; órdenes de voz para silenciar o cerrar JARVIS, también mientras habla o genera la respuesta (ver [Órdenes de voz](#órdenes-de-voz)).
- **Agente con 50 tools** sobre la API de Claude, con un gate de permisos de 4 niveles y confirmación por voz para las acciones irreversibles y para enviar capturas de pantalla.
- **Respuesta en streaming:** el texto aparece en la UI mientras Claude lo genera, y la voz se reproduce por frases: la siguiente se sintetiza mientras suena la actual.
- **Correo (Gmail):** leer, buscar, archivar, borrar uno o varios y enviar. Borrar y enviar piden confirmación. Se puede enviar a la propia dirección («mándamelo a mí», «a mi correo», «a mi propia dirección»), que JARVIS obtiene del perfil de la cuenta de Gmail sin preguntarla, o a una dirección dictada por voz, que JARVIS valida y deletrea antes de pedir la confirmación. Si no puede obtener la dirección propia, dice la causa: autorización caducada, red o permisos.
- **Briefing hablado al arrancar y con «dame el briefing»:** agenda de hoy, CI y PRs de GitHub, correo, tareas y tiempo, en 6 frases como mucho (ver [Briefing](#briefing)).
- **Memoria entre sesiones:** un resumen enmascarado por sesión, con «¿te acuerdas de…?», «olvida lo de…» y retención de 90 días (ver [Memoria](#memoria)).
- **Calendario (Google Calendar):** `ver_agenda` y `crear_evento`. "Qué tengo hoy" combina agenda y tareas. Cada turno lleva la fecha y hora reales.
- **Visión por voz:** "mira mi pantalla" o "qué error sale" capturan el monitor principal tras confirmar con "confirmo captura", y Claude describe lo que ve.
- **Operador de ingeniería:** leer archivos, `git status`, `diff` y `log`, buscar en el código y `ejecutar_tests`, siempre sobre una allowlist de proyectos.
- **Búsquedas en el navegador:** "busca colchones en Amazon" o "busca en Google…" abren la búsqueda ya hecha en el navegador por defecto (Google, Amazon, YouTube, Wikipedia, Maps o GitHub). La consulta no pasa por ningún servicio de JARVIS.
- **Música y sistema:** VLC y yt-dlp, volumen, archivos, recordatorios, notas, pomodoro y apagado con confirmación.
- **Observabilidad por turno:** latencia por fase, tokens, coste estimado y log JSONL con replay, más la tool `gasto_hoy` para preguntarlo por voz.
- **UI de escritorio en Electron:** conversación en vivo, estado del micrófono, bloques de código y una línea de métricas bajo cada respuesta.

## Arquitectura

```mermaid
flowchart LR
    MIC[Micrófono] --> WH[Whisper local con umbral de confianza]
    WH --> RT{Router de 3 capas}
    RT -->|atajo| AT[Tool directa sin LLM]
    RT -->|local| OL[qwen2.5 en Ollama]
    RT -->|dev o claude| CL[Claude Haiku 4.5 en streaming]
    CL -->|tool_use| GT{Gate de permisos}
    GT -->|permitido| TL[Tools]
    GT -->|bloqueado| CL
    TL -->|resultado o captura de pantalla| CL
    CL -->|texto parcial| WS[WebSocket local]
    AT --> TTS
    OL --> TTS
    CL -->|respuesta final| TTS[TTS por frases con Piper o ElevenLabs]
    TTS --> SPK[Altavoz]
    TTS --> WS
    WS --> UI[UI Electron]
    MET[Métricas por turno] --> LOG[turnos.jsonl]
    MET --> WS
```

- **Atajo:** frases cerradas como "qué hora es" o "para la música" ejecutan la tool sin llamar a ningún modelo.
- **Local:** preguntas generales sin tools van a qwen2.5:3b en Ollama. Si la respuesta no sirve, se pasa a Claude.
- **Claude:** todo lo que necesita tools, datos actuales, la persona de JARVIS o la pantalla. La ruta `dev` es Claude con un prompt de desarrollador.
- **Métricas:** cada fase (whisper, router, llm, tools, tts) registra su tiempo en `metrics.py`. Al cerrar el turno se escribe una línea en `logs/turnos.jsonl` y la UI recibe el resumen.

```mermaid
flowchart TD
    TU[Claude pide una tool] --> RI{Nivel de riesgo}
    RI -->|READ o WRITE| EJ[Ejecutar]
    RI -->|EXTERNAL| RC{Requiere confirmación}
    RC -->|no| EJ
    RC -->|sí, como ver_pantalla| CF{El usuario acaba de decir la frase de confirmación}
    RI -->|DESTRUCTIVE| CF
    CF -->|no| PE[Armar acción pendiente con TTL de 120 s]
    PE --> BL[Bloquear y pedir la frase exacta por voz]
    BL --> US[El usuario dice la frase]
    US --> WC{Transcripción con confianza alta}
    WC -->|no| BL
    WC -->|sí| CF
    CF -->|sí, misma acción y dentro del TTL| EJ
    CF -->|sí, pero otra acción o caducada| NO[Bloquear y descartar el pendiente]
```

Antes del nivel de riesgo, cada tool pasa por sus validaciones propias: allowlist de rutas, destinatario propio o dictado por el micrófono con formato válido, o que el email venga del último listado. Las frases de confirmación son "confirmo borrado", "confirmo borrado de correo", "confirmo borrado de correos", "confirmo envío", "confirmo apagado del pc" y "confirmo captura". Tras normalizar tildes y mayúsculas también valen "confirma" y "confirmar" en lugar de "confirmo", con un "el" opcional delante de la acción ("confirma el envío", "confirmar captura"), porque Whisper confunde esas formas. Siempre hacen falta el verbo y la acción: "sí", "vale", "ok" o "adelante" no valen, y la frase de una acción no confirma otra.

## Órdenes de voz

Las dos listas están en `parada.py` (`SILENCIO` y `APAGADO`). La respuesta a "¿cómo te callo?" o "¿cómo te cierro?" y la línea del prompt de sistema se generan desde ellas, sin pasar por el modelo. La columna «Frases» de esta tabla sale de `parada.tabla_readme()` y un test comprueba que coincide. Las tildes dan igual: «callate» vale como «cállate».

| Orden | Frases | Efecto |
|---|---|---|
| Silenciar | «cállate», «no hables», «escucha», «silencio», «para», «basta», «calla», «calla ya», «cállate ya», «déjame hablar», «espera», «detente», «detén», «para ya», «ya basta», «chitón», «déjalo», «deja de hablar», «no sigas» | Corta el audio, vacía las frases pendientes y cancela la generación en curso. JARVIS sigue abierto y escuchando. |
| Apagar | «apágate», «apaga JARVIS», «apaga JARVIS del todo», «adiós», «hasta luego», «desconéctate», «cierra JARVIS» | Corta el audio y la generación, se despide y cierra JARVIS. |

- Solo valen si la frase transcrita es entera la orden (puede llevar "Jarvis", "ya", "por favor"...) y tiene como mucho 4 palabras sin contar "Jarvis". "Apágate cuando termines de leer el correo" no apaga nada.
- Funcionan con JARVIS hablando, a la primera; mientras habla, solo se atienden estas órdenes y se descarta el eco de su propia voz.
- Nunca cuentan como confirmación de una acción ni llegan al modelo o al gate. El modelo no tiene ninguna tool para cerrar JARVIS.
- Sin voz: botón «Callar» de la UI, Esc o Ctrl+Alt+P silencian.

## Memoria

JARVIS recuerda las sesiones anteriores con un resumen corto por sesión. No hay embeddings ni servicios externos: es una base SQLite local con búsqueda de texto completo (FTS5). Nunca se inyecta en cada turno; solo se consulta cuando hablas del pasado.

**Dónde.** `C:\Jarvis-secrets\memoria_sesiones.db`, fuera del repo y junto a los tokens y el `.env`. Se puede cambiar con `JARVIS_MEMORIA_DB`.

**Qué se guarda.** Un registro por sesión: fecha, resumen de 3 a 5 líneas, temas y tools usadas. Al cerrar JARVIS («adiós», «apágate» o un cierre normal), el resumen lo genera el modelo más barato disponible: primero Ollama local, que no sale del equipo, y si falla Claude Haiku. Si los dos fallan, se guarda solo la lista de temas. El modelo solo ve los turnos no privados, ya enmascarados, y lo que devuelve se vuelve a enmascarar antes de guardarse.

**Qué no se guarda nunca.**
- Direcciones de correo: se escriban o se dicten, salen como `<email>`.
- Secretos: salen como `[SECRETO-REDACTADO:tipo]`.
- El texto de los turnos privados (correo, agenda, pantalla o una dirección dictada). De esos turnos quedan, como mucho, el tema y las tools.
- La salida de `leer_archivo`, `git_diff`, `buscar_en_codigo`, `ejecutar_tests` y `ver_pantalla`. De esos turnos solo queda la pregunta.
- Las órdenes de parada y las de memoria.

**Cómo se consulta.** El modelo tiene la tool READ `recordar(consulta)`, que devuelve como mucho 3 resultados con fecha. La usa cuando dices «la semana pasada», «lo de ayer» o «¿te acuerdas de…?»; esas frases siempre van a Claude y nunca al modelo local. Las métricas registran cuántas consultas hay y si encuentran algo, nunca el texto.

**Cómo se borra.**

| Di | Qué pasa |
|---|---|
| «qué recuerdas de kafka» | Lee los resúmenes que mencionan el tema, sin modelo. |
| «olvida lo de kafka» | Borra al momento las sesiones que mencionan el tema, también lo acumulado en la sesión actual. |
| «olvida todo» | Acción DESTRUCTIVE del gate: JARVIS dice cuántas sesiones hay y solo borra tras «confirmo borrado de memoria» dicho al micrófono (un uso, 120 s). |

Borrar quita las filas, compacta el índice FTS y reescribe el archivo (`secure_delete` y `VACUUM`), así que el texto no queda en páginas libres. La retención es de 90 días (`JARVIS_MEMORIA_DIAS`): lo caducado se borra al arrancar. También puedes borrar el archivo `memoria_sesiones.db` con JARVIS cerrado. `python doctor.py` dice si la base existe, si se puede leer y cuántas sesiones tiene.

## Briefing

Al arrancar, JARVIS da un briefing hablado y corto: como mucho 6 frases contando el saludo y la pregunta final, con lo más urgente primero.

1. Eventos que quedan hoy en Google Calendar: cuántos y cuál es el primero.
2. GitHub, en los repos configurados: si el último CI ha fallado, los PRs abiertos esperando review y, si no hay nada urgente, que el CI está en verde.
3. Correo sin leer.
4. Tareas vencidas o pendientes.
5. Tiempo.

Lo que no cabe se queda fuera. El aviso de reautorizar Google, si hace falta, va aparte, justo antes de la pregunta. El briefing solo lee: `events.list` de Calendar, los no leídos de Gmail y peticiones `GET` a la API de GitHub. Nunca ejecuta una acción ni usa la memoria entre sesiones. Con un token de Google roto no toca ese servicio.

- **Por voz:** «dame el briefing» (o «resumen del día») lo repite en cualquier momento, sin pasar por el modelo. Es un turno privado (agenda y correo): se dice con Piper y el log no guarda su texto.
- **GitHub por voz:** «cómo va el CI de jarvis en GitHub» usa la tool READ `github_estado`.
- **Desactivarlo:** `JARVIS_BRIEFING=0` deja solo el saludo y los avisos al arrancar; «dame el briefing» sigue funcionando.
- **Repos:** `JARVIS_GITHUB_REPOS`, separados por comas (`dueño/repo`, o solo `repo` si es de Joaquinriosheredia). Por defecto, solo `jarvis-voice-platform`.
- **«Review pendiente»:** PR abierto, que no es borrador y sin ninguna review aprobada (como mucho 5 PRs por repo).

**Token de GitHub.** Crea un token *fine-grained* en GitHub → Settings → Developer settings → Fine-grained tokens:
- *Repository access:* «Only select repositories», con los repos del briefing.
- *Repository permissions:* `Metadata: Read-only` (obligatorio), `Pull requests: Read-only` y `Actions: Read-only`. Nada más: ni `Contents`, ni permisos de cuenta u organización.
- Guárdalo como `GITHUB_TOKEN=` en `C:\Jarvis-secrets\.env`. Nunca se imprime ni se registra.

Si no hay token, no es válido o no hay red, el briefing sigue sin la parte de GitHub y lo dice una sola vez por sesión. `doctor.py` comprueba que el token existe y es fine-grained; `doctor.py --live` comprueba que puede leer el repo, los PRs y Actions de cada repo configurado.

## Decisiones de diseño

- **Router por capas:** el coste y la latencia se pagan solo cuando hacen falta. Los atajos no tocan ningún modelo y la capa local es gratuita.
- **Confirmación leída del micrófono:** el gate solo acepta la frase del texto que transcribió Whisper en ese turno. El modelo no puede fabricarla. Va ligada a la firma de la acción, sirve una sola vez y caduca a los 120 s.
- **Confianza de Whisper más estricta con una acción pendiente:** normalmente se descarta lo que tenga `avg_logprob` medio < -1.0 o `compression_ratio` > 2.4. Con una confirmación pendiente se exige ≥ -0.7, y si falta la métrica el texto se descarta. Un texto dudoso nunca confirma una acción.
- **No hablar de más con streaming:** el texto de cada iteración se retiene hasta saber que no acaba en `tool_use`. Si empieza un bloque `tool_use`, lo retenido se descarta, también en pantalla. Solo se dice la respuesta final, y lo decide `jarvis.py`, que también elige entre la respuesta, el anuncio real del gate o el resultado de una confirmación.
- **Prompt caching con fecha real:** el system prompt y las tools llevan `cache_control` (más de 5.000 tokens de prefijo). La fecha y la hora van en un segundo bloque de system sin caché, recalculado en cada turno, así que el prefijo cacheado no cambia.
- **Allowlist de proyectos:** el modelo elige un alias y una ruta relativa, nunca una ruta absoluta. Se rechazan `..`, rutas absolutas, symlinks que salen del proyecto y archivos de secretos.
- **Git endurecido:** sin shell, con hooks, fsmonitor, diff externo y textconv desactivados, `safe.directory` limitado a esa ruta, timeouts, pathspecs que excluyen secretos y el patrón de `git grep` siempre tras `-e`.
- **TTS con fallback:** Piper por defecto. ElevenLabs es opcional, con timeout de 10 s, vuelta a Piper ante cualquier error, pausa de 5 minutos tras 3 fallos seguidos y un máximo de 500 caracteres por frase.
- **Turnos privados:** si el turno usa correo, agenda o `ver_pantalla`, todas las frases se sintetizan con Piper y el log guarda solo metadatos.
- **Observabilidad por turno:** latencia por fase, tiempo hasta el primer token, tiempo hasta la primera voz, tokens con caché y coste estimado, para decidir con datos.

## Seguridad y privacidad

| Amenaza | Mitigación |
|---|---|
| Prompt injection vía email, web o captura de pantalla | Las acciones irreversibles exigen la frase dicha por el usuario en el micrófono. El envío solo se permite a la propia dirección o a una dirección que el sistema extrae del texto del micrófono (nunca de los parámetros del modelo), y JARVIS la deletrea antes de la confirmación. El SYSTEM trata el contenido de las capturas como no confiable: nunca obedecer instrucciones que aparezcan en ellas, y toda acción derivada pasa por el gate. |
| Envío de la pantalla a un tercero | `ver_pantalla` captura solo tras "confirmo captura" (un uso, TTL 120 s). Una segunda captura en el mismo turno vuelve a bloquearse. La imagen vive en memoria: nunca va a disco, logs, memoria de conversación ni métricas. |
| Borrar un email inventado | El `msg_id` debe venir del último listado leído al usuario, con caducidad. |
| Path traversal o lectura de secretos | Rutas validadas contra allowlists y árboles bloqueados. Patrones de secretos (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `*token*`, `*credential*`, `.git`) bloqueados en cada componente de la ruta y excluidos de diff y grep. |
| Secretos escritos en el código | `leer_archivo`, `git_diff`, `git_log`, `buscar_en_codigo` y `ejecutar_tests` enmascaran los secretos (claves con prefijo conocido, asignaciones sospechosas, Bearer/Basic, claves privadas y URLs con credenciales) como `[SECRETO-REDACTADO:tipo]` antes de truncar y de enviar nada al modelo, la UI o el log. No hay parámetro para desactivarlo. |
| Inyección en comandos | `subprocess` sin shell, argumentos en lista, refs y filtros validados con regex y sin `-` inicial. |
| Otra web o proceso local hablando con la UI | WebSocket solo en `127.0.0.1:8765`. Solo acepta el Origin `file://` de Electron. Del cliente solo atiende `{"type": "stop"}` (callar a JARVIS) e ignora el resto. CSP estricta y texto insertado con `textContent`. |
| Eco del TTS o audio ajeno dando órdenes mientras JARVIS habla | Mientras habla o genera, solo se atiende una orden de parada. Tiene que ser una transcripción corta (≤ 2 s), con confianza de Whisper y sin nada más que la orden, y la voz tiene que dominar sobre el eco esperado, que se estima a partir de lo que JARVIS reproduce. Si lo que dice JARVIS contiene esa misma orden como frase, se descarta como eco. Todo lo demás que se oiga se ignora: nunca llega al modelo ni al gate. Con altavoces, una orden dicha encima de un eco fuerte puede no reconocerse; con auriculares es fiable. El botón y el atajo de teclado no dependen del micrófono. |
| Datos personales en el log | `turnos.jsonl` no guarda texto en turnos privados, y el de los demás pasa por el mismo enmascarado (sin direcciones ni secretos). Los turnos que leen código o tests no guardan la respuesta. `JARVIS_LOG_TEXTO=0` deja solo metadatos en todos. El texto provisional del streaming nunca se guarda. Un turno con una dirección de email dictada es privado, y la consola (que `app/start.vbs` guarda en `jarvis.log`) sustituye cada dirección por `<email>`. Para los logs anteriores a estas reglas, `limpiar_logs.py` (de un solo uso) quita el texto de los turnos que hoy son privados y cambia las direcciones por `<email>`: primero muestra el recuento y pide confirmación, y guarda los originales en `logs/backup-<fecha>/`. |
| Datos personales en la memoria de conversación | El historial de `memoria.json`, que vuelve al modelo como contexto, nunca guarda direcciones: se sustituyen por `<email>` con el mismo enmascarado que los logs. Un turno privado se guarda sin texto, con un marcador del tema, las tools usadas y si quedó algo por confirmar. Lo que el modelo necesite de esos turnos lo pone el sistema: destinatario `yo` (la cuenta de Gmail) o `dictada` (la última dirección dictada por el micrófono, durante 5 minutos). Las notas no se tocan. `limpiar_memoria.py` (de un solo uso) aplica lo mismo al `memoria.json` y al `memoria.json.bak` existentes, con recuento, confirmación y copia en `logs/backup-memoria-<fecha>/`. |
| Datos personales en la memoria entre sesiones | Solo resúmenes enmascarados de turnos no privados, fuera del repo, con retención de 90 días y borrado por voz. Ver [Memoria](#memoria). |
| Fuga de credenciales | Secretos fuera del repo, en `C:\Jarvis-secrets`. La clave de ElevenLabs nunca se escribe en logs (hay un test). |

## Calidad

- **Tests:** 653 tests con `unittest`, sin red ni hardware: micrófono, Whisper, Piper, captura de pantalla, Gmail, Calendar y la API de Claude, incluido su streaming, están mockeados.
  `python -m unittest discover -s tests -q` → `Ran 653 tests ... OK`
- **CI en GitHub Actions:** tests con mocks en cada push (`.github/workflows/ci.yml`, Windows, Python 3.12, sin claves ni red), `doctor.py --json` (solo se valida que produce JSON: en el runner no hay audio, Ollama ni credenciales) y escaneo de secretos con gitleaks.
- **Evals:** 60 casos que miden, por petición, la ruta del router, la tool elegida y la decisión del gate.
  `python -m unittest tests.test_evals -v` → Ruta 60/60, Tool 60/60, Gate 60/60, con umbral mínimo del 80 %.
  La ruta y el gate son lógica real. La métrica Tool usa un mock del modelo en la ruta claude, así que solo valida la tubería. Para medir al modelo de verdad hay que lanzarlo con `JARVIS_EVALS_LIVE=1`, que llama a la API y tiene coste.
- **Tools por nivel de riesgo:** 22 READ, 3 WRITE, 17 EXTERNAL (`ver_pantalla` con confirmación) y 5 DESTRUCTIVE. `test_brain_permisos` fija estas cifras.

## Instalación y ejecución (Windows)

Requisitos: Windows 10/11, Python 3 (el venv actual usa 3.14), Node.js, [Ollama](https://ollama.com), ffmpeg en el PATH (lo usa Whisper), VLC en su ruta por defecto y WSL solo para los proyectos del operador que viven en Linux.

```powershell
cd C:\Jarvis
python -m venv venv
venv\Scripts\pip install -r requirements.txt   # incluye pillow (captura) y tzdata (zona horaria)
ollama pull qwen2.5:3b
venv\Scripts\python.exe doctor.py              # primer paso tras instalar: comprueba el entorno
```

Configuración en `C:\Jarvis-secrets\.env`, que carga `jarvis.py`:

```
ANTHROPIC_API_KEY=
TAVILY_API_KEY=
# Opcionales
JARVIS_TTS=piper                # o elevenlabs
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
JARVIS_STREAMING=1              # 0 = sin streaming ni voz por frases
JARVIS_LOG_TEXTO=1              # 0 = el log de turnos guarda solo metadatos
WHISPER_MIN_LOGPROB=-1.0        # confianza mínima media de Whisper
WHISPER_MAX_COMPRESSION=2.4     # compression_ratio máximo por segmento
GITHUB_TOKEN=                   # fine-grained, solo lectura (ver Briefing)
JARVIS_GITHUB_REPOS=jarvis-voice-platform
JARVIS_BRIEFING=1               # 0 = sin briefing al arrancar
JARVIS_MEMORIA_DB=C:\Jarvis-secrets\memoria_sesiones.db
JARVIS_MEMORIA_DIAS=90          # retención de la memoria entre sesiones
```

Google (Gmail y Calendar):

1. Crea un cliente OAuth de tipo escritorio en Google Cloud y guarda el JSON como `C:\Jarvis-secrets\credentials.json`.
2. Autoriza cada servicio una vez. Gmail guarda `token.json` y Calendar un token propio, `token_calendar.json`:
   ```powershell
   venv\Scripts\python.exe reautorizar.py gmail
   venv\Scripts\python.exe reautorizar.py calendar
   ```
3. Publica la app OAuth en producción (ver más abajo) para que la autorización no caduque a los 7 días.

### Autorización de Google

Al arrancar, JARVIS comprueba de verdad `token.json` y `token_calendar.json`: renueva el access token con el refresh_token contra Google, sin leer ni enviar correo ni eventos y sin reescribir el archivo. El resultado sale en la consola (`[AUTH] ...`):

| Estado | Qué significa | Qué hace JARVIS |
|---|---|---|
| válido | El refresh_token funciona y el access token seguía vigente | Nada |
| renovado | El access token había caducado y se ha renovado | Nada |
| caducado o revocado (`invalid_grant`) | Google ya no acepta el refresh_token: app en modo Testing (7 días), acceso retirado, cambio de contraseña o 6 meses sin uso | Avisa |
| caducado sin refresh_token | El token no se puede renovar | Avisa |
| no autorizado / dañado | Falta el archivo del token, o está incompleto o con otros permisos | Avisa |
| sin red | No se pudo hablar con Google: no verificado | Solo lo registra |
| sin credenciales | Falta `credentials.json` | Avisa |

Google devuelve `invalid_grant` tanto si el refresh_token ha caducado como si se ha revocado, así que esos dos casos salen como un solo estado. Si hay que hacer algo, JARVIS lo dice una sola vez en el resumen de arranque, con la causa, y deja en la UI un aviso fijo con el comando exacto. Con el token de Gmail mal no intenta leer el correo del resumen ni abre el navegador. Para arreglarlo:

```powershell
venv\Scripts\python.exe reautorizar.py gmail      # o calendar
```

`reautorizar.py` renombra el token actual con la fecha (`token-caducado-AAAAMMDD-HHMMSS.json`, en `C:\Jarvis-secrets`), abre el flujo OAuth en el navegador pidiendo de nuevo el consentimiento, guarda el token nuevo y confirma que funciona con la misma comprobación. Si el flujo falla o se cancela, el token anterior vuelve a su nombre. No hace falta ningún paso a mano en PowerShell.

**Modo Testing: caducidad a los 7 días.** Una app OAuth de tipo externo con el estado de publicación en Testing ("Prueba") recibe refresh_tokens que Google invalida a los 7 días; a partir de ahí todo falla con `invalid_grant`. Para pasarla a producción:

1. En Google Cloud Console, abre el proyecto del cliente OAuth y ve a *Google Auth Platform → Público* (en la interfaz antigua, *APIs y servicios → Pantalla de consentimiento de OAuth*).
2. En *Estado de publicación*, pulsa *Publicar app* y confirma. Pasa a *En producción*.
3. No hace falta verificar la app para uso personal. Como `gmail.modify` es un permiso restringido, al autorizar Google muestra "Google no ha verificado esta app": entra en *Configuración avanzada → Ir a ... (no seguro)*. Una app sin verificar admite hasta 100 usuarios.
4. Vuelve a ejecutar `reautorizar.py gmail` y `reautorizar.py calendar`: los tokens emitidos en Testing siguen caducando a los 7 días.

Aun en producción, el refresh_token se invalida si retiras el acceso en tu cuenta de Google, si cambias la contraseña (con permisos de Gmail) o tras 6 meses sin usarse. En esos casos, el aviso de arranque y `reautorizar.py` lo resuelven.

Arranque y consulta del log:

```powershell
venv\Scripts\python.exe jarvis.py      # asistente de voz + servidor WebSocket
cd app; npm install; npm start         # UI Electron (opcional)
venv\Scripts\python.exe replay.py 20   # últimos 20 turnos: hora, ruta, tools, latencia, coste
venv\Scripts\python.exe replay.py --resumen   # hoy y 7 días: p50/p95, coste, % por ruta, caché
```

## Diagnóstico

```powershell
venv\Scripts\python.exe doctor.py            # tabla legible
venv\Scripts\python.exe doctor.py --json     # JSON estable (version, estado, resumen, comprobaciones)
venv\Scripts\python.exe doctor.py --live     # además valida la clave de Anthropic (max_tokens=1), la voz de ElevenLabs y los tokens de Google
```

Comprueba Python y el venv, las dependencias de `requirements.txt`, ffmpeg, VLC, yt-dlp, Node y Electron, Ollama y su modelo, audio, Piper, Whisper y CUDA, credenciales y tokens de Google, `.gitignore` y secretos en git, el puerto 8765, la zona horaria, los proyectos del operador, `logs/` y git. Es de solo lectura: no escribe archivos, no graba audio ni carga modelos, y nunca imprime claves (solo "definida" y su longitud). Sin `--live` no hay red, así que los tokens de Google salen como "no verificado". Con `--live`, se renuevan en memoria con la misma comprobación que el arranque de JARVIS y salen con su estado real.

- **OK:** correcto.
- **AVISO:** funciona con limitaciones (sin Ollama todo va a Claude, sin CUDA Whisper va en CPU, token de Google sin verificar por falta de red…). No cambia el código de salida.
- **ERROR:** algo que impide arrancar o una función básica. Cada fallo trae la acción para arreglarlo. Código de salida 1 si hay algún ERROR.

## Estructura del repo

```
jarvis.py            bucle principal: escuchar → pensar → hablar
audio.py             micrófono, Whisper con umbral de confianza, Piper, voz por frases, escucha de órdenes de parada
brain.py             router, agente Claude con streaming, tools, gate de permisos
frases.py            troceado de la respuesta en frases para la voz
parada.py            listas de órdenes de silencio y apagado, filtro de eco y detector de voz dominante
tts_elevenlabs.py    TTS opcional con fallback a Piper y marca de turno privado
metrics.py           métricas por turno, coste estimado y log JSONL
replay.py            consulta del log de turnos
doctor.py            diagnóstico del entorno (--live: claves y tokens de Google)
reautorizar.py       vuelve a autorizar Gmail o Calendar (renombra el token, OAuth y comprobación)
limpiar_logs.py      limpieza de un solo uso de logs antiguos (direcciones y turnos privados)
limpiar_memoria.py   limpieza de un solo uso de direcciones en el historial de memoria.json
ui_bridge.py         servidor WebSocket local para la UI
memoria.py           historial de conversación
briefing.py          briefing hablado: agenda, GitHub, correo, tareas y tiempo, lo urgente primero
memoria_sesiones.py  memoria entre sesiones: resúmenes en SQLite FTS5, recordar, olvidar y retención
skills/              gmail, calendario, github, google_auth, pantalla, operador, musica, sistema, tareas, webs
app/                 UI Electron (main.js, preload.js, renderer.js, index.html, styles.css)
tests/               tests unitarios y evals (test_evals.py)
```

## Roadmap

- **Evals en vivo de forma periódica** con `JARVIS_EVALS_LIVE=1`: tasa de acierto real de la tool elegida por versión de prompt y de modelo.
- **Captura de la ventana activa** en lugar de la pantalla completa, para enviar menos datos.
- **Automatización de navegador** para tareas web que hoy se quedan en abrir una URL.
- **Empaquetado e instalador** para no depender de un venv y rutas fijas.
- **Más proyectos con suite configurada** en `ejecutar_tests`. Hoy hay dos.

## Limitaciones conocidas

- La captura es de la pantalla completa del monitor principal. No está probada con varios monitores ni con un escalado de Windows distinto del 100 %.
- Sin automatización de navegador: abre URLs y búsquedas en el navegador, y busca en la web, pero no controla el navegador ni lee páginas.
- Solo voz: no hay entrada por texto ni en la UI ni en la terminal.
- Solo Windows. El operador accede a los proyectos que viven en WSL a través de `wsl.exe` y `\\wsl.localhost`.
- Lo que dices, lo que leen las tools (emails, archivos) y las capturas confirmadas se envían a la API de Anthropic cuando el router elige Claude. La privacidad del TTS y del log no cambia eso.
- Con streaming, la voz empieza cuando Claude termina la respuesta, no durante la generación: es el precio de no decir nunca texto de una iteración que acabe usando tools.
- El coste del log es una estimación con precios fijos en `metrics.py` y no se concilia con la factura real.
- Un solo usuario y una sola máquina: rutas, proyectos y destinatarios están configurados en el código.
