@{
    # Keep this file pure ASCII. Windows PowerShell 5.1 reads a BOM-less .psd1 as
    # ANSI (cp936 here), and non-ASCII comments can make Import-PowerShellDataFile
    # fail outright ("cannot be parsed as a PowerShell data file").
    #
    # These values are the fallback used by scripts/common.ps1 only when the env
    # vars are missing; the running app reads .env instead. Keep both in sync:
    # clone = 1.7B Base (voice_clone_1.7b), design = 1.7B VoiceDesign.
    VoiceClone = @{
        Name      = "Qwen3-TTS 1.7B Voice Clone"
        ModelId   = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
        ModelPath = "D:/ouyang/model/voice_clone_1.7b"
        Port      = 7861
    }
    VoiceDesign = @{
        Name      = "Qwen3-TTS 1.7B Voice Design"
        ModelId   = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
        ModelPath = "D:/ouyang/model/voice_design_1.7b"
        Port      = 7862
    }
}
