# Crea el acceso directo de JARVIS en el Escritorio y en el menu Inicio.
# Solo hace falta ejecutarlo una vez (o si cambian las rutas):
#   powershell -ExecutionPolicy Bypass -File C:\Jarvis\app\crear_acceso_directo.ps1
# El acceso directo lleva el mismo AppUserModelID que la ventana Electron
# (Jarvis.Desktop) para que el icono anclado y la ventana sean un solo boton.
$ErrorActionPreference = 'Stop'

$AppDir = $PSScriptRoot
$AppId  = 'Jarvis.Desktop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class AumidLnk {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid fmtid; public uint pid; }

    [StructLayout(LayoutKind.Explicit, Size = 16)]
    struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint c);
        void GetAt(uint i, out PropertyKey k);
        void GetValue(ref PropertyKey k, out PropVariant v);
        void SetValue(ref PropertyKey k, ref PropVariant v);
        void Commit();
    }

    public static void SetAppId(string lnk, string appId) {
        Type t = Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046"));
        object link = Activator.CreateInstance(t);
        try {
            ((IPersistFile)link).Load(lnk, 2 /* STGM_READWRITE */);
            PropertyKey key = new PropertyKey {
                fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };  // PKEY_AppUserModel_ID
            PropVariant v = new PropVariant { vt = 31 /* VT_LPWSTR */, p = Marshal.StringToCoTaskMemUni(appId) };
            try {
                IPropertyStore store = (IPropertyStore)link;
                store.SetValue(ref key, ref v);
                store.Commit();
            } finally {
                Marshal.FreeCoTaskMem(v.p);
            }
            ((IPersistFile)link).Save(lnk, true);
        } finally {
            Marshal.ReleaseComObject(link);
        }
    }
}
'@

$destinos = @(
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('Programs')   # menu Inicio
)

$wsh = New-Object -ComObject WScript.Shell
foreach ($dir in $destinos) {
    $lnk = Join-Path $dir 'JARVIS.lnk'
    $s = $wsh.CreateShortcut($lnk)
    $s.TargetPath       = Join-Path $env:WINDIR 'System32\wscript.exe'
    $s.Arguments        = '"' + (Join-Path $AppDir 'start.vbs') + '"'
    $s.WorkingDirectory = $AppDir
    $s.IconLocation     = (Join-Path $AppDir 'jarvis.ico') + ',0'
    $s.Description      = 'JARVIS - asistente de voz'
    $s.Save()
    [AumidLnk]::SetAppId($lnk, $AppId)
    Write-Host "Creado: $lnk"
}
