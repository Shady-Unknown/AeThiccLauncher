# Script di compilazione per Ae Thicc Launcher su Windows 11
$ErrorActionPreference = "Stop"
$ExeName = "Ae_Thicc_Launcher"

Write-Host "--- Avvio processo di build blindato per $ExeName ---" -ForegroundColor Cyan

# 0. Controllo Ambiente Virtuale e Dipendenze
$VenvPath = $null
if (Test-Path ".venv\Scripts\python.exe") {
    $VenvPath = ".venv"
} elseif (Test-Path ".venv_win\Scripts\python.exe") {
    $VenvPath = ".venv_win"
}
if (-not $VenvPath) {
    Write-Host "ERRORE: Ambiente virtuale non trovato (.venv o .venv_win)!" -ForegroundColor Red
    exit
}
$Python = Join-Path $VenvPath "Scripts\python.exe"

# Versione applicazione: unica fonte in core/version.py
$Version = (& $Python -c "from core.version import __version__; print(__version__)" 2>$null | Select-Object -First 1)
if ($Version) { $Version = $Version.Trim() }
if (-not $Version -or $Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "ERRORE: impossibile leggere la versione da core\version.py (valore: '$Version')" -ForegroundColor Red
    exit 1
}
Write-Host "Versione applicazione: $Version" -ForegroundColor Cyan

$ChangelogHasVersion = Test-Path "CHANGELOG.md" -PathType Leaf
if ($ChangelogHasVersion) {
    $ChangelogHasVersion = Select-String -Path "CHANGELOG.md" -Pattern "^## v$([regex]::Escape($Version))\b" -Quiet
}
if (-not $ChangelogHasVersion) {
    Write-Host "ATTENZIONE: CHANGELOG.md non ha una sezione '## v$Version'." -ForegroundColor Yellow
    Write-Host "   Le note di release useranno il testo di fallback 'Release $Version'." -ForegroundColor Gray
}

Write-Host "Verifica dipendenze..." -ForegroundColor Gray
& $Python -m pip install -r requirements.txt --quiet
& $Python -m pip install pyinstaller --quiet

# 1. Pulizia build precedenti
Write-Host "Pulizia vecchi file..." -ForegroundColor Gray
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "$ExeName.spec") { Remove-Item -Force "$ExeName.spec" }

# 2. Esecuzione PyInstaller — onedir: niente estrazione del bundle a ogni avvio,
#    avvio molto più rapido e meno falsi positivi degli antivirus.
Write-Host "Generazione eseguibile in corso (Onedir, No Console)..." -ForegroundColor Yellow
& $Python -m PyInstaller --noconsole --onedir --clean --name $ExeName `
    --icon=".media\icon.ico" `
    --collect-all "curl_cffi" `
    --collect-all "keyring" `
    --hidden-import "PyQt6.QtWebEngineWidgets" `
    --hidden-import "PyQt6.QtWebEngineCore" `
    main.py

$DistRoot = "dist"
$DistDir = Join-Path $DistRoot $ExeName
if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Path $DistDir | Out-Null }

# 3. Gestione Risorse Esterne (accanto all'exe, dentro la cartella onedir)
Write-Host "Organizzazione asset esterni nella cartella '$DistDir'..." -ForegroundColor Yellow

$Folders = @(".media", "bin", "data", "lists", "local_dbs")
foreach ($folder in $Folders) {
    if (Test-Path $folder) {
        $dest = Join-Path $DistDir $folder
        if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest | Out-Null }
        Copy-Item -Path "$folder\*" -Destination $dest -Recurse -Force
        Write-Host "   -> Cartella '$folder' copiata correttamente." -ForegroundColor Gray
    }
}

# File singoli
$Files = @("master_catalog.json")
foreach ($file in $Files) {
    if (Test-Path $file) {
        Copy-Item -Path $file -Destination $DistDir -Force
        Write-Host "   -> File '$file' copiato." -ForegroundColor Gray
    }
}

$DistExe = Join-Path $DistDir "$ExeName.exe"
if (-not (Test-Path $DistExe)) {
    Write-Host "ERRORE: L'eseguibile non è stato generato. Controlla i log di PyInstaller sopra." -ForegroundColor Red
    exit
}

# 4. Zip portatile (contenuto alla radice: exe, _internal, asset)
$ZipPath = Join-Path (Resolve-Path ".").Path "${DistRoot}\${ExeName}_Windows_${Version}.zip"
$SevenZip = Join-Path (Resolve-Path ".").Path "bin\Windows\x64\7z.exe"
Write-Host "Creazione pacchetto portatile..." -ForegroundColor Yellow
if (Test-Path $SevenZip) {
    Push-Location $DistDir
    try {
        & $SevenZip a -tzip $ZipPath "*" -mx=5 | Out-Null
    } finally {
        Pop-Location
    }
} else {
    Compress-Archive -Path "$DistDir\*" -DestinationPath $ZipPath -Force
}
Write-Host "   -> $ZipPath" -ForegroundColor Gray

# 5. Installer Inno Setup (ISCC.exe often is NOT on PATH: search known paths + registry)
function Find-Iscc {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 5\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 5\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }

    $uninstallRoots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($root in $uninstallRoots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($entry in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
            $props = Get-ItemProperty $entry.PSPath -ErrorAction SilentlyContinue
            if ($props.DisplayName -like "*Inno Setup*" -and $props.InstallLocation) {
                $candidate = Join-Path $props.InstallLocation "ISCC.exe"
                if (Test-Path $candidate) { return $candidate }
            }
        }
    }
    return $null
}

$IsccPath = Find-Iscc
if ($IsccPath) {
    if (Test-Path "installer.iss") {
        Write-Host "Compilazione installer Inno Setup ($IsccPath)..." -ForegroundColor Yellow
        & $IsccPath "/DAppVersion=$Version" "installer.iss"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERRORE: Inno Setup ha restituito codice $LASTEXITCODE" -ForegroundColor Red
        }
        $Setup = Join-Path $DistRoot "${ExeName}_Setup_${Version}.exe"
        if (Test-Path $Setup) {
            Write-Host "   -> $Setup" -ForegroundColor Gray
        } else {
            Write-Host "   ATTENZIONE: installer.iss non ha prodotto $Setup" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "Inno Setup non trovato: salto l'installer .exe." -ForegroundColor Yellow
    Write-Host "   Installa con: winget install -e --id JRSoftware.InnoSetup" -ForegroundColor Gray
    Write-Host "   Se e' gia' installato, verifica C:\Program Files (x86)\Inno Setup 6\ISCC.exe" -ForegroundColor Gray
}

Write-Host "Build completata con successo!" -ForegroundColor Green
Write-Host "   Cartella: $DistDir" -ForegroundColor Green
Write-Host "   Zip:      $ZipPath" -ForegroundColor Green
$SetupPath = Join-Path $DistRoot "${ExeName}_Setup_${Version}.exe"
if (Test-Path $SetupPath) {
    Write-Host "   Setup:    $SetupPath" -ForegroundColor Green
}
