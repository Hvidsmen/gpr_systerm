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
function Query($connection,[string]$sql,$parameters=@{},[int]$timeout=15){
    $command=$connection.CreateCommand();$command.CommandText=$sql;$command.CommandTimeout=$timeout
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
function Read-MetadataNames($connection,$columns,$GenderOnly=$false){
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
    if($GenderOnly){Invoke-MetadataReader 'gender-index' $metadataDirectory $PythonPath $parser}else{Invoke-MetadataReader 'index' $metadataDirectory $PythonPath $parser}
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
    if($GenderOnly){Invoke-MetadataReader 'gender' $metadataDirectory $PythonPath $parser}else{Invoke-MetadataReader 'decode' $metadataDirectory $PythonPath $parser}
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
            $offsetRows=@(Query $connection 'SELECT DISTINCT [Offset] AS date_offset FROM [dbo].[_YearOffset]')
            if($offsetRows.Count -ne 1 -or [int]$offsetRows[0].date_offset -notin @(0,2000)){throw 'Unsupported or ambiguous 1C date offset.'}
            $dateOffset=[int]$offsetRows[0].date_offset
            $storedNow=[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Russian Standard Time').AddYears($dateOffset)
            $positionCatalogue=@(Query $connection "SELECT COUNT_BIG(*) AS position_rows,COALESCE(SUM(CASE WHEN NULLIF(LTRIM(RTRIM([_Description])),N'') IS NOT NULL THEN 1 ELSE 0 END),0) AS position_named FROM [dbo].[_Reference92]")
            $employees=@(Query $connection @'
SELECT TOP (200001) CONVERT(varchar(32), e.[_IDRRef], 2) AS source_id,
 e.[_Code] AS employee_number,
 COALESCE(NULLIF(LTRIM(RTRIM(p.[_Fld31265])),N''),NULLIF(LTRIM(RTRIM(p.[_Description])),N''),NULLIF(LTRIM(RTRIM(e.[_Description])),N''),N'') AS full_name,
 p.[_Fld31276] AS last_name, p.[_Fld31277] AS first_name, p.[_Fld31278] AS middle_name,
 CONVERT(varchar(32),p.[_Fld31258RRef],2) AS gender_ref,
 CASE WHEN e.[_Fld30280]=0x01 THEN 1 ELSE 0 END AS archived,
 CASE WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N''),NULLIF(jo.[_Description],N'')) IS NULL AND sf.staffing_position IS NOT NULL THEN COALESCE(NULLIF(sf.organisation,N''),ho.[_Description],N'') WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N'')) IS NULL AND NULLIF(jo.[_Description],N'') IS NOT NULL THEN COALESCE(NULLIF(oo.[_Description],N''),ho.[_Description],N'') WHEN NULLIF(j.[_Description],N'') IS NULL AND COALESCE(NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N'')) IS NOT NULL THEN COALESCE(NULLIF(op.[_Description],N''),NULLIF(o.[_Description],N''),ho.[_Description],N'') ELSE COALESCE(NULLIF(o.[_Description],N''),NULLIF(oi.[_Description],N''),NULLIF(oh.[_Description],N''),ho.[_Description],N'') END AS organisation,
 CASE WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N''),NULLIF(jo.[_Description],N'')) IS NULL AND sf.staffing_position IS NOT NULL THEN COALESCE(sf.department,N'') WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N'')) IS NULL AND NULLIF(jo.[_Description],N'') IS NOT NULL THEN COALESCE(doo.[_Description],N'') WHEN NULLIF(j.[_Description],N'') IS NULL AND COALESCE(NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N'')) IS NOT NULL THEN COALESCE(NULLIF(dp.[_Description],N''),dps.[_Description],N'') ELSE COALESCE(NULLIF(d.[_Description],N''),NULLIF(di.[_Description],N''),NULLIF(dh.[_Description],N''),ds.[_Description],N'') END AS department,
 COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N''),jo.[_Description],N'') AS position,
 CASE WHEN NULLIF(j.[_Description],N'') IS NOT NULL THEN N'Текущие кадровые данные'
      WHEN COALESCE(NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N'')) IS NOT NULL THEN N'Данные для подбора сотрудников'
      WHEN NULLIF(ji.[_Description],N'') IS NOT NULL THEN N'Кадровая история (интервалы)'
      WHEN NULLIF(jh.[_Description],N'') IS NOT NULL THEN N'Кадровая история'
      WHEN NULLIF(js.[_Description],N'') IS NOT NULL THEN N'Штатное расписание'
      WHEN NULLIF(jo.[_Description],N'') IS NOT NULL THEN N'Занятость позиций штатного расписания' ELSE N'' END AS position_source,
 CASE WHEN NULLIF(j.[_Description],N'') IS NULL THEN
   CONVERT(char(10),DATEADD(year,-@dateOffset,CASE
     WHEN COALESCE(NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N'')) IS NOT NULL AND YEAR(pc.[_Fld16846])>@dateOffset+1 THEN pc.[_Fld16846]
     WHEN NULLIF(ji.[_Description],N'') IS NOT NULL THEN i.[_Fld18186]
     WHEN NULLIF(jh.[_Description],N'') IS NOT NULL THEN h.[_Period]
     WHEN NULLIF(js.[_Description],N'') IS NOT NULL THEN CASE WHEN NULLIF(i.[_Fld18199RRef],0x00000000000000000000000000000000) IS NOT NULL THEN i.[_Fld18186] ELSE h.[_Period] END
     WHEN NULLIF(jo.[_Description],N'') IS NOT NULL AND YEAR(occ.since)>@dateOffset+1 THEN occ.since END),23) END AS position_since,
 sf.staffing_position,sf.staffing_source,
 CASE WHEN YEAR(sf.since)-@dateOffset BETWEEN 1900 AND 2199 THEN CONVERT(char(10),DATEADD(year,-@dateOffset,sf.since),23) END AS staffing_since,
 CONVERT(varchar(32),COALESCE(sf.staffing_ref,so.[_IDRRef]),2) AS trace_staffing_ref,
 CONVERT(varchar(32),COALESCE(sf.position_ref,so.[_Fld31540RRef]),2) AS trace_position_ref,
 CONVERT(varchar(32),COALESCE(sf.department_ref,so.[_Fld31539RRef]),2) AS trace_department_ref,
 CASE WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N''),NULLIF(jo.[_Description],N'')) IS NOT NULL THEN 'job'
      WHEN sf.staffing_position IS NOT NULL THEN 'staffing'
      WHEN occ.candidates>1 OR pc.candidates>1 THEN 'ambiguous'
      WHEN so.[_IDRRef] IS NULL THEN 'no_staffing_assignment'
      WHEN so.[_Fld31540RRef]=0x00000000000000000000000000000000 THEN 'no_position_link'
      WHEN jo.[_IDRRef] IS NULL THEN 'missing_job_record'
      ELSE 'empty_job_name' END AS position_state,
 CASE WHEN YEAR(p.[_Fld31257])>@dateOffset+1 THEN CONVERT(char(10),DATEADD(year,-@dateOffset,p.[_Fld31257]),23) END AS birth_date,
 p.[_Fld31259] AS inn, p.[_Fld31260] AS snils, p.[_Fld31261] AS birth_place,
 CASE WHEN YEAR(k.[_Fld21754])>@dateOffset+1 THEN CONVERT(char(10),DATEADD(year,-@dateOffset,k.[_Fld21754]),23) END AS hire_date,
 CASE WHEN YEAR(k.[_Fld21751])>@dateOffset+1 THEN CONVERT(char(10),DATEADD(year,-@dateOffset,k.[_Fld21751]),23) END AS dismissal_date,
 CASE WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N''),NULLIF(jo.[_Description],N'')) IS NULL AND sf.staffing_position IS NOT NULL AND sf.fte>0 THEN sf.fte WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N'')) IS NULL AND NULLIF(jo.[_Description],N'') IS NOT NULL THEN occ.fte WHEN NULLIF(j.[_Description],N'') IS NOT NULL THEN k.[_Fld45771] WHEN COALESCE(NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N'')) IS NOT NULL THEN pc.[_Fld16842] ELSE COALESCE(i.[_Fld18200],h.[_Fld18164],NULLIF(k.[_Fld45771],0)) END AS fte,
 g.[_Description] AS schedule,
 CASE WHEN pay.candidates=1 THEN pay.tariff_rate END AS tariff_rate,
 CASE WHEN pay.candidates=1 THEN pay.combined_tariff_rate END AS combined_tariff_rate,
 CASE WHEN pay.candidates=1 THEN pay.payroll_fund END AS payroll_fund,
 CASE WHEN pay.candidates=1 THEN CONVERT(char(10),DATEADD(year,-@dateOffset,pay.since),23) END AS pay_since,
 CASE WHEN k.[_Fld21746RRef] IS NOT NULL THEN 1 ELSE 0 END AS probe_current_row,
 CASE WHEN k.[_Fld21750RRef]<>0x00000000000000000000000000000000 THEN 1 ELSE 0 END AS probe_current_position_ref,
 CASE WHEN NULLIF(j.[_Description],N'') IS NOT NULL THEN 1 ELSE 0 END AS probe_current_position_join,
 CASE WHEN NULLIF(ji.[_Description],N'') IS NOT NULL THEN 1 ELSE 0 END AS probe_interval_position_join,
 CASE WHEN NULLIF(jh.[_Description],N'') IS NOT NULL THEN 1 ELSE 0 END AS probe_history_position_join,
 CASE WHEN NULLIF(js.[_Description],N'') IS NOT NULL THEN 1 ELSE 0 END AS probe_staffing_position_join,
 CASE WHEN EXISTS(SELECT 1 FROM [dbo].[_InfoRg18211] cx WHERE cx.[_Fld18214RRef]=e.[_IDRRef] AND cx.[_Fld18213]<=@storedNow AND cx.[_Fld18222RRef]<>0x00000000000000000000000000000000) THEN 1 ELSE 0 END AS probe_corrected_history_position_ref,
 CASE WHEN EXISTS(SELECT 1 FROM [dbo].[_InfoRg20861] ox WHERE ox.[_Fld20862RRef]=e.[_IDRRef] AND ox.[_Active]=0x01 AND ox.[_Fld20867]<=@storedNow AND (ox.[_Fld20869_RRRef]<>0x00000000000000000000000000000000 OR NULLIF(LTRIM(RTRIM(ox.[_Fld20869_S])),N'') IS NOT NULL)) THEN 1 ELSE 0 END AS probe_order_position,
 CASE WHEN e.[_Fld30282RRef]<>e.[_IDRRef] AND EXISTS(SELECT 1 FROM [dbo].[_InfoRg21744] hx WHERE hx.[_Fld21746RRef]=e.[_Fld30282RRef] AND hx.[_Fld21750RRef]<>0x00000000000000000000000000000000) THEN 1 ELSE 0 END AS probe_head_employee_position_ref,
 CASE WHEN pc.[_Fld16831RRef] IS NOT NULL THEN 1 ELSE 0 END AS probe_picker_candidate,
 CASE WHEN COALESCE(NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N'')) IS NOT NULL THEN 1 ELSE 0 END AS probe_picker_position_join,
 CASE WHEN pc.candidates>1 THEN 1 ELSE 0 END AS probe_picker_ambiguous,
 CASE WHEN EXISTS(SELECT 1 FROM [dbo].[_InfoRg40095] sx WHERE sx.[_Fld40096RRef]=e.[_IDRRef] AND sx.[_Fld40098]<=@storedNow AND sx.[_Fld40113]=0x00 AND sx.[_Fld40105RRef]<>0x00000000000000000000000000000000) THEN 1 ELSE 0 END AS probe_staffing_occupancy_position_ref,
 CASE WHEN occ.staffing_ref IS NOT NULL THEN 1 ELSE 0 END AS probe_occupancy_candidate,
 CASE WHEN occ.candidates>1 THEN 1 ELSE 0 END AS probe_occupancy_ambiguous,
 CASE WHEN so.[_IDRRef] IS NOT NULL THEN 1 ELSE 0 END AS probe_occupancy_staffing_join,
 CASE WHEN so.[_Fld31540RRef]<>0x00000000000000000000000000000000 THEN 1 ELSE 0 END AS probe_occupancy_position_ref,
 CASE WHEN jo.[_IDRRef] IS NOT NULL THEN 1 ELSE 0 END AS probe_occupancy_position_row,
 CASE WHEN NULLIF(jo.[_Description],N'') IS NOT NULL THEN 1 ELSE 0 END AS probe_occupancy_position_join,
 CASE WHEN doo.[_IDRRef] IS NOT NULL THEN 1 ELSE 0 END AS probe_occupancy_department_row,
 CASE WHEN NULLIF(doo.[_Description],N'') IS NOT NULL THEN 1 ELSE 0 END AS probe_occupancy_department_named,
 CASE WHEN COALESCE(NULLIF(j.[_Description],N''),NULLIF(jp.[_Description],N''),NULLIF(jps.[_Description],N''),NULLIF(ji.[_Description],N''),NULLIF(jh.[_Description],N''),NULLIF(js.[_Description],N''),NULLIF(jo.[_Description],N'')) IS NULL AND sf.staffing_position IS NOT NULL THEN 1 ELSE 0 END AS probe_staffing_display_fallback,
 CASE WHEN NULLIF(so.[_Description],N'') IS NOT NULL THEN 1 ELSE 0 END AS probe_occupancy_staffing_named,
 CASE WHEN EXISTS(SELECT 1 FROM [dbo].[_InfoRg59135] ux WHERE ux.[_Fld59138RRef]=e.[_IDRRef] AND ux.[_Period]<=@storedNow AND ux.[_Fld59139RRef]<>0x00000000000000000000000000000000) THEN 1 ELSE 0 END AS probe_custom_position_ref
FROM [dbo].[_Reference297] e
LEFT JOIN [dbo].[_Reference383] p ON p.[_IDRRef]=e.[_Fld30276RRef]
LEFT JOIN [dbo].[_InfoRg21744] k ON k.[_Fld21746RRef]=e.[_IDRRef]
OUTER APPLY (
 SELECT TOP (1) x.*,COUNT_BIG(*) OVER(PARTITION BY x.[_Fld16846]) AS candidates
 FROM (
   SELECT DISTINCT q.[_Fld16831RRef],q.[_Fld16837RRef],q.[_Fld39554RRef],q.[_Fld16838RRef],q.[_Fld16839RRef],q.[_Fld16842],q.[_Fld16846]
   FROM [dbo].[_InfoRg16829] q
   WHERE q.[_Fld16831RRef]=e.[_IDRRef] AND q.[_Fld39556]=0x00 AND q.[_Fld16846]<=@storedNow
     AND (e.[_Fld30280]=0x01 OR (q.[_Fld39557]=0x00 AND (YEAR(q.[_Fld16847])<=@dateOffset+1 OR CAST(q.[_Fld16847] AS date)>=CAST(@storedNow AS date))))
 ) x
 ORDER BY x.[_Fld16846] DESC
) pc
LEFT JOIN [dbo].[_Reference92] jp ON jp.[_IDRRef]=pc.[_Fld16838RRef] AND pc.candidates=1
LEFT JOIN [dbo].[_Reference394] ps ON ps.[_IDRRef]=pc.[_Fld16839RRef] AND pc.candidates=1
LEFT JOIN [dbo].[_Reference92] jps ON jps.[_IDRRef]=ps.[_Fld31540RRef]
LEFT JOIN [dbo].[_Reference216] dp ON dp.[_IDRRef]=pc.[_Fld16837RRef] AND pc.candidates=1
LEFT JOIN [dbo].[_Reference216] dps ON dps.[_IDRRef]=ps.[_Fld31539RRef]
LEFT JOIN [dbo].[_Reference166] op ON op.[_IDRRef]=pc.[_Fld39554RRef] AND pc.candidates=1
LEFT JOIN [dbo].[_Reference166] opp ON opp.[_IDRRef]=ps.[_OwnerIDRRef]

-- Resolve by the exact employment ID, never by physical person/head employee.
-- Empty dismissal intervals must not erase the last known job of an archived employee.
OUTER APPLY (
 SELECT TOP (1) x.* FROM [dbo].[_InfoRg18183] x
 WHERE x.[_Fld18184RRef]=e.[_IDRRef] AND x.[_Fld18186]<=@storedNow AND x.[_Fld18205]=0x00
   AND (x.[_Fld18194RRef]<>0x00000000000000000000000000000000 OR x.[_Fld18199RRef]<>0x00000000000000000000000000000000)
 ORDER BY x.[_Fld18186] DESC,x.[_Fld18189] DESC,x.[_Fld18203_RRRef] DESC
) i
OUTER APPLY (
 SELECT TOP (1) x.* FROM [dbo].[_InfoRg18156] x
 WHERE x.[_Fld18157RRef]=e.[_IDRRef] AND x.[_Active]=0x01 AND x.[_Period]<=@storedNow
   AND (x.[_Fld18162RRef]<>0x00000000000000000000000000000000 OR x.[_Fld18163RRef]<>0x00000000000000000000000000000000)
 ORDER BY x.[_Period] DESC,x.[_RecorderRRef] DESC,x.[_LineNo] DESC
) h
LEFT JOIN [dbo].[_Reference394] s ON s.[_IDRRef]=COALESCE(NULLIF(i.[_Fld18199RRef],0x00000000000000000000000000000000),NULLIF(h.[_Fld18163RRef],0x00000000000000000000000000000000))
LEFT JOIN [dbo].[_Reference166] o ON o.[_IDRRef]=k.[_Fld21748RRef]
LEFT JOIN [dbo].[_Reference166] oi ON oi.[_IDRRef]=i.[_Fld18193RRef]
LEFT JOIN [dbo].[_Reference166] oh ON oh.[_IDRRef]=h.[_Fld18160RRef]
LEFT JOIN [dbo].[_Reference166] ho ON ho.[_IDRRef]=e.[_Fld30277RRef]
LEFT JOIN [dbo].[_Reference216] d ON d.[_IDRRef]=k.[_Fld21749RRef]
LEFT JOIN [dbo].[_Reference216] di ON di.[_IDRRef]=i.[_Fld18198RRef]
LEFT JOIN [dbo].[_Reference216] dh ON dh.[_IDRRef]=h.[_Fld18161RRef]
LEFT JOIN [dbo].[_Reference216] ds ON ds.[_IDRRef]=s.[_Fld31539RRef]
LEFT JOIN [dbo].[_Reference92] j ON j.[_IDRRef]=k.[_Fld21750RRef]
LEFT JOIN [dbo].[_Reference92] ji ON ji.[_IDRRef]=i.[_Fld18194RRef]
LEFT JOIN [dbo].[_Reference92] jh ON jh.[_IDRRef]=h.[_Fld18162RRef]
LEFT JOIN [dbo].[_Reference92] js ON js.[_IDRRef]=s.[_Fld31540RRef]
-- Occupancy can contain concurrent substitutions/extra work: do not pick one at random.
-- staffing occupancy begin
OUTER APPLY (
 SELECT TOP (1) v.*,COUNT_BIG(*) OVER() AS candidates
 FROM (
   SELECT x.[_Fld40105RRef] AS staffing_ref,x.[_Fld40107] AS fte,MAX(x.[_Fld40098]) AS since
   FROM (
     SELECT q.*,DENSE_RANK() OVER(ORDER BY q.[_Fld40098] DESC) AS latest
     FROM [dbo].[_InfoRg40095] q
     WHERE q.[_Fld40096RRef]=e.[_IDRRef]
       AND q.[_Fld40098]<=CASE WHEN e.[_Fld30280]=0x01 AND YEAR(k.[_Fld21751])>@dateOffset+1 AND k.[_Fld21751]<@storedNow THEN k.[_Fld21751] ELSE @storedNow END
       AND q.[_Fld40113]=0x00 AND q.[_Fld40107]>0
       AND q.[_Fld40105RRef]<>0x00000000000000000000000000000000
       AND (q.[_Fld40101_RRRef] IS NULL OR q.[_Fld40101_RRRef]=0x00000000000000000000000000000000)
       AND (e.[_Fld30280]=0x01 OR (
         (YEAR(q.[_Fld40097])<=@dateOffset+1 OR q.[_Fld40097]>=CAST(@storedNow AS date))
         AND (YEAR(q.[_Fld40108])<=@dateOffset+1 OR q.[_Fld40108]>=CAST(@storedNow AS date))))
   ) x
   WHERE e.[_Fld30280]<>0x01 OR x.latest=1
   GROUP BY x.[_Fld40105RRef],x.[_Fld40107]
 ) v
 ORDER BY v.since DESC,v.staffing_ref
) occ
-- staffing occupancy end
LEFT JOIN [dbo].[_Reference394] so ON so.[_IDRRef]=occ.staffing_ref AND occ.candidates=1
LEFT JOIN [dbo].[_Reference92] jo ON jo.[_IDRRef]=so.[_Fld31540RRef]
LEFT JOIN [dbo].[_Reference216] doo ON doo.[_IDRRef]=so.[_Fld31539RRef]
LEFT JOIN [dbo].[_Reference166] oo ON oo.[_IDRRef]=so.[_OwnerIDRRef]
-- Staffing name selection begin: only unique, already selected employment records.
OUTER APPLY (
 SELECT TOP (1) c.*
 FROM (
   SELECT 1 AS priority,NULLIF(LTRIM(RTRIM(so.[_Description])),N'') AS staffing_position,
     oo.[_Description] AS organisation,doo.[_Description] AS department,occ.fte AS fte,occ.since AS since,
     N'Занятость штатных позиций (наименование)' AS staffing_source,
     so.[_IDRRef] AS staffing_ref,so.[_Fld31540RRef] AS position_ref,so.[_Fld31539RRef] AS department_ref
   UNION ALL
   SELECT 2,NULLIF(LTRIM(RTRIM(ps.[_Description])),N''),
     COALESCE(NULLIF(op.[_Description],N''),opp.[_Description]),COALESCE(NULLIF(dp.[_Description],N''),dps.[_Description]),pc.[_Fld16842],pc.[_Fld16846],
     N'Подбор сотрудников (штатная позиция)',ps.[_IDRRef],ps.[_Fld31540RRef],ps.[_Fld31539RRef]
   UNION ALL
   SELECT 3,NULLIF(LTRIM(RTRIM(s.[_Description])),N''),
     CASE WHEN i.[_Fld18199RRef]=s.[_IDRRef] THEN oi.[_Description] ELSE oh.[_Description] END,ds.[_Description],
     CASE WHEN i.[_Fld18199RRef]=s.[_IDRRef] THEN i.[_Fld18200] ELSE h.[_Fld18164] END,
     CASE WHEN i.[_Fld18199RRef]=s.[_IDRRef] THEN i.[_Fld18186] ELSE h.[_Period] END,
     N'Кадровая история (штатная позиция)',s.[_IDRRef],s.[_Fld31540RRef],s.[_Fld31539RRef]
 ) c
 WHERE c.staffing_position IS NOT NULL
 ORDER BY c.priority
) sf
-- Staffing name selection end
OUTER APPLY (
 SELECT TOP (1) x.[_Fld16737RRef] FROM [dbo].[_InfoRg16734] x
 WHERE x.[_Fld16735RRef]=e.[_IDRRef] AND x.[_Active]=0x01 AND x.[_Period]<=@storedNow
   AND (YEAR(x.[_Fld16738])<=@dateOffset+1 OR x.[_Fld16738]>=@storedNow)
 ORDER BY x.[_Period] DESC,x.[_RecorderRRef] DESC,x.[_LineNo] DESC
) gr
LEFT JOIN [dbo].[_Reference75] g ON g.[_IDRRef]=gr.[_Fld16737RRef]
OUTER APPLY (
 SELECT COUNT_BIG(*) AS candidates, MAX(x.[_Fld20193]) AS tariff_rate,
   MAX(x.[_Fld20194]) AS combined_tariff_rate, MAX(x.[_Fld20195]) AS payroll_fund,MAX(x.[_Period]) AS since
 FROM [dbo].[_InfoRg20185] x
 WHERE x.[_Fld20186RRef]=e.[_IDRRef] AND x.[_Fld20197]=0x01
   AND (YEAR(x.[_Fld20187])<=@dateOffset+1 OR x.[_Fld20187]>=@storedNow)
   AND x.[_Period]=(SELECT MAX(y.[_Period]) FROM [dbo].[_InfoRg20185] y
       WHERE y.[_Fld20186RRef]=e.[_IDRRef] AND y.[_Period]<=@storedNow AND y.[_Fld20197]=0x01)
) pay
WHERE e.[_Marked]=0x00
'@ @{'@dateOffset'=$dateOffset;'@storedNow'=$storedNow} 180)
            # Read only the named sex enumeration metadata. Never infer from
            # employee names, patronymics or _EnumOrder.
            $genderMap=@{};$genderResolved=$false
            try {
                $genderColumns=@(Query $connection @'
SELECT s.name AS [schema],t.name AS [table],c.name AS [column],ty.name AS [type],c.max_length,c.is_nullable
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.columns c ON c.object_id=t.object_id JOIN sys.types ty ON ty.user_type_id=c.user_type_id
WHERE s.name='dbo' AND t.name IN ('Params','Config','_Enum817')
'@)
                $genderMeta=Read-MetadataNames $connection $genderColumns $true
                $enumRefs=@(Query $connection 'SELECT CONVERT(varchar(32),[_IDRRef],2) AS ref FROM [dbo].[_Enum817]')
                foreach($code in @('M','F')){
                    $candidates=@($genderMeta.gender_members.$code)
                    $genderMatches=@($enumRefs | Where-Object {$_.ref -in $candidates})
                    if($genderMatches.Count -ne 1){throw 'Sex enum SQL reference is missing or ambiguous.'}
                    if($genderMap.ContainsKey($genderMatches[0].ref)){throw 'Sex enum references overlap.'}
                    $genderMap[$genderMatches[0].ref]=$code
                }
                $genderResolved=$true
            } catch {$genderMap=@{}}
            foreach($employee in $employees){
                $gender='';if($genderResolved -and $employee.gender_ref -and $genderMap.ContainsKey($employee.gender_ref)){$gender=$genderMap[$employee.gender_ref]}
                $employee | Add-Member -NotePropertyName gender -NotePropertyValue $gender
                $employee.PSObject.Properties.Remove('gender_ref')
            }
            Write-Json $reportPath @{employees_import=$true;employees_card_version=6;gender_source='ФизическоеЛицо.Пол';gender_resolved=$genderResolved;position_catalog=$positionCatalogue[0];employees=@($employees)}
            Write-Json $statusPath @{ok=$true;report=$reportPath}
            return
        }
        if($PositionsImport){
            # Only staffing positions are a source of portal catalogue names.
            # Do not join the possibly empty general job catalogue _Reference92.
            $records=@(Query $connection 'SELECT CONVERT(varchar(32),[_IDRRef],2) AS ref, [_Description] AS name, CONVERT(varchar(20),[_Marked],1) AS marked, CONVERT(varchar(20),[_Fld31544],1) AS approved, CONVERT(varchar(20),[_Fld31546],1) AS closed FROM [dbo].[_Reference394]')
            $notMarked=@($records | Where-Object { $_.marked -eq '0x00' })
            $eligible=@($notMarked | Where-Object { $_.approved -eq '0x01' -and $_.closed -eq '0x00' })
            $names=@($eligible | ForEach-Object { @{name=$_.name} })
            $summary=@{table='dbo._Reference394';total=$records.Count;not_marked=$notMarked.Count;excluded=@($notMarked | Where-Object { $_.closed -eq '0x01' }).Count;eligible=$eligible.Count}
            Write-Json $reportPath @{positions_import=$true;position_names=@($names);position_source=$summary;position_flags=@();position_records=@($records);positions_snapshot_version=2}
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
