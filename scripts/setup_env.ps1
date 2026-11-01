# 一键创建项目环境（Windows PowerShell）
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
#       默认：创建 .venv 并**继承系统已装包**（秒级完成、零下载，适合本机已有 Anaconda）
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1 -Isolated
#       隔离：创建干净 .venv 并从镜像完整安装（更可复现，需要稳定网络）
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1 -Force      # 重建
#   powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1 -SkipModels # 不下载视觉权重
[CmdletBinding()]
param(
    [switch]$Isolated,
    [switch]$Force,
    [switch]$SkipModels,
    [string]$Python = "python",
    [string]$IndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

function Write-Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

Write-Step "1/4 检查 Python"
$ver = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "  Python $ver（需要 >= 3.10；本项目在 3.13.5 验证）"
if ([version]$ver -lt [version]"3.10") { throw "Python 版本过低：$ver" }

Write-Step "2/4 创建虚拟环境 .venv"
if ((Test-Path $VenvPy) -and -not $Force) {
    Write-Host "  已存在，跳过（用 -Force 重建）" -ForegroundColor Yellow
} else {
    if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
    if ($Isolated) {
        & $Python -m venv $Venv
        Write-Host "  已创建（隔离模式）"
    } else {
        & $Python -m venv --system-site-packages $Venv
        Write-Host "  已创建（继承系统包：秒级、零下载）"
    }
}

Write-Step "3/4 安装依赖"
if ($Isolated) {
    Write-Host "  隔离模式：从 $IndexUrl 安装（可能需要几分钟）"
    & $VenvPy -m pip install -U pip -i $IndexUrl --quiet
    & $VenvPy -m pip install -r (Join-Path $Root "requirements.txt") -i $IndexUrl --progress-bar off
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败；可换镜像：-IndexUrl https://mirrors.aliyun.com/pypi/simple" }
} else {
    Write-Host "  继承模式：只补齐缺失项"
    $probe = "import importlib.util as u;" +
             "need={'fastapi':'fastapi','uvicorn':'uvicorn','pydantic':'pydantic'," +
             "'yaml':'PyYAML','numpy':'numpy','PIL':'Pillow','httpx':'httpx'," +
             "'onnxruntime':'onnxruntime','pytest':'pytest','scipy':'scipy'};" +
             "print(' '.join(p for m,p in need.items() if u.find_spec(m) is None))"
    $missing = (& $VenvPy -c $probe).Trim()
    if ([string]::IsNullOrWhiteSpace($missing)) {
        Write-Host "  系统包已齐全，无需安装" -ForegroundColor Green
    } else {
        Write-Host "  缺失：$missing → 安装"
        & $VenvPy -m pip install ($missing.Split(" ")) -i $IndexUrl --progress-bar off
        if ($LASTEXITCODE -ne 0) { throw "补齐依赖失败；改用 -Isolated 或换镜像重试" }
    }
}

Write-Step "4/4 准备 FashionCLIP 视觉权重（可选）"
$Onnx = Join-Path $Root "models\fashion-clip\onnx\model.onnx"
if ($SkipModels) {
    Write-Host "  已跳过（-SkipModels）。缺失时自动回退哈希后端，功能仍可用" -ForegroundColor Yellow
} elseif (Test-Path $Onnx) {
    Write-Host ("  已存在：{0} MB" -f [math]::Round((Get-Item $Onnx).Length / 1MB, 1))
} else {
    Write-Host "  开始下载（约 606MB，hf-mirror，3–4 分钟）..."
    & $VenvPy (Join-Path $Root "scripts\fetch_fashionclip.py")
    if ($LASTEXITCODE -ne 0) { Write-Host "  下载失败：视觉编码回退哈希，不影响其他功能" -ForegroundColor Yellow }
}

Write-Step "自检"
$selfcheck = "import sys, importlib.util as u;" +
             "mods=['fastapi','uvicorn','pydantic','yaml','numpy','PIL','httpx','onnxruntime','pytest','scipy'];" +
             "print('  解释器 :', sys.executable);" +
             "print('  Python :', sys.version.split()[0]);" +
             "print('  缺失   :', [m for m in mods if u.find_spec(m) is None] or '无');" +
             "sys.path.insert(0, '.');" +
             "from src.towers import visual;" +
             "print('  视觉后端:', 'FashionCLIP 可用' if visual.available() else '未就绪（回退哈希，功能可用）')"
& $VenvPy -c $selfcheck

Write-Host @"

============================================================
环境就绪。激活方式（任选其一）：

  PowerShell:     .\.venv\Scripts\Activate.ps1
  cmd.exe:        .venv\Scripts\activate.bat
  Git Bash:       source .venv/Scripts/activate
  VS Code:        Ctrl+Shift+P -> Python: Select Interpreter -> 选 .venv
  不激活直接用:   .\.venv\Scripts\python.exe run.py --online

启动 / 测试：
  python run.py --online
  python -m pytest -q
  python eval\run_eval.py
============================================================
"@ -ForegroundColor Green
