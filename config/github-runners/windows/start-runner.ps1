# Runs once at boot, as SYSTEM, on a broker-created VM (scheduled task GhRunnerStart, baked into the image).
# The broker passes the just-in-time runner configuration as the VM's custom data; Azure writes it to
# C:\AzureData\CustomData.bin. The configuration is single use and expires unused. The runner takes one job,
# de-registers itself and exits; the broker then deletes the VM. Nothing here is a credential of the operator.
$ErrorActionPreference = 'Stop'
$log = 'C:\ghrunner\start.log'
function Log($message) { "$(Get-Date -Format o) $message" | Add-Content -Path $log }

$customData = 'C:\AzureData\CustomData.bin'
$deadline = (Get-Date).AddMinutes(10)
while (-not (Test-Path $customData) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
if (-not (Test-Path $customData)) { Log 'no custom data arrived; nothing to run'; exit 1 }

$jit = (Get-Content -Path $customData -Raw).Trim()
Remove-Item -Path $customData -Force   # the on-disk copy is no longer needed
if ($jit.Length -lt 100) { Log 'custom data is not a runner configuration'; exit 1 }

Set-Location 'C:\actions-runner'
Log 'starting the runner'
& .\run.cmd --jitconfig $jit
Log "runner exited with $LASTEXITCODE"
exit $LASTEXITCODE
