# Optional: start at Windows login (opens browser once; reuses server later).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "Cursor-Codex-Manager.lnk"
$w = New-Object -ComObject WScript.Shell
$lnk = $w.CreateShortcut($lnkPath)
$lnk.TargetPath = "wscript.exe"
$lnk.Arguments = "`"$root\open.vbs`""
$lnk.WorkingDirectory = $root
$lnk.WindowStyle = 7
$lnk.Description = "Cursor/Codex Manager (reuse Streamlit if running)"
$lnk.Save()
Write-Host "已加入开机启动: $lnkPath"
Write-Host "取消: 删除该快捷方式即可。"
