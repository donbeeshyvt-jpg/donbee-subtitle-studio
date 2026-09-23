# 與 Bash hook 相同：核對 Git 暫存內容及公開索引，不提交私有紀錄。
$ErrorActionPreference = 'Stop'
$releasePython = if (Test-Path '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }
& $releasePython scripts/check-public-release.py --require-code-map
exit $LASTEXITCODE
