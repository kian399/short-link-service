# 面试演示脚本

> 用法：面试前 10 分钟照着跑一遍，确认每段输出都对。
> 每段都给「命令 → 应该看到什么 → 怎么讲」，话术可以直接说出口。

---

## 0. 演示前准备（3 分钟）

```powershell
cd D:\short-link-service

# 起生产形态（nginx + app + pg + redis）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

# 记下 Key，后面都要用
$K = (Select-String -Path .env.prod -Pattern '^API_KEY=').Line -replace 'API_KEY=',''
$B = "http://127.0.0.1:8080"
```

四个容器全 `Up` 才能开始。`app` 后面必须带 `(healthy)`。

**如果 502**：`docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx`

---

## 1. 服务活着 + 可观测

```powershell
curl.exe -s $B/healthz
curl.exe -s $B/metrics | Select-String "db_pool|l1_cache_size"
```

**应该看到**

```
{"status":"ok"}
db_pool_size 20
l1_cache_size 1
```

**话术**

> 整个服务用 Docker Compose 编排，nginx 对外，app、PostgreSQL、Redis 都在内网。
> 暴露了 `/healthz` 给探活，`/metrics` 是手动实现的 Prometheus 文本格式，
> 能看到连接池占用和 L1 缓存条目数——压测时靠它定位瓶颈。

---

## 2. 创建短链

```powershell
curl.exe -s -X POST $B/api/v1/links `
  -H "X-API-Key: $K" -H "Content-Type: application/json" `
  -d '{\"original_url\":\"https://github.com/kian399/short-link-service\",\"custom_alias\":\"demo-2026\"}'
```

**应该看到**：`201`，返回体里有 `"short_url":"http://127.0.0.1:8080/demo-2026"`

**话术**

> 短码是随机 7 位 Base62，62 的 7 次方大概 3.5 万亿，业务量级下碰撞可以忽略，
> 真正的并发兜底是数据库唯一索引加最多 3 次重试。相比发号器方案，随机码不可枚举，
> 也不需要额外维护一个发号服务。自定义别名走同一套接口，保留字有黑名单。

---

## 3. 缓存怎么生效（核心段）

```powershell
# 第一次访问：L1 和 Redis 都没有，必须回源查库
curl.exe -s -o NUL -w "冷启动 %{time_total}s`n" $B/demo-2026

# 看 Redis 里存了什么
docker exec short-link-service-redis-1 redis-cli get link:demo-2026
docker exec short-link-service-redis-1 redis-cli ttl link:demo-2026

# 再访问 10 次
1..10 | ForEach-Object { curl.exe -s -o NUL -w "%{time_total}s " $B/demo-2026 }
```

**应该看到**

```
{"u": "https://github.com/kian399/short-link-service", "e": null}
3597
```

**话术（这段最能体现深度，讲慢一点）**

> 缓存是三层：进程内 L1 30 秒、Redis 一小时、最后才是数据库。
> 缓存值存的是 JSON 而不是纯 URL，因为要把 `expires_at` 一起带上——
> 这样命中缓存就能判断过期，不用回查数据库。
>
> TTL 也不是固定值，取的是「配置的 3600 秒」和「距离过期还有多久」的较小值，
> 避免给一个马上就要过期的链接缓存一小时。
>
> 单次请求的延迟差异在本机不明显（回源 7ms、命中 4.7ms），因为 PostgreSQL 和
> 应用在同一台机器。真实收益在并发下：200 并发时数据库连接会排队，
> 压测里 QPS 从 631 提到 773，P99 从 400ms 降到 320ms。

---

## 4. 访问统计（数据链路）

```powershell
# 点几次
1..5 | ForEach-Object { curl.exe -s -o NUL $B/demo-2026 }

# 立刻查（还没落库）
curl.exe -s $B/api/v1/links/demo-2026/stats -H "X-API-Key: $K"
```

**应该看到**：`total_clicks` 和 `pending_today` 都包含刚点的次数，`daily` 里当天一行

**话术**

> 点击统计没有走「每次跳转 INSERT 一行」，那是写放大，QPS 根本上不去。
> 跳转路径只做一次 Redis 的 INCR，后台协程每 60 秒把计数批量 UPSERT 进日聚合表。
>
> 这里有个必须处理的细节：刚落库延迟最多 60 秒，用户点完立刻查是查不到的。
> 所以统计接口会同时读「已落库的日聚合」和「Redis 里还没落库的今日计数」，
> 用 `pending_today` 把这两部分区分开，用户看到的总数永远是对的。

---

## 5. 限流（安全边界）

```powershell
# 先看当前额度用到哪了
docker exec short-link-service-redis-1 redis-cli --scan --pattern "rl:redirect:*"

# 连打（会超限）
1..601 | ForEach-Object {
  $c = curl.exe -s -o NUL -w "%{http_code}" $B/demo-2026
  if ($_ -ge 598) { "$_ -> $c" }
}
```

**应该看到**：598/599/600 是 `302`，601 是 `429`

**话术**

> 限流是 Redis 固定窗口，跳转按客户端 IP 600 次每分钟，创建按 API Key 60 次。
>
> 关键在 INCR 和 EXPIRE 必须原子。如果分两条命令发，中间进程崩了就会留下一个
> 永不过期的计数键，那个 IP 从此永久被限流。所以用 Lua 脚本在 Redis 内部执行。
>
> 固定窗口的缺点我知道：窗口切换的瞬间可能放过接近两倍的突发。短链这种场景能接受，
> 生产要更平滑可以换滑动窗口或者令牌桶。超限返回 429 加 `Retry-After` 响应头，
> 客户端能自己知道多久后重试。

---

## 6. 软删除 + 数据保留

```powershell
curl.exe -s -o NUL -w "DELETE -> %{http_code}`n" -X DELETE $B/api/v1/links/demo-2026 -H "X-API-Key: $K"
curl.exe -s -o NUL -w "跳转 -> %{http_code}`n" $B/demo-2026
curl.exe -s $B/api/v1/links/demo-2026/stats -H "X-API-Key: $K"
```

**应该看到**：`204`、`404`、统计数据还在

**话术**

> 删除做的是软删除，置 `is_active=false`，跳转按 404 处理，但数据行和统计都保留。
>
> 原因是统计表挂了外键级联，物理删除会把历史点击一起带走。软删之后
> 报表还能查,只是这个链接不再对外服务。所有写操作——创建、删除、PATCH 修改——
> 都会主动删一次缓存键,不删的话用户点了还是跳老地址。

---

## 关键数字（背下来）

| 指标 | 数值 | 场景 |
|---|---|---|
| 接口数 | 8 个 REST 接口 | 建/查/改/删/列表/统计/跳转/指标 |
| 测试 | 67 个用例，覆盖率 90% | pytest + CI |
| QPS 基线（纯 DB） | 631 | 200 并发 3 分钟 |
| QPS 加 Redis | 773（+22%） | 同上 |
| QPS 4 worker | 1538 | 单进程 CPU 是瓶颈 |
| P99 对比 | 400ms → 320ms | 加缓存后 |
| 冷启动 vs 命中 | 7.0ms / 4.7ms | 本机 loopback |
| 限流 | 600/分钟（IP）、60/分钟（Key） | 429 + Retry-After |
| 缓存 TTL | L1 30s / Redis 3600s | 取剩余寿命的较小值 |

---

## 高频追问怎么答

**Q：为什么不用 prometheus_client 库？**
> 只需要 Counter/Gauge/Histogram 三种，自己实现 150 行就够，还能讲清文本格式的每一行。
> 引入库反而说不清 histogram 的 bucket 是怎么累加的。

**Q：L1 缓存多进程下怎么办？**
> 每个 worker 各存一份，删除链接时其他进程的 L1 不会立刻失效，最坏情况是多跳 30 秒旧地址。
> 这个我权衡过：跨进程广播（Redis pub/sub）能解决，但复杂度不值得——L1 的 TTL 只有 30 秒，
> 自愈很快。

**Q：singleflight 真的有用吗？**
> 在 80/20 的热点流量下测出来几乎没有差异，因为命中率太高，回源本身就少。
> 它真正的价值在缓存刚失效的瞬间——理论上能挡住上千个并发回源。
> 我的压测没造出这个场景，所以数据上不支撑，这条我在报告里标注了。

**Q：说说你压测踩的坑。**
> 最典型的一个：改完代码只跑了 `docker compose up` 没加 `--build`，镜像还是两周前的，
> 排查半天以为是代码问题。还有一次 100% 失败，`status 0`，是压测时服务根本没起来。
> 所以现在压测前一定先 `curl /healthz` 确认。
