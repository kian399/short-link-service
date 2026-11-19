# 面试演示脚本

我自己用的流程：面试前十分钟照着跑一遍，确认每段都能出来东西。每段都写了「敲什么 → 应该看到什么 → 怎么说」。

跑完的真实输出存在 [demo-transcript.txt](demo-transcript.txt)，`bench/demo_transcript.ps1` 一键生成；
仓库根目录的 `docs/demo.gif` 就是拿这份记录渲染的（不是录屏，是终端样式的回放）。

## 0. 起服务

```powershell
cd D:\short-link-service
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

$K = (Select-String -Path .env.prod -Pattern '^API_KEY=').Line -replace 'API_KEY=',''
$B = "http://127.0.0.1:8080"
```

四个容器都要 Up，app 后面得带 `(healthy)`（Dockerfile 里的 HEALTHCHECK 打 /healthz）。

如果 502，先别怀疑代码，多半是 app 重建过、nginx 还在打旧 IP：

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx
```

## 1. 服务活着、能观测

```powershell
curl.exe -s $B/healthz
curl.exe -s $B/metrics | Select-String "db_pool|l1_cache_size"
```

应该看到：

```
{"status":"ok"}
db_pool_size 20
l1_cache_size 1
```

怎么说：

> 整个服务用 Compose 编排，对外只有 nginx，app、PG、Redis 都在内网。
> `/healthz` 给探活用，`/metrics` 是我手写的 Prometheus 文本格式，能看到连接池占用和 L1 条目数。
> 压测的时候就是靠这几行定位到连接池打满的。
>
> `/metrics` 现在只放行容器网段，公网访问是 403，线上不适合把这些指标裸奔出去。

## 2. 创建短链

```powershell
curl.exe -s -X POST $B/api/v1/links `
  -H "X-API-Key: $K" -H "Content-Type: application/json" `
  -d '{\"original_url\":\"https://github.com/kian399/short-link-service\",\"custom_alias\":\"demo-2026\"}'
```

返回 201，里面有 `"short_url":"http://127.0.0.1:8080/demo-2026"`。

怎么说：

> 短码随机 7 位 Base62，62 的 7 次方大概 3.5 万亿。真撞了靠数据库唯一索引兜，接口重试三次。
> 没做发号器，因为不想为了生成一个码再维护一个服务，随机码还顺带不可枚举。
> 自定义别名走同一个接口，保留字有黑名单。

## 3. 缓存怎么生效（这段是重点）

```powershell
# 第一次，L1 和 Redis 都没有，必须回源
curl.exe -s -o NUL -w "冷启动 %{time_total}s`n" $B/demo-2026

# Redis 里存了什么
docker compose -f docker-compose.prod.yml --env-file .env.prod exec redis redis-cli get link:demo-2026
docker compose -f docker-compose.prod.yml --env-file .env.prod exec redis redis-cli ttl link:demo-2026

# 再打十次
1..10 | ForEach-Object { curl.exe -s -o NUL -w "%{time_total}s " $B/demo-2026 }
```

应该看到类似：

```
{"u": "https://github.com/kian399/short-link-service", "e": null}
3597
```

怎么说（慢一点）：

> 缓存是三层：进程内 30 秒、Redis 一小时、最后才到数据库。
> 值存的是 JSON 不是纯 URL，因为要把 `expires_at` 一起带上，这样命中缓存就能判断过期。
> Redis 的 TTL 也不是固定一小时，取的是配置值和「离过期还剩多久」里小的那个，
> 不然给一条马上过期的链接缓存一小时，链接过期了缓存还在。
>
> 单次请求差得不多，本机回源 7ms、命中 4.7ms，因为 PG 和应用在一台机器上。
> 收益要看并发：200 并发时数据库连接是排队的，压测里 QPS 从 631 到 773，P99 从 400ms 到 320ms。

## 4. 访问统计

```powershell
1..5 | ForEach-Object { curl.exe -s -o NUL $B/demo-2026 }
curl.exe -s $B/api/v1/links/demo-2026/stats -H "X-API-Key: $K"
```

`total_clicks` 和 `pending_today` 都包含刚点的五次，`daily` 里当天一行。

怎么说：

> 跳转没走「每次 INSERT 一行」，那是写放大。跳转路径只做一次 Redis INCR，
> 后台协程每 60 秒批量 UPSERT 进日聚合表。
>
> 这里有个必须处理的细节：落库最多延迟 60 秒，用户点完立刻查会看不到。
> 所以统计接口同时读「已落库的」和「Redis 里还没落库的」，用 `pending_today` 区分开。

## 5. 限流

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.prod exec redis redis-cli --scan --pattern "rl:redirect:*"

1..601 | ForEach-Object {
  $c = curl.exe -s -o NUL -w "%{http_code}" $B/demo-2026
  if ($_ -ge 598) { "$_ -> $c" }
}
```

598、599、600 是 302，601 是 429。

怎么说：

> 限流是 Redis 固定窗口，跳转按 IP 600 次每分钟，写接口按 Key 60 次。
>
> 关键是 INCR 和 EXPIRE 必须原子。分两条命令发，中间进程崩了就会留一个永不过期的键，
> 那个 IP 从此永久被限流。所以用 Lua 在 Redis 里面执行。
>
> 固定窗口的边界问题我知道，窗口切换那一瞬间可能放过接近两倍突发。短链能接受，
> 要平滑就换滑动窗口或者令牌桶。超限返回 429 加 Retry-After，客户端知道多久后重试。

## 6. 软删除

```powershell
curl.exe -s -o NUL -w "DELETE -> %{http_code}`n" -X DELETE $B/api/v1/links/demo-2026 -H "X-API-Key: $K"
curl.exe -s -o NUL -w "跳转 -> %{http_code}`n" $B/demo-2026
curl.exe -s $B/api/v1/links/demo-2026/stats -H "X-API-Key: $K"
```

依次是 204、404、统计还在。

怎么说：

> 删除是软删，置 `is_active=false`，跳转按 404，数据行和统计都留着。
>
> 因为统计表挂了外键级联，物理删会把历史点击一起带走。软删之后报表还能查，
> 只是这个链接不再对外服务。所有写操作都会主动删一次缓存键，不删的话用户点了还是跳老地址。

## 7. 数字（背下来这几行）

| 指标 | 数值 |
| --- | --- |
| 接口 | 7 个 REST + /healthz + /metrics |
| 测试 | 72 个用例，覆盖率 90% |
| QPS 纯 DB | 631 |
| QPS 加 Redis | 773（回源 PG 从 593/秒降到 150/秒） |
| QPS 4 worker | 1538 |
| P99 | 400ms → 320ms |
| 冷启动 / 命中 | 7.0ms / 4.7ms（本机 loopback） |
| 限流 | 600/分钟（IP）、60/分钟（Key） |
| 缓存 TTL | L1 30s / Redis 3600s，取剩余寿命的较小值 |

## 8. 追问怎么接

问：为什么不用 prometheus_client？
> 我只需要 Counter、Gauge、Histogram 三种，自己写一百多行就够了。
> 而且手写一遍才知道 histogram 的 bucket 是怎么累积的，直接引库反而说不清。

问：多进程下 L1 不一致怎么办？
> 每个 worker 各存一份，删链接时别的进程不会立刻失效，最坏多跳 30 秒旧地址。
> 想过用 Redis pub/sub 广播，但为了 30 秒的窗口加一套订阅不值当。

问：singleflight 真有用吗？
> 常规流量下没用，我压测里 shared 计数是 0。它只在缓存刚失效那几毫秒有用，
> 我单独造了场景测，回源从 604 压到 60。报告里写的是这个，不是「提升了并发」。

问：压测踩过什么坑？
> 最气的一次是改完代码只跑 `docker compose up` 没加 `--build`，镜像还是两周前的，
> 排查了半天以为代码写错。还有一次 locust 跑出 100% 失败、status 0，
> 是服务根本没起来，三组数据全作废。现在压测前一定先 curl /healthz。

问：你自己找出过什么隐蔽的 bug？
> 两个。一个是先访问一个不存在的短码、再把它创建出来，60 秒内还是 404，
> 因为空值缓存的 miss 键没清，创建的时候补一次删缓存就好了。
> 另一个是多 worker 部署下点击统计会翻倍：每个 worker 都跑落库协程，
> 同一批计数各写一次，压测是单 worker 所以一直没暴露，后来用 Redis SET NX 加了把锁。
