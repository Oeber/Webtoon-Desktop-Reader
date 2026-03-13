param(
    [string[]]$Site,
    [switch]$ClearUserAgent,
    [switch]$ClearWebEngine
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$dbPath = Join-Path $projectRoot "data\reader.db"
$webEngineRoot = Join-Path $projectRoot "data\webengine"

if (-not (Test-Path $dbPath)) {
    throw "Database not found at $dbPath. Start the app once so it can create data\reader.db."
}

$pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }
$clearUserAgentFlag = if ($ClearUserAgent) { "1" } else { "0" }

$script = @'
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1])
clear_user_agent = sys.argv[2] == "1"
requested_sites = [item.strip() for item in sys.argv[3:] if item.strip()]

COOKIE_PREFIX = "site_session_cookies:"
UA_PREFIX = "site_session_user_agent:"

conn = sqlite3.connect(db_path)
try:
    if requested_sites:
        sites = requested_sites
    else:
        rows = conn.execute(
            "SELECT key FROM app_settings WHERE key LIKE ? ORDER BY key",
            (f"{COOKIE_PREFIX}%",),
        ).fetchall()
        sites = [str(row[0])[len(COOKIE_PREFIX):].strip() for row in rows]

    if not sites:
        print(f"db={db_path}")
        print("sites_cleared=0")
        print("No cached site cookies found.")
        raise SystemExit(0)

    total_cleared = 0
    for site in sites:
        cookie_key = f"{COOKIE_PREFIX}{site}"
        ua_key = f"{UA_PREFIX}{site}"

        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (cookie_key,),
        ).fetchone()
        had_cookie_value = row is not None and str(row[0] or "").strip() not in ("", "[]")

        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, '[]', strftime('%s', 'now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (cookie_key,),
        )

        cleared_user_agent = False
        if clear_user_agent:
            existing_ua = conn.execute(
                "SELECT 1 FROM app_settings WHERE key = ?",
                (ua_key,),
            ).fetchone()
            conn.execute("DELETE FROM app_settings WHERE key = ?", (ua_key,))
            cleared_user_agent = existing_ua is not None

        if had_cookie_value:
            total_cleared += 1

        print(f"site={site}")
        print(f"cookies_cleared={'yes' if had_cookie_value else 'already-empty'}")
        print(f"user_agent_cleared={'yes' if cleared_user_agent else 'no'}")

    conn.commit()
    print(f"db={db_path}")
    print(f"sites_cleared={len(sites)}")
    print(f"sites_with_cookie_values={total_cleared}")
finally:
    conn.close()
'@

$arguments = @("-", $dbPath, $clearUserAgentFlag)
if ($Site) {
    $arguments += $Site
}

$script | & $pythonExe $arguments

if ($LASTEXITCODE -ne 0) {
    throw "Failed to clear cached site cookies."
}

if ($ClearWebEngine -and (Test-Path $webEngineRoot)) {
    $sitesToClear = @()
    if ($Site) {
        $sitesToClear = $Site
    }
    else {
        $sitesToClear = Get-ChildItem $webEngineRoot -Directory |
            ForEach-Object { $_.Name -replace '-cache$', '' } |
            Select-Object -Unique
    }

    foreach ($siteName in $sitesToClear) {
        foreach ($target in @(
            (Join-Path $webEngineRoot $siteName),
            (Join-Path $webEngineRoot "$siteName-cache")
        )) {
            if (Test-Path $target) {
                Remove-Item $target -Recurse -Force -ErrorAction Stop
                Write-Host "webengine_cleared=$target"
            }
        }
    }
}
