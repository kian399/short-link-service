# 压测执行手册

两种跑法：想省事就用 `run_all_groups.py` 一把梭（A–F 六组自动切换开关、清缓存、抓指标）；
想一组一组盯着看，就照下面的执行卡手动来。手动跑一组大约八分钟（准备三分钟 + 压测三分钟 + 冷却两分钟）。

手动跑完记得把结果填进 `docs/benchmark.md`。

> 一个前提：压测必须关掉限流。跳转限流是按 IP 600 次/分钟，压测全从 `127.0.0.1` 打，
> 不关的话第 601 个请求开始全是 429，QPS 曲线会假性暴跌。
> 每组的开关里都带 `RATE_LIMIT_ENABLED='false'`，`run_all_groups.py` 也内置了。
> 想单独量限流本身的代价，用 F 组（限流打开、阈值拉到一亿，不会触发 429）。

---

## 0. 基线（A 组）直接用这个

A 组已经跑过三次，取稳定值：

```
A 组基线：QPS 630.8 / 中位 310ms / P95 370ms / P99 400ms / 错误率 0%
```

**说明**：第一轮 487、第二轮 566、第三轮 631，递增是因为 PostgreSQL 缓冲池预热（10 万行数据从磁盘读进内存）。只有第三轮是稳定值，前两轮不能用。

---

## 1. 每组都要做的准备（4 步，别跳）

```powershell
cd D:\short-link-service
```

**① 确认只有一个实例在跑**

```powershell
netstat -ano | findstr :8000
```

有输出就是上一组没退干净，先杀：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

**② 清空上一组的 Redis 缓存**（不清的话下一组白占便宜，数据不可比）

```powershell
docker compose exec redis redis-cli flushdb
```

**③ 清掉上一组的环境变量**（避免残留开关污染）

```powershell
Remove-Item Env:CACHE_L1_ENABLED, Env:CACHE_L2_ENABLED, Env:SINGLEFLIGHT_ENABLED, Env:STATS_RECORD_ENABLED, Env:RATE_LIMIT_ENABLED -ErrorAction SilentlyContinue
```

**④ 确认容器状态**

```powershell
docker compose ps
```

只需要 `postgres` 和 `redis` 是 Up，`app` 必须是停的（否则它抢 8000）。

---

## 2. 四组执行卡

### B 组：加 Redis（L2）

**目的**：验证「命中 Redis 就不查数据库」带来多少提升。这是缓存收益的主要来源。

**开关**

```powershell
$env:CACHE_L1_ENABLED='false'; $env:CACHE_L2_ENABLED='true'; $env:SINGLEFLIGHT_ENABLED='false'; $env:STATS_RECORD_ENABLED='false'; $env:RATE_LIMIT_ENABLED='false'
```

**起服务（窗口 1，占住不关）**

```powershell
uv run uvicorn app.main:app --port 8000
```

**预热 + 压测（窗口 2）**

```powershell
cd D:\short-link-service
uv run python bench/prewarm.py http://127.0.0.1:8000
uv run locust -f bench/loadtest.py --headless -u 200 -r 20 -t 3m --host http://127.0.0.1:8000 --csv bench/out/mine_B1
```

**预期**：QPS 630 → 800 左右，中位延迟 310ms → 250ms

---

### C 组：再加 L1 进程内缓存

**目的**：验证第二层缓存能再省多少——L1 命中时连 Redis 的网络往返都省掉。

**开关**

```powershell
$env:CACHE_L1_ENABLED='true'; $env:CACHE_L2_ENABLED='true'; $env:SINGLEFLIGHT_ENABLED='false'; $env:STATS_RECORD_ENABLED='false'; $env:RATE_LIMIT_ENABLED='false'
```

**起服务**

```powershell
uv run uvicorn app.main:app --port 8000
```

**预热 + 压测**

```powershell
uv run python bench/prewarm.py http://127.0.0.1:8000
uv run locust -f bench/loadtest.py --headless -u 200 -r 20 -t 3m --host http://127.0.0.1:8000 --csv bench/out/mine_C1
```

**预期**：QPS 800 → 850 左右，延迟只降 5–15ms

> 为什么提升不大：Redis 和你的服务在同一台机器，走 loopback 只要 0.1ms。L1 的价值在**跨机器部署**时才明显。



---

### D 组：加 singleflight

**目的**：**不是提吞吐**。它只削「缓存刚好失效瞬间大量并发回源」的尖峰，看 **P99 和方差**，别看 QPS。

**开关**

```powershell
$env:CACHE_L1_ENABLED='true'; $env:CACHE_L2_ENABLED='true'; $env:SINGLEFLIGHT_ENABLED='true'; $env:STATS_RECORD_ENABLED='false'; $env:RATE_LIMIT_ENABLED='false'
```

**起服务 + 压测**

```powershell
uv run uvicorn app.main:app --port 8000
```

```powershell
uv run python bench/prewarm.py http://127.0.0.1:8000
uv run locust -f bench/loadtest.py --headless -u 200 -r 20 -t 3m --host http://127.0.0.1:8000 --csv bench/out/mine_D1
```

**预期**：QPS 和 C 组基本一样（±5%），P99 略降或持平

> 如果 D 组和 C 组差不到 10%，**如实写「差异在测量噪声内」**，别硬说 singleflight 提升了性能。面试官反而会认可你的严谨。

---

### E 组：打开埋点

**目的**：量化「每次跳转多一次 Redis INCR」的成本。

**开关**

```powershell
$env:CACHE_L1_ENABLED='true'; $env:CACHE_L2_ENABLED='true'; $env:SINGLEFLIGHT_ENABLED='true'; $env:STATS_RECORD_ENABLED='true'; $env:RATE_LIMIT_ENABLED='false'
```

**起服务 + 压测**

```powershell
uv run uvicorn app.main:app --port 8000
```

```powershell
uv run python bench/prewarm.py http://127.0.0.1:8000
uv run locust -f bench/loadtest.py --headless -u 200 -r 20 -t 3m --host http://127.0.0.1:8000 --csv bench/out/mine_E1
```

**预期**：QPS 比 D 组低 5–10%，延迟高 10–20ms

---

### F 组：C 的配置 + 限流打开（阈值拉满）

**目的**：量化 M3 新加的限流在跳转路上花了多少——每次跳转多一次 Redis `EVAL`。
阈值设成 1 亿，保证不会触发 429，测到的只是「多一次脚本调用」的纯成本。

**开关**

```powershell
$env:CACHE_L1_ENABLED='true'; $env:CACHE_L2_ENABLED='true'; $env:SINGLEFLIGHT_ENABLED='false'; $env:STATS_RECORD_ENABLED='false'; $env:RATE_LIMIT_ENABLED='true'; $env:RATE_LIMIT_REDIRECT_PER_MIN='100000000'
```

**起服务 + 压测**

```powershell
uv run uvicorn app.main:app --port 8000
```

```powershell
uv run python bench/prewarm.py http://127.0.0.1:8000
uv run locust -f bench/loadtest.py --headless -u 200 -r 20 -t 3m --host http://127.0.0.1:8000 --csv bench/out/mine_F1
```

**预期**：QPS 比 C 组低 3–8%，P99 高 5–15ms。这个差值只能当量级参考，别当精确值，它和组间噪声是一个量级。

---

## 3. 每组跑两次，取中位数

单次结果波动 10–20%，只跑一次下不了结论。

把上面每组的 `_1` 改成 `_2` 再跑一遍，然后取两次的中位数。

**每组结束前先停服务**：

```powershell
# 窗口 1 按 Ctrl+C，然后确认端口释放
netstat -ano | findstr :8000
```

---

## 4. 记录表（边跑边填）

| 组 | 场景            | 第 1 次 QPS | 第 2 次 QPS | 中位 QPS | 中位延迟 | P95 | P99 | 错误率 |
| - | ------------- | --------- | --------- | ------ | ---- | --- | --- | --- |
| A | 纯数据库          | —         | —         | 630.8  | 310  | 370 | 400 | 0%  |
| B | +Redis        |           |           |        |      |     |     |     |
| C | +L1           |           |           |        |      |     |     |     |
| D | +singleflight |           |           |        |      |     |     |     |
| E | +埋点           |           |           |        |      |     |     |     |
| F | C+限流(EVAL)    |           |           |        |      |     |     |     |

每组的完整数据在 `bench/out/mine_X1_stats.csv`，用 Excel 打开看 `Requests/s`、`95%`、`99%` 三列。

---

## 5. 报告怎么写（填进 docs/benchmark.md）

`docs/benchmark.md` 的结构已经搭好了，跑完照着填这几块：

**① 环境**：机器配置（CPU 型号、核数、内存）

```powershell
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
```

**② 结果表**：把上面记录表的中位数填进去

**③ 瓶颈归因**（最重要的一段，占报告一半价值）

必须写到这个粒度：

> A 组平局延迟 310ms，但单次查询只要 15ms（Min 值），说明 295ms 是排队。  
> 查 `/metrics` 发现 `db_pool_size=5`、`db_pool_checked_out=15`（打满），  
> 瓶颈在 SQLAlchemy 连接池。调大到 20 后，A 组 QPS 从 487 提到 631。

**④ 结论**：一句话说清「缓存带来多少提升 + 当前瓶颈在哪」

---

## 6. 常见错误对照

| 现象                              | 原因                | 修法                                        |
| ------------------------------- | ----------------- | ----------------------------------------- |
| `10048` 端口被占                    | 上一组 uvicorn 没退    | `Get-NetTCPConnection -LocalPort 8000` 杀掉 |
| `--host: expected one argument` | 命令粘贴换行断了          | 写成一行                                      |
| QPS 越跑越低                        | 上一组缓存没清           | `redis-cli flushdb`                       |
| 所有组 QPS 差不多                     | 开关没生效（环境变量在启动时读取） | 改完必须重启 uvicorn                            |
| Min 特别小但平均很大                    | 正常，说明在排队          | 看连接池 / 并发数                                |
| 失败 100%、延迟 20 秒以上               | PG/Redis 没起来       | `docker compose ps`，看 app 日志里的 Connection refused |

---

## 7. 压测时别做的事

- 别开浏览器刷网页、别看视频（抢 CPU，数据会抖）
- 别同时跑 Docker Desktop 的文件同步
- 别在压测中途改代码（uvicorn 没 --reload，改了也不生效，但容易搞混）
