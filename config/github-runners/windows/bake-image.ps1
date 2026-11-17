# Bakes the golden Windows runner image on the template VM: Git for Windows, the Actions runner and the start task.
# Run through Azure Run Command by scripts/bake_azure_windows_image.sh, which prepends these variables:
#   $RunnerVersion   the pinned runner release (for example 2.338.2)
#   $StartScriptB64  config/github-runners/windows/start-runner.ps1, base64
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$headers = @{ 'User-Agent' = 'runner-image-bake' }

function Get-Verified($Url, $OutFile, $Digest) {
  Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
  $actual = 'sha256:' + (Get-FileHash -Path $OutFile -Algorithm SHA256).Hash.ToLower()
  if (-not $Digest) { throw "no published digest for $Url; refusing to install an unverified file" }
  if ($actual -ne $Digest.ToLower()) { throw "digest mismatch for $Url (got $actual, expected $Digest)" }
  "verified $(Split-Path $OutFile -Leaf) $actual"
}

# Git for Windows (actions/checkout prefers a real git).
$git = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers $headers
$gitAsset = $git.assets | Where-Object { $_.name -match '^Git-[0-9.]+-64-bit\.exe$' } | Select-Object -First 1
if (-not $gitAsset) { throw 'no 64-bit Git for Windows installer in the latest release' }
$gitExe = Join-Path $env:TEMP $gitAsset.name
Get-Verified $gitAsset.browser_download_url $gitExe $gitAsset.digest
Start-Process -FilePath $gitExe -ArgumentList '/VERYSILENT', '/NORESTART', '/SP-', '/SUPPRESSMSGBOXES' -Wait
Remove-Item $gitExe -Force
# `shell: bash` in a workflow needs bash on the PATH; Git for Windows only adds its cmd directory by default.
$gitRoot = 'C:\Program Files\Git'
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
foreach ($dir in @("$gitRoot\bin", "$gitRoot\usr\bin")) {
  if (($machinePath -split ';') -notcontains $dir) { $machinePath = "$machinePath;$dir" }
}
[Environment]::SetEnvironmentVariable('Path', $machinePath, 'Machine')
$env:Path = $machinePath
"git=" + (& git --version)
$bashVersion = (& bash --version | Select-Object -First 1)
if (-not $bashVersion) { throw 'bash is not runnable after the Git for Windows install' }
"bash=$bashVersion"

# The Actions runner.
$rel = Invoke-RestMethod -Uri "https://api.github.com/repos/actions/runner/releases/tags/v$RunnerVersion" -Headers $headers
$asset = $rel.assets | Where-Object { $_.name -eq "actions-runner-win-x64-$RunnerVersion.zip" } | Select-Object -First 1
if (-not $asset) { throw "runner $RunnerVersion has no Windows x64 package" }
$zip = Join-Path $env:TEMP $asset.name
Get-Verified $asset.browser_download_url $zip $asset.digest
if (Test-Path 'C:\actions-runner') { Remove-Item 'C:\actions-runner' -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath 'C:\actions-runner' -Force
Remove-Item $zip -Force
if (-not (Test-Path 'C:\actions-runner\run.cmd')) { throw 'run.cmd is missing after extraction' }

# The start task: SYSTEM, at boot. The VM is single use and has no inbound path.
New-Item -ItemType Directory -Path 'C:\ghrunner' -Force | Out-Null
[IO.File]::WriteAllBytes('C:\ghrunner\start-runner.ps1', [Convert]::FromBase64String($StartScriptB64))
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ghrunner\start-runner.ps1'
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'GhRunnerStart' -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
"task=" + (Get-ScheduledTask -TaskName 'GhRunnerStart').State

# Leave nothing of this bake behind.
Remove-Item -Path 'C:\ghrunner\start.log' -Force -ErrorAction SilentlyContinue
Get-ChildItem $env:TEMP -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
"baked runner=$RunnerVersion"
