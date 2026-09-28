param(
    [switch]$Reconfigure,
    [switch]$SkipFirewall,
    [switch]$SetupOnly,
    [switch]$DebugLog
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

try {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $requirements = Join-Path $PSScriptRoot 'requirements.txt'
    $stamp = Join-Path $PSScriptRoot '.venv\requirements.sha256'

    if (-not (Test-Path -LiteralPath $python)) {
        $launcher = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { throw 'Python 3.11+ is required. Install Python, then run this command again.' }
        Write-Host 'Creating local Python environment...'
        if ($launcher -eq 'py') { & py -3 -m venv .venv } else { & python -m venv .venv }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }

    $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $installed = (Test-Path -LiteralPath $stamp) -and ((Get-Content -LiteralPath $stamp -Raw).Trim() -eq $hash)
    if (-not $installed) {
        Write-Host 'Installing DeskMesh dependencies...'
        & $python -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check internet access and retry.' }
        Set-Content -LiteralPath $stamp -Value $hash -Encoding Ascii
    }

    $setupArgs = @('deskmesh.py', 'setup')
    if ($Reconfigure) { $setupArgs += '--reset' }
    & $python @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'DeskMesh setup was not completed.' }

    $config = Get-Content -LiteralPath 'deskmesh.json' -Raw | ConvertFrom-Json
    if ($config.role -eq 'primary' -and -not $SkipFirewall) {
        $controlPort = if ($config.control_port) { [int]$config.control_port } else { 47660 }
        $audioPort = if ($config.audio_port) { [int]$config.audio_port } else { 47661 }
        $discoveryPort = if ($config.discovery_port) { [int]$config.discovery_port } else { 47662 }
        $controlRule = "DeskMesh-Control-$controlPort"
        $audioRule = "DeskMesh-Audio-$audioPort"
        $discoveryRule = "DeskMesh-Discovery-$discoveryPort"
        $needsRule = (-not (Get-NetFirewallRule -Name $controlRule -ErrorAction SilentlyContinue)) -or (-not (Get-NetFirewallRule -Name $audioRule -ErrorAction SilentlyContinue)) -or (-not (Get-NetFirewallRule -Name $discoveryRule -ErrorAction SilentlyContinue))
        if ($needsRule) {
            Write-Host 'Allowing DeskMesh on Private local networks. Windows may ask for administrator approval...'
            $firewallScript = Join-Path $PSScriptRoot 'configure-firewall.ps1'
            $quotedPath = $firewallScript.Replace("'", "''")
            $command = "& '$quotedPath' -ControlPort $controlPort -AudioPort $audioPort -DiscoveryPort $discoveryPort"
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
            $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
                & $firewallScript -ControlPort $controlPort -AudioPort $audioPort -DiscoveryPort $discoveryPort
                if ($LASTEXITCODE -ne 0) { throw 'Firewall setup failed.' }
            } else {
                $process = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded" -PassThru -Wait
                if ($process.ExitCode -ne 0) { throw 'Firewall setup was not approved or failed. Run the same command again.' }
            }
            Write-Host 'Private-network firewall rules are ready.'
        }
        $privateNetworks = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Private' -and $_.IPv4Connectivity -ne 'Disconnected' })
        if ($privateNetworks.Count -eq 0) {
            Write-Warning 'No active Private IPv4 network was detected. DeskMesh firewall rules will not work on a Public network; change only a network you trust to Private.'
        }
    }

    if ($SetupOnly) { exit 0 }
    Write-Host 'Starting DeskMesh. Press Ctrl+C to stop.'
    $runArgs = @('deskmesh.py')
    if ($DebugLog) { $runArgs += '--debug' }
    $runArgs += 'start'
    & $python @runArgs
    exit $LASTEXITCODE
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
