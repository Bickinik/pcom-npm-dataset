# Full data run (day 1). Run from the repo folder, one step at a time:
#   .\run_full.ps1 update   # pull the advisory DB and drop the stale advisory cache
#   .\run_full.ps1 smoke    # 20 packages with downloads -> data_smoke\ (check by hand)
#   .\run_full.ps1 full     # all advisory packages + 30k random names -> data_full\
# Logs go to <out>\build.log. Watch progress with: Get-Content data_full\build.log -Wait -Tail 5
param([Parameter(Mandatory)][ValidateSet("update", "smoke", "full")][string]$Step)

$ErrorActionPreference = "Stop"
$db = "..\advisory-database"
$extra = "extra_packages_random.txt"

function Build([string]$out, [string]$more) {
    New-Item -ItemType Directory -Force $out | Out-Null
    $cmd = "python -m npmdata.build_dataset --advisory-db $db --extra-packages $extra " +
           "--seed 42 --workers 4 --out $out $more > $out\build.log 2>&1"
    Write-Host "running: $cmd"
    $t = Get-Date
    cmd /c $cmd
    if ($LASTEXITCODE -ne 0) { throw "build failed, see $out\build.log" }
    Write-Host ("done in {0:N0} min" -f ((Get-Date) - $t).TotalMinutes)
    Get-Content "$out\build_info.json"
}

switch ($Step) {
    "update" {
        git -C $db pull --ff-only
        if ($LASTEXITCODE -ne 0) { throw "git pull failed" }
        git -C $db log -1 --format="advisory DB now at %ci"
        # The advisory cache only notices a changed file count, not edited files.
        if (Test-Path cache\advisories_npm.json) { Remove-Item cache\advisories_npm.json }
        Write-Host "advisory cache removed; it is rebuilt on the next run"
    }
    "smoke" {
        # --max-packages takes the first names alphabetically: mostly scoped (@...) ones,
        # which is the case the downloads bug was about.
        Build "data_smoke" "--max-packages 20"
        python -I -c "import sqlite3; c=sqlite3.connect('data_smoke/training_data.sqlite'); print('rows, rows with downloads M0:', c.execute('select count(*), count(package_downloads_M0) from training_data').fetchone())"
        Get-Content data_smoke\errors.tsv
    }
    "full" { Build "data_full" "" }
}
