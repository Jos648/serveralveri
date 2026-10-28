# IdlePool -- test ucun ikinci (satici) hesabi yaradir ve agent emrini cap edir.
# Esas proqram (alici hesabinla) artiq acilmis olmalidir, cunki server ondan ise dusur.

$seller = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/register" -Method Post `
    -Body (@{ name = "Satici" } | ConvertTo-Json) -ContentType "application/json"

Write-Host "Satici hesabi yaradildi: $($seller.name), acar: $($seller.key)"
Write-Host ""
Write-Host "Indi bu emri AYRI bir PowerShell pencersinde ise sal:"
Write-Host ""
Write-Host "python idlepool_app.py agent --key $($seller.key) --ram-gb 2 --disk-gb 10 --cores 1 --price 3"
