param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$ControlPort,
    [Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$AudioPort,
    [Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$DiscoveryPort
)

$ErrorActionPreference = 'Stop'

$rules = @(
    @{ Name = "DeskMesh-Control-$ControlPort"; Label = 'DeskMesh control'; Protocol = 'TCP'; Port = $ControlPort },
    @{ Name = "DeskMesh-Audio-$AudioPort"; Label = 'DeskMesh audio'; Protocol = 'UDP'; Port = $AudioPort },
    @{ Name = "DeskMesh-Discovery-$DiscoveryPort"; Label = 'DeskMesh discovery'; Protocol = 'UDP'; Port = $DiscoveryPort }
)

foreach ($rule in $rules) {
    $existing = Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Set-NetFirewallRule -Name $rule.Name -Enabled True -Direction Inbound -Action Allow -Profile Private -RemoteAddress LocalSubnet | Out-Null
        $existing | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol $rule.Protocol -LocalPort $rule.Port | Out-Null
    } else {
        New-NetFirewallRule -Name $rule.Name -DisplayName $rule.Label -Description 'DeskMesh trusted-LAN access only' -Direction Inbound -Action Allow -Enabled True -Protocol $rule.Protocol -LocalPort $rule.Port -Profile Private -RemoteAddress LocalSubnet | Out-Null
    }
}
