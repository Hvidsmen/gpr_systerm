param([string]$Ports)
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($Ports)){throw 'Specify comma-separated training ports, for example -Ports "8101,8102".'}
$selectedPorts=@($Ports.Split(',') | ForEach-Object {[int]$_.Trim()})
if(@($selectedPorts | Where-Object {$_ -lt 8101 -or $_ -gt 8199}).Count){throw 'Specify the configured training ports between 8101 and 8199.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run PowerShell as Administrator.'}
foreach($port in ($selectedPorts | Sort-Object -Unique)){
    $name="YDS-Portal-Training-$port"
    if(-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)){
        New-NetFirewallRule -Name $name -DisplayName "YDS training portal $port" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress LocalSubnet -Profile Domain,Private | Out-Null
    }
}
Write-Host 'Configured training ports are available to the local network on Domain/Private profiles.'
