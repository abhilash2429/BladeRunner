param([string]$File = "scratch_day1.txt")
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$gitBin = Join-Path (Split-Path (Split-Path (Get-Command git).Source)) "bin"
$env:PATH = "$gitBin;$env:PATH"
Set-Content $File 'day1'
git add $File
git commit -q -m "chore: day1 scratch file" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
& $env:OPENCODE_EXE run --format json -m amazon-bedrock/zai.glm-5 "Append the line 'checked' to $File and do nothing else." | Out-File -Encoding utf8 runs/raw/opencode_day1.jsonl
Write-Host "opencode exit: $LASTEXITCODE"
Get-Content $File
git add $File
git commit -q -m "chore: day1 opencode edit" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git log -1 --format=%B
entire checkpoint list
entire why "${File}:2"
