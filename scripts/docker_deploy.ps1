[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8080,
    [ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$ProjectName = 'course-registration',
    [switch]$Stop,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$StateDir = Join-Path $ProjectRoot '.runtime\docker'
$EnvPath = Join-Path $StateDir 'compose.env'
$SecretsDir = Join-Path $StateDir 'secrets'
$ComposeFile = Join-Path $ProjectRoot 'compose.yaml'
$StagingDir = $null
$DeployLock = $null

function Get-DockerResult([string[]]$DockerArguments) {
    $OriginalPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $Lines = @(& docker @DockerArguments 2>&1)
        $Code = $LASTEXITCODE
        return [PSCustomObject]@{ Code = $Code; Text = ($Lines | Out-String).Trim() }
    } finally { $ErrorActionPreference = $OriginalPreference }
}

function Protect-LocalDirectory([string]$Path) {
    [IO.Directory]::CreateDirectory($Path) | Out-Null
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $SystemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $Acl = New-Object Security.AccessControl.DirectorySecurity
    $Acl.SetAccessRuleProtection($true, $false)
    foreach ($Sid in @($Identity, $SystemSid)) {
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $Sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'
        )
        $Acl.AddAccessRule($Rule)
    }
    # DirectoryInfo writes only the access section changed above. Set-Acl
    # may also request SACL privileges on an already protected directory.
    $Directory = New-Object IO.DirectoryInfo($Path)
    $Directory.SetAccessControl($Acl)
}

function New-RandomSecret([bool]$KeepPadding = $false) {
    $Bytes = New-Object byte[] 32
    $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Random.GetBytes($Bytes) } finally { $Random.Dispose() }
    $Value = [Convert]::ToBase64String($Bytes).Replace('+', '-').Replace('/', '_')
    if (-not $KeepPadding) { $Value = $Value.TrimEnd('=') }
    return $Value
}

function Write-Utf8File([string]$Path, [string]$Value) {
    [IO.File]::WriteAllText($Path, $Value, (New-Object Text.UTF8Encoding($false)))
}

function Show-LogsHint {
    Write-Host '故障排查：在项目目录执行下面的命令查看服务日志（密钥文件请勿公开）。' -ForegroundColor Yellow
    Write-Host ('docker compose --env-file .runtime/docker/compose.env -p ' + $ProjectName + ' -f compose.yaml logs --tail 100 init api mysql catalog billing worker')
    Write-Host '修复后重新运行部署脚本即可。已有数据库和密钥会保留。'
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw '未检测到 Docker。请安装 Docker Desktop：https://www.docker.com/products/docker-desktop/ ，启动后选择 Linux 容器，并等待引擎就绪，再运行本脚本。项目依赖和镜像会由本脚本自动下载；本脚本不会修改 Windows、安装 WSL 或重启电脑。'
    }
    $ComposeVersion = Get-DockerResult @('compose', 'version', '--short')
    if ($ComposeVersion.Code -ne 0) { throw 'Docker Compose 不可用。请更新 Docker Desktop，确保 docker compose version 可以正常执行。' }
    $Engine = Get-DockerResult @('info', '--format', '{{.OSType}}')
    if ($Engine.Code -ne 0) { throw 'Docker 引擎尚未启动或当前用户无访问权限。请启动 Docker Desktop，等待引擎就绪，再运行本脚本；可用 docker info 排查。' }
    if ($Engine.Text -ne 'linux') { throw '当前 Docker 不是 Linux 容器引擎。请在 Docker Desktop 中切换到 Linux containers 后重试。' }
    if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) { throw ('缺少部署文件：' + $ComposeFile) }
    if ($CheckOnly) {
        Write-Host ('检查通过：Linux Docker 引擎和 Compose ' + $ComposeVersion.Text + ' 可用。未生成密钥，也未启动容器。')
        exit 0
    }

    if ((Test-Path -LiteralPath $EnvPath -PathType Leaf) -and -not $PSBoundParameters.ContainsKey('ProjectName')) {
        $SavedProjects = @(Get-Content -LiteralPath $EnvPath -Encoding UTF8 | Where-Object { $_ -match '^COMPOSE_PROJECT_NAME=' })
        if ($SavedProjects.Count -gt 1) { throw 'compose.env 存在重复 COMPOSE_PROJECT_NAME，请只保留一行。' }
        if ($SavedProjects.Count -eq 1) {
            $SavedProject = $SavedProjects[0].Substring(21)
            if ($SavedProject -cnotmatch '^[a-z0-9][a-z0-9_-]*$') { throw 'compose.env 的项目名不合法，只允许小写字母、数字、下划线和连字符。' }
            $ProjectName = $SavedProject
        }
    }

    if ($Stop) {
        if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { throw '没有找到 .runtime/docker/compose.env；请使用曾经部署该项目的完整项目目录停止服务。' }
        $ComposeArgs = @('compose', '--env-file', $EnvPath, '-p', $ProjectName, '-f', $ComposeFile)
        & docker @ComposeArgs down
        if ($LASTEXITCODE -ne 0) { throw '停止容器失败。请检查 Docker 引擎状态后重试；未删除数据库卷。' }
        Write-Host 'Docker 服务已停止。数据库卷、密钥和演示账号文件均已保留。再次运行部署脚本即可启动。'
        exit 0
    }

    Protect-LocalDirectory $StateDir
    $LockPath = Join-Path $StateDir 'deploy.lock'
    try { $DeployLock = [IO.File]::Open($LockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw '另一个 Docker 部署脚本正在运行，请等待它结束后再试。' }

    if ((Test-Path -LiteralPath $EnvPath -PathType Leaf) -and -not $PSBoundParameters.ContainsKey('Port')) {
        $SavedPort = @(Get-Content -LiteralPath $EnvPath -Encoding UTF8 | Where-Object { $_ -match '^APP_PORT=([0-9]+)$' })
        if ($SavedPort.Count -eq 1) {
            $SavedValue = $SavedPort[0].Substring(9)
            if ($SavedValue.Length -gt 5 -or [int]$SavedValue -lt 1 -or [int]$SavedValue -gt 65535) { throw 'compose.env 中 APP_PORT 不合法，请改为 1～65535 的整数。' }
            $Port = [int]$SavedValue
        } else { throw 'compose.env 中缺少唯一有效的 APP_PORT 配置，请用 -Port 8080 明确指定端口。' }
    }

    $VolumeName = $ProjectName + '_mysql_data'
    $VolumeList = Get-DockerResult @('volume', 'ls', '--format', '{{.Name}}')
    if ($VolumeList.Code -ne 0) { throw '无法读取 Docker 数据卷清单，请检查 Docker 引擎状态。未生成或替换密钥。' }
    $HasDatabase = @($VolumeList.Text -split '\r?\n') -contains $VolumeName
    $SecretNames = @('mysql_root_password', 'app_db_password', 'catalog_db_password', 'billing_db_password', 'encryption_key', 'integration_key')
    if (Test-Path -LiteralPath $SecretsDir) {
        foreach ($Name in $SecretNames) {
            $SecretPath = Join-Path $SecretsDir $Name
            if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) { throw ('密钥文件缺失：' + $Name + '。请从备份恢复完整 .runtime/docker/secrets，不能用新密钥替换已有数据库的凭据。') }
            $Value = [IO.File]::ReadAllText($SecretPath)
            $Pattern = if ($Name -eq 'encryption_key') { '^[A-Za-z0-9_-]{43}=$' } else { '^[A-Za-z0-9_-]{43}$' }
            if ($Value -cnotmatch $Pattern) { throw ('密钥文件格式不正确：' + $Name + '。请从备份恢复，或核对文件是否被编辑；密钥内容未输出。') }
        }
    } else {
        if ($HasDatabase) { throw ('已存在数据库卷 ' + $VolumeName + '，但本地密钥目录缺失。请先恢复原 .runtime/docker/secrets，防止数据库凭据和加密密钥失配。不会生成替代密钥。') }
        $StagingDir = Join-Path $StateDir ('.secrets-' + [Guid]::NewGuid().ToString('N'))
        Protect-LocalDirectory $StagingDir
        foreach ($Name in $SecretNames) { Write-Utf8File (Join-Path $StagingDir $Name) (New-RandomSecret ($Name -eq 'encryption_key')) }
        [IO.Directory]::Move($StagingDir, $SecretsDir)
        $StagingDir = $null
        Write-Host '首次部署密钥已随机生成并保存在本地受限目录，未写入源码或终端。'
    }
    Protect-LocalDirectory (Join-Path $StateDir 'accounts')
    $ComposeStatePath = $StateDir.Replace('\', '/')
    if ($ComposeStatePath.Contains('"') -or $ComposeStatePath.Contains("`r") -or $ComposeStatePath.Contains("`n")) { throw '项目路径不能包含双引号或换行，请移动项目目录后重试。' }
    # Compose .env uses $$ for literal $, so a project path remains literal.
    $ComposeStatePath = $ComposeStatePath.Replace('$', '$$')
    $EnvTemp = Join-Path $StateDir ('compose-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    Write-Utf8File $EnvTemp ('APP_PORT=' + $Port + "`nCOMPOSE_PROJECT_NAME=" + $ProjectName + "`nDOCKER_STATE_DIR=" + '"' + $ComposeStatePath + '"' + "`nLOCAL_UID=0`nLOCAL_GID=0`n")
    try {
        if (Test-Path -LiteralPath $EnvPath) { [IO.File]::Replace($EnvTemp, $EnvPath, [System.Management.Automation.Language.NullString]::Value) }
        else { [IO.File]::Move($EnvTemp, $EnvPath) }
    } finally { if (Test-Path -LiteralPath $EnvTemp) { Remove-Item -LiteralPath $EnvTemp -Force } }

    $ComposeArgs = @('compose', '--env-file', $EnvPath, '-p', $ProjectName, '-f', $ComposeFile)
    & docker @ComposeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose 配置校验失败，请检查上方错误。数据和密钥已保留。' }
    Write-Host '正在自动下载镜像、安装项目依赖、构建前端并初始化数据库。首次运行可能需要较长时间。'
    & docker @ComposeArgs up --build --detach
    if ($LASTEXITCODE -ne 0) { Show-LogsHint; throw '构建或启动失败，请根据日志修复网络、端口或初始化错误后重试。数据库和密钥已保留。' }

    $Address = 'http://127.0.0.1:' + $Port
    $Deadline = [DateTime]::UtcNow.AddSeconds(300)
    $Ready = $false
    while ([DateTime]::UtcNow -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri ($Address + '/api/v1/health') -TimeoutSec 5
            if ($Response.StatusCode -eq 200) { $Ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if (-not $Ready) { Show-LogsHint; throw '容器已启动，但等待系统就绪超过 5 分钟。请检查服务日志；本脚本未删除任何数据。' }
    Write-Host ('部署成功：' + $Address) -ForegroundColor Green
    Write-Host ('演示账号文件：' + (Join-Path $StateDir 'accounts\演示账号.txt'))
    Write-Host '再次运行本脚本只会更新和启动服务，不会重置数据库或演示密码。请妥善备份 .runtime/docker 和数据库。'
    exit 0
} catch {
    Write-Host ('部署操作未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($DeployLock) { $DeployLock.Dispose() }
    if ($StagingDir -and (Test-Path -LiteralPath $StagingDir)) {
        $FullStage = [IO.Path]::GetFullPath($StagingDir)
        $FullParent = [IO.Path]::GetFullPath($StateDir)
        if ([IO.Path]::GetDirectoryName($FullStage) -eq $FullParent -and [IO.Path]::GetFileName($FullStage).StartsWith('.secrets-')) {
            Remove-Item -LiteralPath $FullStage -Recurse -Force
        }
    }
}
