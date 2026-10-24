[CmdletBinding()]
param(
    [ValidatePattern('^[a-zA-Z0-9_.\\,-]+$')][string]$Server='msk-db',
    [ValidatePattern('^[\p{L}\p{N}_-]+$')][string]$Database='zup',
    [string]$Root='',
    [switch]$TrustServerCertificate,
    [switch]$RunHere,
    [ValidatePattern('^[a-f0-9]{32}$')][string]$RunId=([guid]::NewGuid().ToString('N')),
    [ValidateRange(1,10000)][int]$SampleLimit=5000
)
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($Root)){$Root=Join-Path $PSScriptRoot '..'}
$Root=(Resolve-Path -LiteralPath $Root).Path
$directory=Join-Path $Root ('.runtime\1c-diagnostics\'+$RunId)
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$statusPath=Join-Path $directory 'status.json'
$reportPath=Join-Path $directory 'report.json'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
function Write-Json($Path,$Value){
    $temporary=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Quote-Identifier([string]$value){return '['+$value.Replace(']',']]')+']'}
function Query($connection,[string]$sql,$parameters=@{}){
    $command=$connection.CreateCommand();$command.CommandText=$sql;$command.CommandTimeout=15
    foreach($name in $parameters.Keys){[void]$command.Parameters.AddWithValue($name,$parameters[$name])}
    $reader=$null
    try {
        $reader=$command.ExecuteReader();$result=New-Object 'System.Collections.Generic.List[object]'
        while($reader.Read()){
            $row=[ordered]@{}
            for($i=0;$i -lt $reader.FieldCount;$i++){$row[$reader.GetName($i)]=if($reader.IsDBNull($i)){$null}else{$reader.GetValue($i)}}
            $result.Add([pscustomobject]$row)
        }
        return $result.ToArray()
    } finally {if($reader){$reader.Dispose()};$command.Dispose()}
}
try {
    if(-not $RunHere){
        $portal=Get-ScheduledTask -TaskName 'YDS Portal'
        $principal=$portal.Principal.UserId
        $principalSid=try{(New-Object Security.Principal.NTAccount($principal)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{$principal}
        if($principalSid -ne $identity.User.Value){
            if($principalSid -ne 'S-1-5-18' -and $principal -notin @('SYSTEM','NT AUTHORITY\SYSTEM')){
                throw "Run this script as the portal account: $principal. Its credentials are not requested or stored by this diagnostic."
            }
            $admin=New-Object Security.Principal.WindowsPrincipal($identity)
            if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run PowerShell as Administrator to diagnose using the portal SYSTEM account.'}
            $taskName='YDS Portal 1C Diagnostics '+$RunId
            $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $Root 'ops\diagnose-1c.ps1')+'" -Root "'+$Root+'" -Server "'+$Server+'" -Database "'+$Database+'" -RunId '+$RunId+' -SampleLimit '+$SampleLimit+' -RunHere'
            if($TrustServerCertificate){$arguments+=' -TrustServerCertificate'}
            $action=New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument $arguments -WorkingDirectory $Root
            $system=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
            $registered=$false
            try {
                Register-ScheduledTask -TaskName $taskName -Action $action -Principal $system -Settings $settings | Out-Null
                $registered=$true;Start-ScheduledTask -TaskName $taskName
                Write-Host 'Diagnosing MSSQL using the portal account. Portal service remains running.' -ForegroundColor Cyan
                $finished=$false
                for($attempt=0;$attempt -lt 420;$attempt++){
                    if(Test-Path -LiteralPath $statusPath){$finished=$true;break}
                    if($attempt -gt 3){
                        $info=Get-ScheduledTaskInfo -TaskName $taskName
                        if($info.LastRunTime.Year -gt 2000 -and (Get-ScheduledTask -TaskName $taskName).State -ne 'Running' -and $info.LastTaskResult -ne 0){throw ('Diagnostic task failed: 0x{0:X}.' -f $info.LastTaskResult)}
                    }
                    if($attempt % 15 -eq 0){Write-Host ('Waiting: '+($attempt*2)+' seconds')}
                    Start-Sleep -Seconds 2
                }
                if(-not $finished){throw 'Diagnostic timed out. Check the diagnostic task and protected report directory.'}
                $result=Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
                if(-not $result.ok){Write-Host ('Portal diagnostic account: '+$result.windows_identity);throw $result.error}
                Write-Host ('DONE. Report: '+$reportPath) -ForegroundColor Green
                $report=Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
                Write-Host ('SQL identity: '+$report.connection.login)
                $report.candidates | Select-Object -Property @('schema','table','matched_rows','sampled_rows') | Format-Table -AutoSize
                exit 0
            } finally {
                if($registered){
                    if((Get-ScheduledTask -TaskName $taskName).State -eq 'Running'){Stop-ScheduledTask -TaskName $taskName}
                    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
                }
            }
        }
    }
    Add-Type -AssemblyName System.Data
    $builder=New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    # PowerShell adapts dictionary members as keys; use SqlClient keyword names.
    $builder['Data Source']=$Server;$builder['Initial Catalog']=$Database
    $builder['Integrated Security']=$true;$builder['Encrypt']=$true
    $builder['TrustServerCertificate']=[bool]$TrustServerCertificate
    $builder['Connect Timeout']=15;$builder['Application Name']='YDS Portal 1C read-only diagnostics'
    $builder['ApplicationIntent']='ReadOnly'
    $connection=New-Object System.Data.SqlClient.SqlConnection($builder.ConnectionString)
    try {
        $connection.Open()
        # All diagnostic statements are SELECTs. Account permissions must also be read-only.
        $who=@(Query $connection 'SELECT @@SERVERNAME AS server, DB_NAME() AS [database], SUSER_SNAME() AS login, ORIGINAL_LOGIN() AS original_login')[0]
        $columns=@(Query $connection @'
SELECT s.name AS [schema],t.name AS [table],c.name AS [column],ty.name AS [type],c.max_length,c.is_nullable
FROM sys.tables AS t
JOIN sys.schemas AS s ON s.schema_id=t.schema_id
JOIN sys.columns AS c ON c.object_id=t.object_id
JOIN sys.types AS ty ON ty.user_type_id=c.user_type_id
ORDER BY s.name,t.name,c.column_id
'@)
        $descriptions=@($columns | Where-Object { $_.table -match '^_Reference\d+$' -and $_.column -eq '_Description' -and $_.type -in @('nvarchar','varchar','nchar','char') })
        $terms=@('должност','машинист','водител','инженер','монтажник','электромонтер','электромонтёр','сварщик','директор','начальник','бухгалтер','мастер','руководител','слесар')
        $candidates=New-Object 'System.Collections.Generic.List[object]'
        $errors=New-Object 'System.Collections.Generic.List[object]'
        foreach($table in $descriptions){
            $name=(Quote-Identifier $table.schema)+'.'+(Quote-Identifier $table.table)
            $parameters=@{'@limit'=$SampleLimit};$clauses=@()
            for($i=0;$i -lt $terms.Count;$i++){$parameter='@term'+$i;$parameters[$parameter]='%'+$terms[$i]+'%';$clauses+='LOWER([_Description]) LIKE '+$parameter}
            $sql='SELECT COUNT(*) AS sampled_rows, COALESCE(SUM(CASE WHEN '+($clauses -join ' OR ')+' THEN 1 ELSE 0 END),0) AS matched_rows FROM (SELECT TOP (@limit) [_Description] FROM '+$name+') AS sample'
            try {
                $counts=@(Query $connection $sql $parameters)[0]
                if($counts.matched_rows -gt 0){
                    $fields=@($columns | Where-Object {$_.schema -eq $table.schema -and $_.table -eq $table.table})
                    $candidates.Add([pscustomobject]@{schema=$table.schema;table=$table.table;sampled_rows=$counts.sampled_rows;matched_rows=$counts.matched_rows;columns=$fields})
                }
            } catch {$errors.Add([pscustomobject]@{schema=$table.schema;table=$table.table;error=$_.Exception.Message})}
        }
        $metadata=@($columns | Where-Object {$_.table -in @('Config','ConfigSave','Params','DBSchema','IBVersion','Files')} | Select-Object -Property @('schema','table') -Unique)
        $report=[ordered]@{created_utc=[DateTime]::UtcNow.ToString('o');windows_identity=$identity.Name;connection=$who;reference_tables_checked=$descriptions.Count;sample_limit=$SampleLimit;search_terms=$terms;candidates=@($candidates | Sort-Object matched_rows -Descending);metadata_tables=$metadata;schema=$columns;scan_errors=$errors.ToArray();notice='Candidates are hints, not a confirmed 1C metadata mapping. No source descriptions, employee rows, binary metadata, or passwords are exported.'}
        Write-Json $reportPath $report
        Write-Json $statusPath @{ok=$true;report=$reportPath}
        Write-Host ('DONE. Report: '+$reportPath) -ForegroundColor Green
        Write-Host ('SQL identity: '+$who.login)
        $report.candidates | Select-Object -Property @('schema','table','matched_rows','sampled_rows') | Format-Table -AutoSize
    } finally {if($connection){$connection.Dispose()}}
} catch {
    if(-not (Test-Path -LiteralPath $statusPath)){Write-Json $statusPath @{ok=$false;windows_identity=$identity.Name;error=$_.Exception.Message}}
    Write-Host ('DIAGNOSTIC FAILED: '+$_.Exception.Message) -ForegroundColor Red
    Write-Host ('Account: '+$identity.Name)
    exit 1
}
