[CmdletBinding()]
param(
    [ValidatePattern('^[a-zA-Z0-9_.\\,-]+$')][string]$Server='msk-db',
    [ValidatePattern('^[\p{L}\p{N}_-]+$')][string]$Database='zup',
    [string]$Root='',
    [string]$PythonPath='',
    [switch]$TrustServerCertificate,
    [switch]$RunHere,
    [switch]$MetadataOnly,
    [switch]$PositionsImport,
    [switch]$EmployeesImport,
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
    ConvertTo-Json -InputObject $Value -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
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
function Save-MetadataFile($connection,$table,[string]$file,[string]$destination,[bool]$parts){
    $name=(Quote-Identifier $table.schema)+'.'+(Quote-Identifier $table.table)
    $sql='SELECT TOP (128) [BinaryData] AS data FROM '+$name+' WHERE [FileName]=@file AND DATALENGTH([BinaryData])<=8388608'
    if($parts){$sql+=' ORDER BY [PartNo]'}
    $rows=@(Query $connection $sql @{'@file'=$file})
    if($rows.Count -eq 0){return $false}
    $memory=New-Object IO.MemoryStream
    try {
        foreach($row in $rows){
            if($null -eq $row.data){continue}
            if($memory.Length+$row.data.Length -gt 8388608){throw 'Metadata file exceeds 8 MB.'}
            $memory.Write($row.data,0,$row.data.Length)
        }
        if($memory.Length -eq 0){return $false}
        [IO.File]::WriteAllBytes($destination,$memory.ToArray());return $true
    } finally {$memory.Dispose()}
}
function Invoke-MetadataReader([string]$mode,[string]$metadataDirectory,[string]$python,[string]$parser){
    $errorFile=Join-Path $metadataDirectory 'reader-error.json'
    if(Test-Path -LiteralPath $errorFile){Remove-Item -LiteralPath $errorFile -Force}
    # Redirect at process level: Windows PowerShell otherwise stops at the first stderr line.
    $arguments='-X utf8 "'+$parser+'" '+$mode+' "'+$metadataDirectory+'"'
    $process=Start-Process -FilePath $python -ArgumentList $arguments -Wait -PassThru -NoNewWindow -RedirectStandardError (Join-Path $metadataDirectory 'parser-errors.log') -RedirectStandardOutput (Join-Path $metadataDirectory 'parser-output.log')
    if($process.ExitCode -ne 0){
        if(Test-Path -LiteralPath $errorFile){
            $detail=Get-Content -LiteralPath $errorFile -Raw -Encoding UTF8 | ConvertFrom-Json
            throw ('Metadata '+$detail.stage+': '+$detail.error_type+': '+$detail.error+'; DBNames bytes: '+$detail.dbnames_bytes)
        }
        $log=Get-Content -LiteralPath (Join-Path $metadataDirectory 'parser-errors.log') -Raw -Encoding UTF8
        if($log.Length -gt 8000){$log=$log.Substring($log.Length-8000)}
        throw ('Metadata reader exit '+$process.ExitCode+': '+$log)
    }
}
function Read-MetadataNames($connection,$columns){
    $metadataDirectory=Join-Path $script:directory 'metadata'
    New-Item -ItemType Directory -Path $metadataDirectory -Force | Out-Null
    $params=@($columns | Where-Object {$_.table -eq 'Params' -and $_.column -eq 'BinaryData'})
    $configs=@($columns | Where-Object {$_.table -eq 'Config' -and $_.column -eq 'BinaryData'})
    if($params.Count -ne 1 -or $configs.Count -ne 1){throw 'Params/Config metadata tables are unavailable or ambiguous.'}
    foreach($table in @($params[0],$configs[0])){
        if(-not @($columns | Where-Object {$_.schema -eq $table.schema -and $_.table -eq $table.table -and $_.column -eq 'FileName'}).Count){throw 'Metadata FileName column is unavailable.'}
    }
    $parts=@($columns | Where-Object {$_.schema -eq $configs[0].schema -and $_.table -eq 'Config' -and $_.column -eq 'PartNo'}).Count -gt 0
    if(-not (Save-MetadataFile $connection $params[0] 'DBNames' (Join-Path $metadataDirectory 'dbnames.bin') $false)){throw 'Params.DBNames is not readable.'}
    if(-not $PythonPath){$command=Get-Command python -ErrorAction SilentlyContinue;if($command){$PythonPath=$command.Source}}
    if(-not $PythonPath){throw 'Python executable for the metadata reader is unavailable.'}
    $parser=Join-Path $PSScriptRoot 'parse_1c_metadata.py'
    Invoke-MetadataReader 'index' $metadataDirectory $PythonPath $parser
    # A named root object avoids Windows PowerShell 5.1 wrapping a JSON array as one item.
    $manifest=ConvertFrom-Json -InputObject (Get-Content -LiteralPath (Join-Path $metadataDirectory 'requests.json') -Raw -Encoding UTF8)
    $read=0;$totalBytes=0;$metadataWarnings=@();$timer=[Diagnostics.Stopwatch]::StartNew()
    foreach($entry in $manifest.objects){
        if($timer.Elapsed.TotalSeconds -gt 180){$metadataWarnings+=[pscustomobject]@{error='Metadata read time limit reached; names are partial.'};break}
        if($entry -is [System.Array] -or $entry.uuid -isnot [string]){throw 'Metadata manifest entry must contain one UUID, not an array.'}
        $uuid=$entry.uuid
        if($uuid -notmatch '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'){throw 'Invalid metadata UUID.'}
        $metadataPath=Join-Path -Path $metadataDirectory -ChildPath ($uuid+'.bin')
        if(Save-MetadataFile $connection $configs[0] $uuid $metadataPath $parts){$read++;$totalBytes+=(Get-Item -LiteralPath $metadataPath).Length;if($totalBytes -gt 134217728){$metadataWarnings+=[pscustomobject]@{error='Metadata snapshot limit reached; names are partial.'};break}}
    }
    Write-Json (Join-Path $metadataDirectory 'schema.json') $columns
    Invoke-MetadataReader 'decode' $metadataDirectory $PythonPath $parser
    $result=Get-Content -LiteralPath (Join-Path $metadataDirectory 'names.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($read -eq 0){$metadataWarnings+=[pscustomobject]@{error='No object files found in Config by DBNames UUID. This metadata layout needs an additional reader.'}}
    $result.errors=@($result.errors)+$metadataWarnings
    return $result
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
            if($MetadataOnly){$arguments+=' -MetadataOnly'}
            if($PositionsImport){$arguments+=' -PositionsImport'}
            if($EmployeesImport){$arguments+=' -EmployeesImport'}
            if($PythonPath){$arguments+=' -PythonPath "'+$PythonPath+'"'}
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
        if($EmployeesImport){
            $employees=@(Query $connection @'
SELECT TOP (200001) CONVERT(varchar(32), e.[_IDRRef], 2) AS source_id,
 e.[_Code] AS employee_number,
 COALESCE(NULLIF(LTRIM(RTRIM(p.[_Fld31265])),N''),NULLIF(LTRIM(RTRIM(p.[_Description])),N''),NULLIF(LTRIM(RTRIM(e.[_Description])),N''),N'') AS full_name,
 p.[_Fld31276] AS last_name, p.[_Fld31277] AS first_name, p.[_Fld31278] AS middle_name,
 CASE WHEN e.[_Fld30280]=0x01 THEN 1 ELSE 0 END AS archived,
 COALESCE(o.[_Description],h.[_Description],N'') AS organisation,
 COALESCE(d.[_Description],N'') AS department,
 COALESCE(j.[_Description],N'') AS position
FROM [dbo].[_Reference297] e
LEFT JOIN [dbo].[_Reference383] p ON p.[_IDRRef]=e.[_Fld30276RRef]
LEFT JOIN [dbo].[_InfoRg21744] k ON k.[_Fld21746RRef]=e.[_IDRRef]
LEFT JOIN [dbo].[_Reference166] o ON o.[_IDRRef]=k.[_Fld21748RRef]
LEFT JOIN [dbo].[_Reference166] h ON h.[_IDRRef]=e.[_Fld30277RRef]
LEFT JOIN [dbo].[_Reference216] d ON d.[_IDRRef]=k.[_Fld21749RRef]
LEFT JOIN [dbo].[_Reference92] j ON j.[_IDRRef]=k.[_Fld21750RRef]
WHERE e.[_Marked]=0x00
'@)
            Write-Json $reportPath @{employees_import=$true;employees=@($employees)}
            Write-Json $statusPath @{ok=$true;report=$reportPath}
            return
        }
        if($PositionsImport){
            $summary=@(Query $connection @'
SELECT COUNT_BIG(*) AS total,
 COALESCE(SUM(CAST(CASE WHEN [_Marked]=0x00 THEN 1 ELSE 0 END AS bigint)),0) AS not_marked,
 COALESCE(SUM(CAST(CASE WHEN [_Marked]=0x00 AND [_Fld25711]=0x01 THEN 1 ELSE 0 END AS bigint)),0) AS excluded,
 COALESCE(SUM(CAST(CASE WHEN [_Marked]=0x00 AND [_Fld25711]=0x00 THEN 1 ELSE 0 END AS bigint)),0) AS eligible
FROM [dbo].[_Reference92]
'@)[0]
            $flags=@(Query $connection 'SELECT TOP (32) CONVERT(varchar(20),[_Marked],1) AS marked, CONVERT(varchar(20),[_Fld25711],1) AS excluded, COUNT_BIG(*) AS records FROM [dbo].[_Reference92] GROUP BY [_Marked],[_Fld25711] ORDER BY COUNT_BIG(*) DESC')
            $names=@(Query $connection 'SELECT [_Description] AS name FROM [dbo].[_Reference92] WHERE [_Marked]=0x00 AND [_Fld25711]=0x00')
            Write-Json $reportPath @{positions_import=$true;position_names=@($names);position_source=$summary;position_flags=@($flags)}
            Write-Json $statusPath @{ok=$true;report=$reportPath}
            return
        }
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
        $scanTables=if($MetadataOnly){@()}else{$descriptions}
        foreach($table in $scanTables){
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
        $metadataNames=@{};$metadataStatus='unavailable';$metadataErrors=@();$metadataRead=0
        try {
            $decoded=Read-MetadataNames $connection $columns
            $metadataNames=$decoded.labels;$metadataRead=$decoded.objects_read;$metadataErrors=@($decoded.errors)
            $metadataStatus=if(@($metadataNames.PSObject.Properties).Count -gt 0){'decoded'}else{'unrecognized'}
        } catch {$metadataErrors=@([pscustomobject]@{error=$_.Exception.Message})}
        $report=[ordered]@{metadata_labels=$metadataNames;metadata_status=$metadataStatus;metadata_objects_read=$metadataRead;metadata_errors=$metadataErrors;created_utc=[DateTime]::UtcNow.ToString('o');windows_identity=$identity.Name;connection=$who;reference_tables_checked=@($scanTables).Count;sample_limit=$SampleLimit;search_terms=$terms;candidates=@($candidates | Sort-Object matched_rows -Descending);metadata_tables=$metadata;schema=$columns;scan_errors=$errors.ToArray();notice='Candidates are hints, not a confirmed 1C metadata mapping. No source descriptions, employee rows, binary metadata, or passwords are exported.'}
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
