#requires -Version 5.1
<#
Start the combined entry (Zhihu OAuth login gate + knowledge workspace) without
writing secrets to files or command arguments.
-CheckOnly validates existing process environment without prompts or network.
-UseEnvironment starts with existing process environment without prompts.
#>
[CmdletBinding()]
param(
    [string]$Python = 'D:\python\python.exe',
    [string]$ListenAddress = '127.0.0.1',
    [ValidateRange(1, 65535)][int]$Port = 8099,
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

function Read-ProcessOptional {
    # Optional override. Empty input means "keep the built-in default", so the
    # variable must NOT be set at all: config.py treats an empty-but-present
    # env var as authoritative and stops falling back to its default value.
    param([string]$Name, [string]$Prompt)
    if ([Environment]::GetEnvironmentVariable($Name, 'Process')) { return }
    $value = Read-Host -Prompt $Prompt
    if ($value) { [Environment]::SetEnvironmentVariable($Name, $value, 'Process') }
}

try {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $command = Get-Command -Name $Python -CommandType Application -ErrorAction SilentlyContinue
        if (-not $command) { throw '找不到 Python，请通过 -Python 指定已安装依赖的 python.exe。' }
        $Python = $command.Source
    }
    Write-Host '一体化入口：知乎授权登录 + 知识库工作区，同一个端口、同一个 origin。'
    Write-Host '凭证只临时进入本进程与服务子进程环境，不写入 .env、源码或启动参数。'
    if (-not $CheckOnly -and -not $UseEnvironment) {
        Write-Host '已存在的环境配置将沿用；缺项在本机补齐。请勿把密钥粘贴到聊天。'
        Write-Host '回调地址必须与活动页面登记值完全一致（协议、域名、端口、路径、无尾斜杠）。'
        Read-ProcessSetting 'ZHIHU_OAUTH_APP_ID' 'App ID（活动页面分配）'
        Read-ProcessSecret 'ZHIHU_OAUTH_APP_KEY' 'App Key（隐藏输入）'
        Read-ProcessSecret 'ZHIHU_ACCESS_SECRET' 'Access Secret（隐藏输入，与 App Key 不同）'
        $localCallback = 'http://127.0.0.1:' + $Port + '/auth/zhihu/callback'
        Read-ProcessSetting 'ZHIHU_OAUTH_REDIRECT_URI' ('已登记的完整回调地址（仅本机默认：' + $localCallback + '）') $localCallback
        Write-Host '以下四项官方已有默认值：直接回车即使用默认，只有平台明确给了别的契约才填写。'
        Read-ProcessOptional 'ZHIHU_OAUTH_PROFILE_URL' '用户身份接口 URL（回车=官方默认 https://openapi.zhihu.com/user）'
        Read-ProcessOptional 'ZHIHU_OAUTH_PROFILE_AUTH' '鉴权模式（回车=官方默认 oauth_bearer）'
        Read-ProcessOptional 'ZHIHU_OAUTH_PROFILE_ID_PATH' '稳定用户 ID 字段路径（回车=官方默认 /uid）'
        Read-ProcessOptional 'ZHIHU_OAUTH_PROFILE_NAME_PATH' '昵称字段路径（回车=官方默认 /fullname）'
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
    if (-not $configuration.ready) { throw '配置尚未就绪，未启动服务；登录按钮会保持禁用。' }
    Write-Host ('回调地址：' + $configuration.callback_uri)
    Write-Host '配置格式通过，不代表平台协议或真实双账号验收通过。'
    if ($CheckOnly) { return }

    $arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot '应用.py'),
        '--host', $ListenAddress, '--port', $Port, '--不开浏览器')
    Write-Host ('入口地址：http://' + $ListenAddress + ':' + $Port)
    Write-Host '使用 Ctrl+C 停止；停止后清除临时配置，服务端会话不会持久化。'
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw '一体化服务已退出；请核对端口和依赖，不要公开原始授权请求。' }
}
finally {
    # Restore inherited values, or remove only values added by this script.
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $original[$name], 'Process')
    }
}
