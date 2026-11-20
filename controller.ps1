param(
    [Parameter(Mandatory = $true)]
    [string]$Port
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class MediaKeys {
    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT {
        public uint type;
        public InputUnion data;
    }
    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion {
        [FieldOffset(0)] public KEYBDINPUT keyboard;
        [FieldOffset(0)] public MOUSEINPUT mouse;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct KEYBDINPUT {
        public ushort vk;
        public ushort scan;
        public uint flags;
        public uint time;
        public UIntPtr extraInfo;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT {
        public int dx;
        public int dy;
        public uint mouseData;
        public uint flags;
        public uint time;
        public UIntPtr extraInfo;
    }
    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, INPUT[] inputs, int size);

    public static void Press(params ushort[] keys) {
        var inputs = new INPUT[keys.Length * 2];
        for (int i = 0; i < keys.Length; i++) {
            inputs[i].type = 1;
            inputs[i].data.keyboard.vk = keys[i];
            inputs[keys.Length + i].type = 1;
            inputs[keys.Length + i].data.keyboard.vk = keys[keys.Length - 1 - i];
            inputs[keys.Length + i].data.keyboard.flags = 2;
        }
        uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
        if (sent != inputs.Length) {
            int error = Marshal.GetLastWin32Error();
            // Release every key if Windows accepted only part of the sequence.
            var releases = new INPUT[keys.Length];
            Array.Copy(inputs, keys.Length, releases, 0, keys.Length);
            SendInput((uint)releases.Length, releases, Marshal.SizeOf(typeof(INPUT)));
            throw new Win32Exception(error,
                "Windows did not accept the complete key sequence");
        }
    }
}
'@

$serial = [System.IO.Ports.SerialPort]::new($Port, 115200)
$serial.ReadTimeout = 500
try {
    $serial.Open()
    Write-Host "Listening on $Port. Press Ctrl+C to stop."
    while ($true) {
        try {
            $command = $serial.ReadLine().Trim()
        }
        catch [System.TimeoutException] {
            continue
        }
        switch ($command) {
            'PREV' { [MediaKeys]::Press(0xB1) }
            'PLAY' { [MediaKeys]::Press(0xB3) }
            'NEXT' { [MediaKeys]::Press(0xB0) }
            'MUTE' { [MediaKeys]::Press(0x11, 0x36) }
            default { Write-Warning "Unrecognized device message: $command"; continue }
        }
        Write-Host "Sent $command"
    }
}
finally {
    $serial.Dispose()
}
