# 脑珊瑚 GUI 回滚脚本（作者：@Ne）
# ------------------------------------------------------------------
# 用法:
#   powershell -File webui/rollback.ps1 uninstall        # 从 profile 卸载插件（先备份配置）
#   powershell -File webui/rollback.ps1 restore-config   # 从 _backup 恢复最近的配置备份
#   powershell -File webui/rollback.ps1 git              # 回滚珊瑚仓库代码改动（git restore + 删 webui）
#
# 说明:
#   - uninstall 每次执行前都会把 coral_config.json 备份到 webui/_backup/（时间戳命名）
#   - DSH 源码目录默认 J:\deepseek-harness-master，可在参数里改
# ------------------------------------------------------------------
param(
    [Parameter(Position = 0)]
    [string]$Step = 'uninstall',
    [string]$DshRoot = 'J:\deepseek-harness-master'
)

$ErrorActionPreference = 'Stop'
$coralRoot = Split-Path -Parent $PSScriptRoot   # webui -> 珊瑚仓库根
$backupDir = Join-Path $PSScriptRoot '_backup'

function Backup-Config {
    New-Item -ItemType Directory -Force $backupDir | Out-Null
    $src = Join-Path $coralRoot 'coral_config.json'
    if (Test-Path $src) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $dst = Join-Path $backupDir "coral_config.json.$stamp.bak"
        Copy-Item $src $dst
        Write-Host "[备份] $dst"
    }
}

switch ($Step) {
    'uninstall' {
        Backup-Config
        Write-Host "[卸载] 从 web profile 移除 @dsh-external/dsh-client-coral ..."
        Push-Location $DshRoot
        pnpm dsh plugin --profile web remove @dsh-external/dsh-client-coral
        Pop-Location
        Write-Host "[完成] 插件已卸载。重启 dsh web 生效；珊瑚代码与缓存原样保留。"
        Write-Host "        如需连配置一起回滚: rollback.ps1 restore-config"
    }
    'restore-config' {
        $latest = Get-ChildItem $backupDir -Filter 'coral_config.json.*.bak' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $latest) { Write-Host '[错误] _backup 里没有配置备份'; exit 1 }
        Copy-Item $latest.FullName (Join-Path $coralRoot 'coral_config.json') -Force
        Write-Host "[完成] 已恢复配置: $($latest.Name)（重启珊瑚进程生效）"
    }
    'git' {
        Push-Location $coralRoot
        Write-Host "[回滚] git restore 珊瑚代码 + 删除 webui/ ..."
        git restore three_dog_coral.py coral_config.json tests/test_config_tools.py .gitignore
        Remove-Item $PSScriptRoot -Recurse -Force
        Pop-Location
        Write-Host "[完成] 代码已回到上次提交状态。"
    }
    default {
        Write-Host '用法: rollback.ps1 [uninstall | restore-config | git]'
        exit 1
    }
}
