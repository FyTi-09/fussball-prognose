# KI-Teil lokal mit Ollama ausführen und das Ergebnis zu GitHub hochladen.
# Wird von der Windows-Aufgabenplanung gestartet (Fr und Sa 10:00).
# Ist der PC aus, fehlen an diesem Spieltag nur die KI-Infos – sonst läuft alles normal.
# Protokoll: ki_lokal.log (UTF-8) im selben Ordner.
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$log = Join-Path $PSScriptRoot "ki_lokal.log"
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Encoding utf8

# Führt ein Programm aus, schreibt alle Ausgaben ins Protokoll und bricht bei Fehler ab.
# Git schreibt Hinweise nach stderr; das ist kein Fehler, nur der Rückgabewert zählt.
function Invoke-Schritt([string]$name, [scriptblock]$befehl, [int[]]$ok = @(0)) {
    $alt = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $befehl 2>&1 | ForEach-Object { "$_" } | Out-File $log -Append -Encoding utf8
    $code = $LASTEXITCODE
    $ErrorActionPreference = $alt
    if ($ok -notcontains $code) {
        "FEHLER bei '$name' (Rückgabewert $code)" | Out-File $log -Append -Encoding utf8
        exit $code
    }
    return $code
}

Invoke-Schritt "git pull" { git pull --rebase --quiet } | Out-Null
Invoke-Schritt "KI-Lauf" { & .\.venv\Scripts\python.exe run.py --nur-ki } | Out-Null
Invoke-Schritt "git add" { git add data/ki } | Out-Null
$geaendert = Invoke-Schritt "git diff" { git diff --cached --quiet } @(0, 1)
if ($geaendert -eq 1) {
    Invoke-Schritt "git commit" { git commit -m "KI-Daten (lokal) $(Get-Date -Format 'yyyy-MM-dd HH:mm')" } | Out-Null
    Invoke-Schritt "git pull" { git pull --rebase --quiet } | Out-Null
    Invoke-Schritt "git push" { git push --quiet } | Out-Null
    "Hochgeladen." | Out-File $log -Append -Encoding utf8
} else {
    "Keine neuen KI-Daten." | Out-File $log -Append -Encoding utf8
}
exit 0
