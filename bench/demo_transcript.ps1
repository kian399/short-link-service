# 生成 docs/demo-transcript.txt：把真实跑一遍的输出按 `$ 命令` 的格式记下来。
# 用法（仓库根目录，生产栈已起）：powershell -ExecutionPolicy Bypass -File bench/demo_transcript.ps1
$ErrorActionPreference = "Stop"
$out = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\docs\demo-transcript.txt"))
"# 生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Set-Content -Encoding utf8 $out
"" | Add-Content -Encoding utf8 $out

$B = "http://127.0.0.1:8080"
$K = (Select-String -Path ".env.prod" -Pattern '^API_KEY=').Line -replace 'API_KEY=', ''
$alias = "demo" + (Get-Random -Minimum 100000 -Maximum 999999)
$compose = "docker compose -f docker-compose.prod.yml --env-file .env.prod"

function Log($cmd, $text) {
    "`$ $cmd" | Add-Content -Encoding utf8 $out
    $text | Add-Content -Encoding utf8 $out
    "" | Add-Content -Encoding utf8 $out
}

Log "$compose ps" (& docker compose -f docker-compose.prod.yml --env-file .env.prod ps --format "{{.Name}}  {{.Status}}")
Log "curl -s $B/healthz" (& curl.exe -s "$B/healthz")
Log "curl -s $B/metrics | grep -E 'db_pool|l1_cache'" (& curl.exe -s "$B/metrics" | Select-String "^db_pool_|^l1_cache_size")

$payload = Join-Path $env:TEMP "create_link.json"
$json = [ordered]@{
    original_url = "https://github.com/kian399/short-link-service"
    custom_alias = $alias
} | ConvertTo-Json -Compress
[IO.File]::WriteAllText($payload, $json, [Text.UTF8Encoding]::new($false))
Log "curl -X POST $B/api/v1/links -H 'X-API-Key: ***' -d '@create.json'" (& curl.exe -s -X POST "$B/api/v1/links" -H "X-API-Key: $K" -H "Content-Type: application/json" -d "@$payload")

Log "curl -o NUL -w '%{http_code} %{time_total}s' $B/$alias   # 第一次，L1/L2 都空，回源" (& curl.exe -s -o NUL -w "HTTP %{http_code}  %{time_total}s" "$B/$alias")
$times = 1..8 | % { [double](& curl.exe -s -o NUL -w "%{time_total}" "$B/$alias") }
$median = ($times | Sort-Object)[[int]($times.Count / 2)]
Log "for i in 1..8; time curl $B/$alias   # 之后 8 次，看中位数" ("HTTP 302  中位 {0:N1}ms（8 次：{1}）" -f ($median * 1000), (($times | % { "{0:N1}" -f ($_ * 1000) }) -join " / "))
Log "$compose exec redis redis-cli get link:$alias" (& docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T redis redis-cli get "link:$alias")
Log "$compose exec redis redis-cli ttl link:$alias" ("剩余 TTL " + (& docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T redis redis-cli ttl "link:$alias") + " 秒")

1..5 | % { curl.exe -s -o NUL "$B/$alias" }
Log "for i in 1..5; do curl -s -o /dev/null $B/$alias; done" ("点了 5 次")
Log "curl -s $B/api/v1/links/$alias/stats -H 'X-API-Key: ***'" (& curl.exe -s "$B/api/v1/links/$alias/stats" -H "X-API-Key: $K" | uv run python -m json.tool --no-ensure-ascii)

# 限流：先把窗口计数清掉，再连打 601 次，看第 601 个变 429
& docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T redis sh -c "redis-cli --scan --pattern 'rl:redirect:*' | xargs -r redis-cli del" | Out-Null
$probe = Join-Path $env:TEMP "ratelimit_probe.py"
$rate = @"
import requests
s = requests.Session()
s.trust_env = False
rows = []
for i in range(1, 602):
    r = s.get('$B/$alias', allow_redirects=False)
    if i >= 598:
        rows.append(f'第 {i} 次 -> {r.status_code}  Retry-After={r.headers.get("Retry-After", "-")}')
print('\n'.join(rows))
"@
[IO.File]::WriteAllText($probe, $rate, [Text.UTF8Encoding]::new($false))
Log "for i in 1..601: GET /$alias    # 第 598~601 次" (uv run python $probe)

# 清掉限流计数，免得删链接那步也被 429 挡住
& docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T redis sh -c "redis-cli --scan --pattern 'rl:redirect:*' | xargs -r redis-cli del" | Out-Null
Log "curl -X DELETE $B/api/v1/links/$alias -H 'X-API-Key: ***'" (& curl.exe -s -o NUL -w "HTTP %{http_code}" -X DELETE "$B/api/v1/links/$alias" -H "X-API-Key: $K")
Log "curl -o NUL -w '%{http_code}' $B/$alias   # 删完再跳" (& curl.exe -s -o NUL -w "HTTP %{http_code}" "$B/$alias")
Log "curl -s $B/api/v1/links/$alias/stats   # 统计还在" (& curl.exe -s "$B/api/v1/links/$alias/stats" -H "X-API-Key: $K" | uv run python -m json.tool --no-ensure-ascii)

$composePsql = "$compose exec postgres psql -U shortlink -d shortlink"
Log "$composePsql -c 'select ...'" (& docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T postgres psql -U shortlink -d shortlink -c "select (select count(*) from links) as links, (select count(*) from link_stats_daily) as stat_days" | Select-Object -First 4)

Write-Output "transcript -> $out（alias=$alias）"
