#requires -Version 5.1
<#
Start the unified account + personal workspace service without writing secrets to files or command arguments.
-CheckOnly validates existing process environment without prompts or network.
-UseEnvironment starts with existing process environment without prompts.
#>
[CmdletBinding()]
param(
    [string]$Python = 'D:\python\python.exe',
    [string]$ListenAddress = '127.0.0.1',
    [ValidateRange(1, 65535)][int]$Port = 8100,
    [string]$ForwardedAllowIps = '',
    [switch]$CheckOnly,
    [switch]$UseEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$names = @(
    'ZHIHU_OAUTH_APP_ID', 'ZHIHU_OAUTH_APP_KEY', 'ZHIHU_ACCESS_SECRET',
    'ZHIHU_OAUTH_REDIRECT_URI', 'ZHIHU_OAUTH_PROFILE_URL',
    'ZHIHU_OAUTH_PROFILE_AUTH', 'ZHIHU_OAUTH_PROFILE_ID_PATH',
    'ZHIHU_OAUTH_PROFILE_NAME_PATH'
)
$original = @{}
foreach ($name in $names) {
    $original[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Read-ProcessSetting {
    param([string]$Name, [string]$Prompt, [string]$Default = '')
    if ([Environment]::GetEnvironmentVariable($Name, 'Process')) { return }
    $value = Read-Host -Prompt $Prompt
    if (-not $value) { $value = $Default }
    [Environment]::SetEnvironmentVariable($Name, $value, 'Process')
}

function Read-ProcessSecret {
    param([string]$Name, [string]$Prompt)
    if ([Environment]::GetEnvironmentVariable($Name, 'Process')) { return }
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [IntPtr]::Zero
    $plain = $null
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        [Environment]::SetEnvironmentVariable($Name, $plain, 'Process')
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        $plain = $null
        $secure.Dispose()
    }
}

try {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $command = Get-Command -Name $Python -CommandType Application -ErrorAction SilentlyContinue
        if (-not $command) { throw '找不到 Python，请通过 -Python 指定已安装依赖的 python.exe。' }
        $Python = $command.Source
    }
    Write-Host '知乎收藏 · 个人知识库：账号入口与个人工作区同站运行，不开放 8099 共享笔记库。'
    Write-Host '凭证只临时进入本进程与服务子进程环境，不写入 .env、源码或启动参数。'
    if (-not $CheckOnly -and -not $UseEnvironment) {
        Write-Host '已存在的环境配置将沿用；缺项在本机补齐。请勿把密钥粘贴到聊天。'
        Write-Host '采用官方 OAuth /user 身份接口与 state 校验；公网作品必须登记同站 HTTPS 回调。'
        Read-ProcessSetting 'ZHIHU_OAUTH_APP_ID' 'App ID'
        Read-ProcessSecret 'ZHIHU_OAUTH_APP_KEY' 'App Key（隐藏输入）'
        Read-ProcessSecret 'ZHIHU_ACCESS_SECRET' '收藏 API Access Secret（隐藏输入，可留空；与 App Key 不同）'
        $localCallback = 'http://127.0.0.1:' + $Port + '/auth/zhihu/callback'
        Read-ProcessSetting 'ZHIHU_OAUTH_REDIRECT_URI' ('已登记的完整回调地址（仅本机默认：' + $localCallback + '）') $localCallback
        # Settings supplies the official /user, Bearer, /uid and /fullname defaults.
        # Only explicitly configured environment values override that contract.
    }

    # This probe prints only redacted readiness, never credentials or raw env values.
    $probe = @'
import json, sys
sys.path.insert(0, sys.argv[1])
import fastapi, httpx, uvicorn
from zhihu_oauth.config import Settings
print(json.dumps(Settings.from_env().public(), ensure_ascii=True))
'@
    $result = & $Python -X utf8 -c $probe $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'Python 配置检查失败，请检查依赖安装；未启动服务。' }
    $configuration = ($result -join "`n") | ConvertFrom-Json
    foreach ($name in $configuration.missing) { Write-Host ('缺少配置：' + $name) }
    foreach ($issue in $configuration.invalid) { Write-Host ('配置格式无效：' + $issue.field + ' — ' + $issue.message) }
    if (-not $configuration.ready) { throw '配置尚未就绪，未发起授权或启动服务。' }
    Write-Host ('回调地址：' + $configuration.callback_uri)
    Write-Host ('个人工作区：' + $configuration.workspace_uri)
    Write-Host '登录配置格式通过，不代表平台授权或真实双账号验收通过。'
    if ($configuration.collections.ready) {
        Write-Host '收藏接口配置格式通过；实际可读范围仍取决于平台权限。'
    } else {
        Write-Host '收藏接口待配置：仍可授权并进入工作区，暂不能读取或收录收藏。'
        foreach ($name in $configuration.collections.missing) { Write-Host ('收藏缺少配置：' + $name) }
        foreach ($issue in $configuration.collections.invalid) { Write-Host ('收藏配置格式无效：' + $issue.field + ' — ' + $issue.message) }
    }
    if ($CheckOnly) { return }

    $arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'OAuth验证服务.py'), '--host', $ListenAddress, '--port', $Port)
    if ($ForwardedAllowIps) { $arguments += @('--forwarded-allow-ips', $ForwardedAllowIps) }
    Write-Host '使用 Ctrl+C 停止；停止后恢复临时配置。会话不持久化，公开摘要按用户 ID 保存在独立数据库。'
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'OAuth 服务已退出；请核对端口、代理和依赖，不要公开原始授权请求。' }
}
finally {
    # Restore inherited values, or remove only values added by this script.
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $original[$name], 'Process')
    }
}
