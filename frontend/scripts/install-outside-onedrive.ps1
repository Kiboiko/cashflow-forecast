# Ставит зависимости фронта ВНЕ папки OneDrive и оставляет в frontend/ ссылку (junction) на node_modules.
#
# Зачем: npm заменяет ссылку node_modules обычной папкой, и OneDrive начинает синхронизировать десятки тысяч файлов
# (установка 17 пакетов занимала минуты). Здесь npm работает в отдельной папке, а в frontend/ остаётся только ссылка.
#
# Запуск из frontend/:   powershell -ExecutionPolicy Bypass -File scripts/install-outside-onedrive.ps1 [аргументы npm]
# Без аргументов — `npm install`. Примеры: ... install -D vitest   |   ... ci
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$NpmArgs)
$ErrorActionPreference = 'Stop'
if (-not $NpmArgs) { $NpmArgs = @('install') }

$frontend = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'package.json'))) { throw "Запустите из папки frontend (там нет package.json: $frontend)" }
$stage = Join-Path $env:LOCALAPPDATA 'kassa-radar-web'
New-Item -ItemType Directory -Force $stage | Out-Null

foreach ($file in 'package.json', 'package-lock.json') {
    if (Test-Path -LiteralPath (Join-Path $frontend $file)) { Copy-Item -LiteralPath (Join-Path $frontend $file) -Destination $stage -Force }
}
Push-Location $stage
$ErrorActionPreference = 'Continue'      # предупреждения npm идут в stderr и не должны обрывать скрипт
try {
    & npm @NpmArgs --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm завершился с кодом $LASTEXITCODE" }
} finally {
    Pop-Location
    $ErrorActionPreference = 'Stop'
}
foreach ($file in 'package.json', 'package-lock.json') {
    if (Test-Path -LiteralPath (Join-Path $stage $file)) { Copy-Item -LiteralPath (Join-Path $stage $file) -Destination $frontend -Force }
}

$link = Join-Path $frontend 'node_modules'
$existing = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    New-Item -ItemType Junction -Path $link -Target (Join-Path $stage 'node_modules') | Out-Null
} elseif ($existing.LinkType -ne 'Junction') {
    throw "frontend\node_modules — обычная папка внутри OneDrive. Удалите её и запустите скрипт снова."
}
Write-Output "Готово: node_modules -> $((Get-Item -LiteralPath $link -Force).Target)"
