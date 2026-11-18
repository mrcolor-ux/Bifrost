# Bifrost installer (Windows): registers the local server in Claude Code and the Claude desktop app.
# Double-click install.bat, which runs this file. Works with Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$Src = $PSScriptRoot

Write-Host ''
Write-Host 'Bifrost - Claude <-> Figma'
Write-Host ''

if (-not (Test-Path (Join-Path $Src 'server\server.py'))) {
    Write-Host 'x Bifrost files not found next to this installer.'
    Write-Host '  Right-click Bifrost.zip > Extract All..., then run install.bat from the extracted folder.'
    exit 1
}

# --- Python 3.9+ -------------------------------------------------------------
# "python" can be a Microsoft Store shortcut that only opens the Store, so each candidate
# has to actually run and report its real path.
function Find-Python {
    $candidates = @(
        [pscustomobject]@{ Exe = 'py'; Args = @('-3') },
        [pscustomobject]@{ Exe = 'python'; Args = @() },
        [pscustomobject]@{ Exe = 'python3'; Args = @() }
    )
    foreach ($dir in (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*", "$env:ProgramFiles\Python3*" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
        $candidates += [pscustomobject]@{ Exe = (Join-Path $dir.FullName 'python.exe'); Args = @() }
    }
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $pyArgs = $c.Args
        try {
            $out = & $c.Exe @pyArgs -c 'import sys; print(sys.executable) if sys.version_info >= (3, 9) else None' 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and $out) {
            $path = ([string]($out | Select-Object -Last 1)).Trim()
            if ($path -and (Test-Path $path)) { return $path }
        }
    }
    return $null
}

$Py = Find-Python
if (-not $Py) {
    Write-Host 'x Python 3.9+ is not installed.'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host 'Install Python 3.12 now with winget? (y/n)'
        if ($answer -match '^[yYsS]') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $Py = Find-Python
        }
    }
    if (-not $Py) {
        Write-Host '  Install it from https://www.python.org/downloads/ (check "Add python.exe to PATH"),'
        Write-Host '  then run install.bat again.'
        exit 1
    }
}
Write-Host "OK Python: $Py"

# --- Permanent home ------------------------------------------------------------
# Claude keeps pointing at this folder, so it can't stay in Downloads or a temp folder.
$Dir = $Src
if (-not (Test-Path (Join-Path $Src '.git'))) {
    $Target = Join-Path $env:LOCALAPPDATA 'Bifrost'
    if ((Resolve-Path $Src).Path.TrimEnd('\') -ne $Target.TrimEnd('\')) {
        if ((Test-Path $Target) -and -not (Test-Path (Join-Path $Target 'server\server.py'))) {
            Write-Host "x $Target already exists and isn't Bifrost. Move it away and run this again."
            exit 1
        }
        $srcRoot = (Resolve-Path $Src).Path.TrimEnd('\')
        foreach ($f in (Get-ChildItem $srcRoot -Recurse -File)) {
            $dest = Join-Path $Target $f.FullName.Substring($srcRoot.Length + 1)
            New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
            Copy-Item -LiteralPath $f.FullName -Destination $dest -Force
        }
        $Dir = $Target
        Write-Host "OK Installed in $Dir"
    }
    # Files from a downloaded zip are marked as "from the internet"; clear it so nothing nags later.
    Get-ChildItem $Dir -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
}
$Server = Join-Path $Dir 'server\server.py'
$Manifest = Join-Path $Dir 'figma-plugin\manifest.json'

# --- Register in Claude ----------------------------------------------------------
# The regular installer keeps its config in %APPDATA%\Claude; the Microsoft Store / MSIX
# version keeps it inside its package folder.
$Desktop = @()
$appData = Join-Path $env:APPDATA 'Claude'
$installed = (Test-Path $appData) -or (Test-Path "$env:LOCALAPPDATA\AnthropicClaude") -or (Test-Path "$env:LOCALAPPDATA\Programs\Claude")
foreach ($pkg in (Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue)) {
    $Desktop += (Join-Path $pkg.FullName 'LocalCache\Roaming\Claude')
    $installed = $true
}
if ($installed) { $Desktop += $appData }

& $Py -I (Join-Path $Dir 'server\register.py') $Py $Server @Desktop
if ($LASTEXITCODE -ne 0) { exit 1 }

try { Set-Clipboard -Value $Manifest } catch {}

Write-Host ''
Write-Host 'Next steps'
Write-Host '  1. In the Figma desktop app, open a design file and use'
Write-Host '     Plugins > Development > Import plugin from manifest...'
Write-Host '     In the file window paste the path (Ctrl+V) in the file name box and press Enter.'
Write-Host '     The path is already copied:'
Write-Host "     $Manifest"
Write-Host '  2. Run  Plugins > Development > Bifrost > Conectar a Claude'
Write-Host '  3. Quit Claude completely (right-click its icon next to the clock > Quit) and open it again.'
Write-Host ''
Write-Host 'Done. Ask Claude: "what do I have selected in Figma?"'
