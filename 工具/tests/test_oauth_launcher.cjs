'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const launcher = path.join(root, '工具', '启动OAuth验证.ps1');
const python = process.env.OAUTH_TEST_PYTHON || 'D:\\python\\python.exe';
const powershell = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
const options = { skip: process.platform !== 'win32' || !fs.existsSync(python) ? 'Windows + Python with OAuth dependencies required' : false };
const quote = value => "'" + value.replaceAll("'", "''") + "'";
const keys = [
  'ZHIHU_OAUTH_APP_ID', 'ZHIHU_OAUTH_APP_KEY', 'ZHIHU_ACCESS_SECRET',
  'ZHIHU_OAUTH_REDIRECT_URI', 'ZHIHU_OAUTH_PROFILE_URL',
  'ZHIHU_OAUTH_PROFILE_AUTH', 'ZHIHU_OAUTH_PROFILE_ID_PATH',
  'ZHIHU_OAUTH_PROFILE_NAME_PATH',
];
const secrets = ['synthetic-app-key-not-a-credential', 'synthetic-access-secret-not-a-credential'];
const validEnvironment =
  "$env:ZHIHU_OAUTH_APP_ID = 'synthetic-app';\n" +
  "$env:ZHIHU_OAUTH_APP_KEY = '" + secrets[0] + "';\n" +
  "$env:ZHIHU_ACCESS_SECRET = '" + secrets[1] + "';\n" +
  "$env:ZHIHU_OAUTH_REDIRECT_URI = 'http://127.0.0.1:8100/auth/zhihu/callback';\n" +
  "$env:ZHIHU_OAUTH_PROFILE_URL = 'https://openapi.zhihu.com/mock-test-only';\n" +
  "$env:ZHIHU_OAUTH_PROFILE_AUTH = 'oauth_bearer';\n" +
  "$env:ZHIHU_OAUTH_PROFILE_ID_PATH = '/data/id';\n";

function runPowerShell(body) {
  // Tests always remove inherited Zhihu settings and use synthetic values only.
  const programData = (process.env.ProgramData || 'C:\\ProgramData')
    .replace(/%SystemDrive%/gi, process.env.SystemDrive || 'C:');
  const env = { ...process.env, ProgramData: programData, PATHEXT: '.COM;.EXE;.BAT;.CMD', ComSpec: 'C:\\Windows\\System32\\cmd.exe' };
  for (const key of keys) delete env[key];
  const command = [
    '$ErrorActionPreference = "Stop"',
    '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)',
    '$OutputEncoding = [Console]::OutputEncoding',
    '$launcher = ' + quote(launcher),
    '$python = ' + quote(python),
    '$keys = @(' + keys.map(quote).join(', ') + ')',
    body,
  ].join('\n');
  const result = spawnSync(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command], {
    cwd: root, env, encoding: 'utf8', windowsHide: true, timeout: 30000,
  });
  assert.ifError(result.error);
  const output = result.stdout + result.stderr;
  for (const secret of secrets) assert.equal(output.includes(secret), false, 'launcher must not print credentials');
  assert.equal(result.status, 0, output);
  assert.match(output, /LAUNCHER_TEST_OK/);
  return output;
}

const assertRestored = String.raw`
foreach ($key in $keys) {
    $expected = if ($key -eq 'ZHIHU_OAUTH_APP_ID') { 'inherited-app' } elseif ($key -eq 'ZHIHU_OAUTH_PROFILE_URL' -and $global:inheritedProfile) { $global:inheritedProfile } else { $null }
    if ([Environment]::GetEnvironmentVariable($key, 'Process') -ne $expected) {
        throw ('Configuration was not restored: ' + $key)
    }
}
if ($global:hiddenPrompts -ne 2) { throw 'Both secrets must use hidden prompts' }
Write-Output 'LAUNCHER_TEST_OK'
`;

function promptMock(profileUrl = '') {
  return String.raw`
$env:ZHIHU_OAUTH_APP_ID = 'inherited-app'
$global:answers = [System.Collections.Generic.Queue[string]]::new()
$global:inheritedProfile = ` + quote(profileUrl) + String.raw`
$env:ZHIHU_OAUTH_PROFILE_URL = $global:inheritedProfile
@('synthetic-app-key-not-a-credential', 'synthetic-access-secret-not-a-credential', '') | ForEach-Object { $global:answers.Enqueue($_) }
$global:hiddenPrompts = 0
function global:Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    if ($global:answers.Count -eq 0) { throw 'Unexpected prompt' }
    $answer = $global:answers.Dequeue()
    if ($AsSecureString) {
        $global:hiddenPrompts++
        $secure = [Security.SecureString]::new()
        foreach ($character in $answer.ToCharArray()) { $secure.AppendChar($character) }
        $secure.MakeReadOnly()
        return $secure
    }
    return $answer
}
`;
}

test('launcher rejects missing settings without prompts, network, or credential output', options, () => {
  const output = runPowerShell(String.raw`
function global:Read-Host { throw 'CheckOnly must never prompt' }
$caught = $false
try { & $launcher -Python $python -CheckOnly }
catch { $caught = $true }
if (-not $caught) { throw 'Missing settings must not pass' }
foreach ($key in $keys) {
    if ([Environment]::GetEnvironmentVariable($key, 'Process')) { throw 'Unexpected environment mutation' }
}
Write-Output 'LAUNCHER_TEST_OK'
`);
  assert.match(output, /ZHIHU_OAUTH_APP_KEY/);
  assert.doesNotMatch(output, /配置格式通过/);
});

test('launcher CheckOnly validates synthetic settings and preserves inherited values', options, () => {
  const output = runPowerShell(validEnvironment + String.raw`
function global:Read-Host { throw 'CheckOnly must never prompt' }
$before = @{}
foreach ($key in $keys) { $before[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
& $launcher -Python $python -CheckOnly
foreach ($key in $keys) {
    if ([Environment]::GetEnvironmentVariable($key, 'Process') -ne $before[$key]) { throw 'Inherited configuration changed' }
}
Write-Output 'LAUNCHER_TEST_OK'
`);
  assert.match(output, /配置格式通过/);
  assert.doesNotMatch(output, /Ctrl\+C/);
});

test('launcher reports invalid fields without echoing their supplied values', options, () => {
  const marker = 'invalid-profile-sensitive-marker';
  const output = runPowerShell(validEnvironment +
    "$env:ZHIHU_OAUTH_PROFILE_URL = 'https://not-zhihu.example/" + marker + "';\n" + String.raw`
$caught = $false
try { & $launcher -Python $python -CheckOnly }
catch { $caught = $true }
if (-not $caught) { throw 'Invalid profile URL must not pass' }
Write-Output 'LAUNCHER_TEST_OK'
`);
  assert.match(output, /ZHIHU_OAUTH_PROFILE_URL/);
  assert.equal(output.includes(marker), false);
});

test('launcher clears prompted settings and preserves inherited ones after validation fails', options, () => {
  const output = runPowerShell(promptMock('https://not-zhihu.example/mock-only') + String.raw`
$caught = $false
try { & $launcher -Python $python }
catch { $caught = $true }
if (-not $caught) { throw 'Invalid prompted settings must not pass' }
` + assertRestored);
  assert.match(output, /ZHIHU_OAUTH_PROFILE_URL/);
});

test('launcher clears prompted settings after a successful mocked service lifecycle', options, () => {
  // A local script replaces Python only for this lifecycle test; no server/network starts.
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'zhihu-oauth-launcher-'));
  const stub = path.join(directory, 'mock-python.ps1');
  fs.writeFileSync(stub, String.raw`
$global:LASTEXITCODE = 0
if ($args -contains '-c') {
    '{"ready":true,"missing":[],"invalid":[],"callback_uri":"http://127.0.0.1:8100/auth/zhihu/callback","workspace_uri":"http://127.0.0.1:8100/workspace/","collections":{"ready":true,"missing":[],"invalid":[]}}'
} else {
    if (-not $env:ZHIHU_OAUTH_APP_KEY -or -not $env:ZHIHU_ACCESS_SECRET) { throw 'Missing child environment' }
    if ($env:ZHIHU_OAUTH_APP_ID -ne 'inherited-app') { throw 'Inherited application ID changed' }
    $global:serviceArguments = @($args)
    $global:serviceRan = $true
}
`, 'utf8');
  try {
    runPowerShell(promptMock() +
      '$stub = ' + quote(stub) + '\n' + String.raw`
$global:serviceRan = $false
& $launcher -Python $stub -ListenAddress '127.0.0.1' -Port 8100 -ForwardedAllowIps '127.0.0.1'
if (-not $global:serviceRan) { throw 'Service invocation missing' }
if (($global:serviceArguments -join '|') -notmatch '--forwarded-allow-ips\|127\.0\.0\.1') { throw 'Proxy arguments were not passed' }
foreach ($argument in $global:serviceArguments) {
    if ($argument -eq 'synthetic-app-key-not-a-credential' -or $argument -eq 'synthetic-access-secret-not-a-credential') { throw 'Secret found in arguments' }
}
` + assertRestored);
  } finally {
    // Non-recursive cleanup of these two explicitly created paths only.
    fs.unlinkSync(stub);
    fs.rmdirSync(directory);
  }
});


test('launcher accepts official OAuth defaults without a collection secret', options, () => {
  const output = runPowerShell(validEnvironment + String.raw`
Remove-Item Env:ZHIHU_ACCESS_SECRET
Remove-Item Env:ZHIHU_OAUTH_PROFILE_URL
Remove-Item Env:ZHIHU_OAUTH_PROFILE_AUTH
Remove-Item Env:ZHIHU_OAUTH_PROFILE_ID_PATH
function global:Read-Host { throw 'CheckOnly must never prompt' }
& $launcher -Python $python -CheckOnly
Write-Output 'LAUNCHER_TEST_OK'
`);
  assert.match(output, /登录配置格式通过/);
  assert.match(output, /收藏接口待配置/);
  assert.doesNotMatch(output, /Ctrl\+C/);
});
