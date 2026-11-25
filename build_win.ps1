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

$PyVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
if ($PyVersion -ne "3.12") {
    Write-Host "ERRORE: la build richiede Python 3.12 (trovato '$PyVersion')." -ForegroundColor Red
    exit 1
}
$env:SOURCE_DATE_EPOCH = (& git log -1 --format=%ct 2>$null | Select-Object -First 1)
if (-not $env:SOURCE_DATE_EPOCH) { $env:SOURCE_DATE_EPOCH = "0" }
Write-Host "SOURCE_DATE_EPOCH: $env:SOURCE_DATE_EPOCH" -ForegroundColor Gray

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
& $Python -m pip install -r requirements-build.txt --quiet

# 1. Pulizia build precedenti
Write-Host "Pulizia vecchi file..." -ForegroundColor Gray
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "$ExeName.spec") { Remove-Item -Force "$ExeName.spec" }

# 2. Esecuzione PyInstaller — onedir: niente estrazione del bundle a ogni avvio,
#    avvio molto più rapido e meno falsi positivi degli antivirus.
Write-Host "Generazione eseguibile in corso (Onedir, No Console)..." -ForegroundColor Yellow
$env:AETHICC_BUILD_MODE = "onedir"
$env:AETHICC_APP_NAME = $ExeName
& $Python -m PyInstaller --clean --noconfirm Ae_Thicc_Launcher.spec

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
        $IsccVersion = (Get-Item $IsccPath).VersionInfo.ProductVersion
        Write-Host "Inno Setup: $IsccVersion ($IsccPath)" -ForegroundColor Gray
        Write-Host "Compilazione installer Inno Setup..." -ForegroundColor Yellow
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

$SetupPath = Join-Path $DistRoot "${ExeName}_Setup_${Version}.exe"
$Artifacts = @($ZipPath, $SetupPath) | Where-Object { Test-Path $_ }
if ($Artifacts.Count -gt 0) {
    $Sums = Join-Path $DistRoot "SHA256SUMS.txt"
    $Lines = foreach ($file in $Artifacts) {
        $hash = (Get-FileHash -Algorithm SHA256 -Path $file).Hash.ToLower()
        "$hash  $(Split-Path $file -Leaf)"
    }
    Set-Content -Path $Sums -Value $Lines -Encoding ascii
    Write-Host "   -> $Sums" -ForegroundColor Gray
}
& $Python verify_release.py --platform windows --artifacts-dir $DistRoot --require-sums

Write-Host "Build completata con successo!" -ForegroundColor Green
Write-Host "   Cartella: $DistDir" -ForegroundColor Green
Write-Host "   Zip:      $ZipPath" -ForegroundColor Green
if (Test-Path $SetupPath) {
    Write-Host "   Setup:    $SetupPath" -ForegroundColor Green
}
