# Backfill every comment for every post, in chunks that stay out of the hourly run's way.
# Each chunk takes the run lock, pulls up to 150 posts, and releases it.
# Chunks start only between :05 and :40 past the hour, so the :00 hourly run is never skipped.
# Stops when no post has fewer comments stored than Skool reports, or when Skool refuses.
Set-Location C:\Projects\brotherhood-desk
$env:PYTHONIOENCODING = 'utf-8'
$log = "logs\backfill_comments.log"
for ($i = 1; $i -le 60; $i++) {
    while ($true) {
        $min = (Get-Date).Minute
        if ($min -ge 5 -and $min -le 40 -and -not (Test-Path logs\running.lock)) { break }
        Start-Sleep -Seconds 60
    }
    Get-Date | Out-File logs\running.lock
    Add-Content $log "===== chunk $i $(Get-Date) ====="
    $out = python collector\collect.py --fullcomments --max-detail 150 2>&1 | Out-String
    Remove-Item logs\running.lock -ErrorAction SilentlyContinue
    Add-Content $log $out
    if ($out -match '(?m)^0 posts have fewer') { Add-Content $log "done: every post is complete"; break }
    if ($out -match 'keeps refusing|NOT LOGGED IN') { Add-Content $log "stopped: Skool refused or login expired"; break }
}
Add-Content $log "===== backfill finished $(Get-Date) ====="
