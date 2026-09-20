# midi_monitor.ps1
# Monitor de MIDI simples via PowerShell, sem precisar de DAW nenhum.
# Usa a API MIDI do Windows (winmm.dll) direto, via P/Invoke.
#
# Uso:
#   1) Abre o PowerShell (nao precisa ser admin)
#   2) cd ate a pasta onde salvou este arquivo
#   3) .\midi_monitor.ps1
#      (se der erro de "execution policy", rode antes:
#       Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass)
#   4) O script lista os dispositivos MIDI de entrada disponiveis.
#      Digita o numero da porta criada pelo rtpMIDI (deve ter um nome
#      parecido com o nome do seu computador, ex: "DESKTOP-UTH71NT" ou
#      similar -- é a MESMA porta que aparece no Reaper).
#   5) Bate nos pads do modulo -- cada nota deve aparecer na tela.
#   6) Ctrl+C pra sair.

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class MidiMonitor {
    [DllImport("winmm.dll")]
    static extern int midiInGetNumDevs();

    [DllImport("winmm.dll", CharSet = CharSet.Auto)]
    static extern int midiInGetDevCaps(IntPtr uDeviceID, ref MIDIINCAPS caps, int uSize);

    [DllImport("winmm.dll")]
    static extern int midiInOpen(out IntPtr hMidiIn, int uDeviceID, MidiInProc dwCallback, IntPtr dwInstance, int dwFlags);

    [DllImport("winmm.dll")]
    static extern int midiInStart(IntPtr hMidiIn);

    [DllImport("winmm.dll")]
    static extern int midiInStop(IntPtr hMidiIn);

    [DllImport("winmm.dll")]
    static extern int midiInClose(IntPtr hMidiIn);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    public struct MIDIINCAPS {
        public short wMid;
        public short wPid;
        public int vDriverVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string szPname;
        public int dwSupport;
    }

    public delegate void MidiInProc(IntPtr hMidiIn, int wMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2);

    const int MIM_DATA = 0x3C3;
    const int CALLBACK_FUNCTION = 0x30000;

    static MidiInProc callback;
    static IntPtr handle;

    public static string[] ListDevices() {
        int n = midiInGetNumDevs();
        string[] list = new string[n];
        for (int i = 0; i < n; i++) {
            MIDIINCAPS caps = new MIDIINCAPS();
            midiInGetDevCaps((IntPtr)i, ref caps, Marshal.SizeOf(typeof(MIDIINCAPS)));
            list[i] = i + ": " + caps.szPname;
        }
        return list;
    }

    public static string OpenAndListen(int deviceId) {
        callback = new MidiInProc(MidiCallback);
        int result = midiInOpen(out handle, deviceId, callback, IntPtr.Zero, CALLBACK_FUNCTION);
        if (result != 0) {
            return "Erro ao abrir dispositivo (codigo " + result + ")";
        }
        midiInStart(handle);
        return null;
    }

    public static void Stop() {
        if (handle != IntPtr.Zero) {
            midiInStop(handle);
            midiInClose(handle);
        }
    }

    static void MidiCallback(IntPtr hMidiIn, int wMsg, IntPtr dwInstance, IntPtr dwParam1, IntPtr dwParam2) {
        if (wMsg == MIM_DATA) {
            long data = dwParam1.ToInt64();
            int status = (int)(data & 0xFF);
            int data1 = (int)((data >> 8) & 0xFF);
            int data2 = (int)((data >> 16) & 0xFF);
            string tipo;
            int canal = (status & 0x0F) + 1;
            int comando = status & 0xF0;
            if (comando == 0x90 && data2 > 0) { tipo = "NOTE ON "; }
            else if (comando == 0x80 || (comando == 0x90 && data2 == 0)) { tipo = "NOTE OFF"; }
            else { tipo = "OUTRO   "; }
            Console.WriteLine(string.Format("[{0:HH:mm:ss.fff}] {1}  canal={2}  nota={3}  velocidade={4}", DateTime.Now, tipo, canal, data1, data2));
        }
    }
}
"@

Write-Host ""
Write-Host "=== Dispositivos MIDI de entrada disponiveis ==="
$devices = [MidiMonitor]::ListDevices()
if ($devices.Count -eq 0) {
    Write-Host "Nenhum dispositivo MIDI encontrado. Confira se o rtpMIDI esta com a sessao conectada (Participants preenchido)."
    exit
}
$devices | ForEach-Object { Write-Host $_ }
Write-Host ""

$escolha = Read-Host "Digita o numero da porta que quer monitorar"
$deviceId = [int]$escolha

$erro = [MidiMonitor]::OpenAndListen($deviceId)
if ($erro) {
    Write-Host $erro -ForegroundColor Red
    exit
}

Write-Host ""
Write-Host "Escutando a porta $deviceId... bate num pad do modulo agora." -ForegroundColor Green
Write-Host "(Ctrl+C para sair)"
Write-Host ""

try {
    while ($true) {
        Start-Sleep -Milliseconds 200
    }
} finally {
    [MidiMonitor]::Stop()
}
