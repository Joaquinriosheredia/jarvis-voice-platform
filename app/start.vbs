' Lanzador de JARVIS (sin terminal visible).
' 1. Si jarvis.py no esta corriendo, lo arranca oculto con la salida en C:\Jarvis\jarvis.log
' 2. Abre la ventana Electron (si ya esta abierta, Electron solo la muestra)
Option Explicit

Const JARVIS_DIR = "C:\Jarvis"

Dim sh, wmi, procs, env, python, electron
Set sh   = CreateObject("WScript.Shell")
python   = JARVIS_DIR & "\venv\Scripts\python.exe"
electron = JARVIS_DIR & "\app\node_modules\electron\dist\electron.exe"

Set wmi   = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process " & _
                          "WHERE Name = 'python.exe' AND CommandLine LIKE '%jarvis.py%'")

If procs.Count = 0 Then
    ' UTF-8 obligatorio: con la salida redirigida, los print con emojis fallarian en cp1252
    Set env = sh.Environment("PROCESS")
    env("PYTHONUTF8")       = "1"
    env("PYTHONIOENCODING") = "utf-8"
    sh.CurrentDirectory = JARVIS_DIR
    sh.Run "cmd /c " & python & " -u jarvis.py > jarvis.log 2>&1", 0, False
End If

sh.Run """" & electron & """ """ & JARVIS_DIR & "\app""", 1, False
