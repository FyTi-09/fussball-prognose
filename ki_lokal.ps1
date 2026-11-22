# KI-Teil lokal mit Ollama ausführen und das Ergebnis zu GitHub hochladen.
# Wird von der Windows-Aufgabenplanung gestartet (z. B. Fr und Sa vormittags).
# Ist der PC aus, fehlen an diesem Spieltag nur die KI-Infos – sonst läuft alles normal.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
git pull --rebase --quiet
& .\.venv\Scripts\python.exe run.py --nur-ki
git add data/ki
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "KI-Daten (lokal) $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    git pull --rebase --quiet
    git push --quiet
}
