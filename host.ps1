param([Parameter(Mandatory=$true)][string]$Request, [switch]$ValidateOnly)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$session=Split-Path -Parent $Request
$status=Join-Path $session 'status.json'
$created=@();$firewallCreated=$false;$plan=$null
function Save-State([string]$state,[string]$problem='') {
    $open=@()
    if($state -eq 'running') {
        $open=@(Get-SmbOpenFile -ErrorAction SilentlyContinue | Where-Object {
            $file=$_.Path
            $file -and @($plan.exports | Where-Object {$file.StartsWith($_.path.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)}).Count -gt 0
        })
    }
    $data=[ordered]@{state=$state;encrypted=($state -eq 'running');problem=$problem;openFiles=$open.Count;shares=@($created);updated=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()}
    $tmp=$status+'.new';[IO.File]::WriteAllText($tmp,($data|ConvertTo-Json -Depth 5 -Compress),[Text.UTF8Encoding]::new($false));Move-Item -LiteralPath $tmp -Destination $status -Force
}
function Check-Parent {
    try {$parent=[Diagnostics.Process]::GetProcessById([int]$plan.parentPid);return $parent.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() -eq $plan.parentFiletime}
    catch {return $false}
}
try {
    $plan=Get-Content -LiteralPath $Request -Raw -Encoding UTF8 | ConvertFrom-Json
    if($plan.version -ne 1 -or $plan.hostId -notmatch '^[a-f0-9]{32}$' -or $plan.nonce -notmatch '^[a-f0-9]{32}$' -or @($plan.exports).Count -lt 1 -or @($plan.exports).Count -gt 16){throw 'Невалиден план за споделяне.'}
    if($plan.account -notmatch '^[^\/:*?"<>|\r\n]+\\[^\/:*?"<>|\r\n]+$'){throw 'Използвай конкретен Windows потребител като КОМПЮТЪР\потребител.'}
    $sid=([Security.Principal.NTAccount]::new($plan.account)).Translate([Security.Principal.SecurityIdentifier]).Value
    if($sid -notmatch '^S-1-5-21-'){throw 'Не са разрешени Everyone, Guests или други общи системни групи.'}
    if($plan.network -notmatch '^\d{1,3}(\.\d{1,3}){3}/(1[6-9]|2\d|30)$'){throw 'Невалидна LAN подмрежа.'}
    $ip=Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias $plan.interface -IPAddress $plan.address -ErrorAction Stop
    if([int]$ip.PrefixLength -ne [int]$plan.prefix){throw 'LAN адресът или маската са променени. Подготви нов план.'}
    $ipBytes=([Net.IPAddress]::Parse($plan.address)).GetAddressBytes()
    if(-not ($ipBytes[0] -eq 10 -or ($ipBytes[0] -eq 172 -and $ipBytes[1] -ge 16 -and $ipBytes[1] -le 31) -or ($ipBytes[0] -eq 192 -and $ipBytes[1] -eq 168))){throw 'Допуска се само частен LAN адрес.'}
    # Recompute the exact subnet instead of trusting text supplied to a helper.
    $networkBytes=[byte[]]::new(4)
    for($i=0;$i -lt 4;$i++){$bits=[Math]::Min(8,[Math]::Max(0,[int]$plan.prefix-8*$i));$mask=if($bits -eq 0){0}else{256-[Math]::Pow(2,8-$bits)};$networkBytes[$i]=$ipBytes[$i] -band [int]$mask}
    if(([Net.IPAddress]::new($networkBytes)).ToString()+'/'+$plan.prefix -ne $plan.network){throw 'Подмрежата не съвпада с LAN адреса.'}
    $configuration=Get-SmbServerConfiguration
    if($configuration.EnableSMB1Protocol -or -not $configuration.EnableSMB2Protocol -or -not $configuration.RejectUnencryptedAccess){throw 'SMB трябва да отхвърля нешифрован достъп, SMB 1 трябва да е изключен. Глобалните настройки не се променят автоматично.'}
    if((Get-Service LanmanServer).Status -ne 'Running'){throw 'Windows Server услугата не работи. Не се включва автоматично.'}
    $tag='Astro Archive Shared Archives '+$plan.hostId
    $rule='AstroArchive-SharedArchives-'+$plan.hostId
    if(Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue){throw 'Вече има правило с това име; няма да бъде заменено.'}
    $seen=@{}
    $appRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')).TrimEnd('\')
    foreach($item in $plan.exports) {
        if($item.share -notmatch '^AstroArchive-[A-Za-z0-9_-]{1,48}$' -or $seen.ContainsKey($item.share)){throw 'Невалидно или повторено име на дял.'}
        $seen[$item.share]=$true
        if($item.access -notin @('read','change')){throw 'Невалидни права на дял.'}
        if($item.path -notmatch '^[A-Za-z]:\\' -or -not (Test-Path -LiteralPath $item.path -PathType Container)){throw 'Избери налична локална папка.'}
        $folder=Get-Item -LiteralPath $item.path -Force
        if($folder.FullName.TrimEnd('\') -eq $folder.PSDrive.Root.TrimEnd('\')){throw 'Не се споделя целият диск.'}
        if($folder.FullName.TrimEnd('\') -eq $appRoot -or $folder.FullName.StartsWith($appRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or $appRoot.StartsWith($folder.FullName.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Програмната папка не се споделя.'}
        $cursor=$folder
        while($cursor){if($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Връзки към други папки не се споделят.'};$cursor=$cursor.Parent}
        if(Get-SmbShare -Name $item.share -ErrorAction SilentlyContinue){throw 'Това име вече се използва от мрежов дял. Съществуващи дялове не се променят.'}
    }
    if($ValidateOnly){[pscustomobject]@{valid=$true;encrypted=$true;shares=@($plan.exports.share);localAddress=$plan.address;remoteNetwork=$plan.network;interface=$plan.interface;account=$plan.account;port=445}|ConvertTo-Json -Compress;exit 0}
    if(-not (Check-Parent)){throw 'Програмата, заявила споделянето, вече не работи.'}
    $stopPath=Join-Path $session 'stop.json'
    if((Test-Path -LiteralPath $stopPath) -and (Get-Content -LiteralPath $stopPath -Raw -Encoding UTF8|ConvertFrom-Json).nonce -eq $plan.nonce){throw 'Включването е отменено.'}
    foreach($item in $plan.exports) {
        $parameters=@{Name=$item.share;Path=$item.path;Description=$tag;EncryptData=$true;Temporary=$true;CachingMode='None';FolderEnumerationMode='AccessBased';ConcurrentUserLimit=[uint32]$plan.maxClients;ErrorAction='Stop'}
        if($item.access -eq 'read'){$parameters.ReadAccess=@($plan.account)}else{$parameters.ChangeAccess=@($plan.account)}
        New-SmbShare @parameters | Out-Null
        $created+=@($item.share)
        $verified=Get-SmbShare -Name $item.share
        if(-not $verified.EncryptData -or $verified.Description -ne $tag){throw 'Windows не потвърди шифровано споделяне.'}
    }
    New-NetFirewallRule -Name $rule -DisplayName 'Astro Archive · encrypted LAN archives' -Description $tag -Direction Inbound -Action Allow -Protocol TCP -LocalPort 445 -LocalAddress $plan.address -RemoteAddress $plan.network -InterfaceAlias $plan.interface -Profile Any -Program System -EdgeTraversalPolicy Block | Out-Null
    $firewallCreated=$true
    Save-State 'running'
    while(Check-Parent) {
        $heartbeat=Join-Path $session 'heartbeat.json';$stop=Join-Path $session 'stop.json'
        if(Test-Path -LiteralPath $stop){try{if((Get-Content -LiteralPath $stop -Raw -Encoding UTF8|ConvertFrom-Json).nonce -eq $plan.nonce){break}}catch{}}
        if(-not (Test-Path -LiteralPath $heartbeat) -or ([DateTime]::UtcNow-(Get-Item -LiteralPath $heartbeat).LastWriteTimeUtc).TotalSeconds -gt 15){break}
        # Never keep publishing a green status after policy or share tampering.
        $configuration=Get-SmbServerConfiguration
        if($configuration.EnableSMB1Protocol -or -not $configuration.RejectUnencryptedAccess){throw 'Политиката за шифроване е променена; споделянето е спряно.'}
        foreach($name in $created){$current=Get-SmbShare -Name $name;if(-not $current.EncryptData -or $current.Description -ne $tag){throw 'Дялът е променен; споделянето е спряно.'}}
        Save-State 'running';Start-Sleep -Seconds 2
    }
    Save-State 'stopping'
} catch {
    try{Save-State 'error' $_.Exception.Message}catch{}
    $hostFailure=$true
} finally {
    if(-not $ValidateOnly) {
        $remaining=@()
        foreach($name in $created) {
            try{
                $current=Get-SmbShare -Name $name -ErrorAction SilentlyContinue
                if($current -and $current.Description -eq $tag){Remove-SmbShare -Name $name -Force -Confirm:$false -ErrorAction Stop}
                if(Get-SmbShare -Name $name -ErrorAction SilentlyContinue){$remaining+=@($name)}
            }catch{$remaining+=@($name)}
        }
        if($firewallCreated){try{$current=Get-NetFirewallRule -Name $rule -ErrorAction Stop;if($current.Description -eq $tag){Remove-NetFirewallRule -Name $rule -ErrorAction Stop};if(Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue){$remaining+=@('Firewall')}}catch{$remaining+=@('Firewall')}}
        if($remaining.Count -gt 0){$hostFailure=$true;try{Save-State 'error' ('Windows не потвърждава премахване на: '+($remaining -join ', ')+'. Провери администраторски.')}catch{}}
        elseif(-not $hostFailure){try{Save-State 'stopped'}catch{}}
    }
}
if($hostFailure){exit 1}
