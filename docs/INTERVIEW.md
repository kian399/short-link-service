# 面试准备：短链服务 + 网络层补课

> 这份文档只服务一件事：面试官问起来，你能顺着讲下去，而不是背结论。
> 所有性能数字来自 `docs/benchmark.md`，讲的时候别改口径；工程细节都能在对应的文件里指出来。
> 标了「没实测」的地方，就是没数据，别硬编。

---

## 1. 项目两分钟自述

背诵版，语速正常大概两分钟，中间可以停一下让对方插话：

> 我做的是一个短链接服务。往 `POST /api/v1/links` 传一个长 URL，返回一个 7 位短码，浏览器访问 `/{code}` 会 302 跳回原地址。
> 技术栈是 FastAPI + PostgreSQL 16 + Redis 7，ORM 用 SQLAlchemy 2，迁移用 Alembic，Docker Compose 编排。生产形态是 nginx 做反向代理，后面 4 个 uvicorn worker，只有 nginx 暴露端口，压测用 locust，测试用 pytest 加 fakeredis。
> 量级是这样：数据库里 10 万条短链，压测 200 并发、按 80/20 的热点分布打。单机单 worker 稳定在 700 QPS 量级，P99 在 300 到 400 毫秒，错误率 0；开 4 个 worker 大概 1.45k QPS。测试 72 个用例，覆盖率 90%。
> 我最想讲三个点。第一个是读路径的分层缓存和回源保护：L1 进程内、L2 Redis、空值缓存、singleflight，效果是回源数据库从每秒 593 次降到 150 次。第二个是点击统计的旁路写法：跳转路上只做一次 Redis INCR，60 秒批量落库，用「快照、写库、扣减」保证不丢数，中间踩过 GETDEL 把一批点击吃掉的坑。第三个是定位瓶颈的方法：我先看 `/metrics` 里连接池的 `checked_out`，确认 200 并发下的 300 多毫秒里绝大部分是排队，不是数据库慢，然后才去调连接池和进程模型。

三个技术点建议按这个顺序展开，第三个最能体现你不是只会加缓存。

如果只给你 30 秒：

> 一个 FastAPI 写的短链服务，10 万条数据、200 并发下单机 700 QPS、P99 三百多毫秒。核心是两级缓存加回源保护，把数据库读从每秒 593 次压到 150 次；点击统计走 Redis 旁路，不阻塞跳转也不丢数。我用 Prometheus 指标定位到真正的瓶颈是线程池排队，不是数据库。

讲完补一句口径，防止被误读：700 QPS 是单机 loopback 压测的服务端吞吐，不是生产容量，也不是「能扛 700 个用户」；P99 说 300 到 400 毫秒，平均延迟是 255 到 325 毫秒，这两个别混；覆盖率 91%，不说「全覆盖」，测试能跑起来靠 fakeredis 和事务回滚。

---

## 2. 项目必被问的 10 个问题

### Q1 短码怎么生成的，为什么不用发号器，碰撞怎么办

面试官想听什么：你有没有真的算过碰撞，而不是抄一句「唯一索引兜底」。

怎么答：
短码在 `app/services/shortener.py`，用 `secrets.choice` 从 62 个字符里随机取 7 位，不是递增的。62 的 7 次方大概 3.5 万亿，10 万条数据下按生日问题粗算碰撞概率在千分之一量级，但我不会拿概率当保证，真正的兜底是 `links.code` 上的唯一索引。

`app/api/v1/links.py` 里插入撞了 `IntegrityError` 就 rollback 换一个码重试，`MAX_CODE_ATTEMPTS = 3`，三次都不行返回 409。自定义别名会先查一次给出友好的 409，但并发下还是靠唯一索引，那次查询只是为了用户体验。

为什么不用发号器：文件里其实留了 `base62_encode`，但我没用它。发号器的好处是绝不碰撞、码可以更短，代价是多一个依赖和一个单点，还要处理 Redis 重启后号段回退。更关键的是短链的核心风险是被枚举，连续的发号能被爬虫按顺序把全部链接走一遍，随机码天然不可枚举。

可能的追问：
1. 4 位短码行不行？不行，62 的 4 次方只有 1477 万，10 万条就必然大量碰撞，只能发号器或者加长。
2. 碰撞重试会不会拖慢创建？只有极低概率进重试，创建也不是热点路径，压测打的都是跳转。

### Q2 缓存为什么用 Cache-Aside，为什么先写库再删缓存，为什么不双写

面试官想听什么：你知道写路径的顺序风险，不是背「Cache-Aside 最常用」。

怎么答：
读路径是标准的 Cache-Aside：先查 L1，没有查 Redis，都没有再回源 PG，然后同时回填 L1 和 L2，代码在 `app/services/cache.py` 的 `get_cached_link` 和 `set_cached_link`。写路径在 `app/api/v1/links.py`，PATCH 和 DELETE 都是先 `db.commit()`，再 `delete_cached_link(code)`。

为什么先写库再删缓存：反过来的话，删完缓存到写库提交之间有窗口，读请求会把旧值重新加载进缓存，写库完成之后缓存里一直是旧值，直到 TTL 到期。先写库再删，最坏情况是删缓存失败、旧值留到 TTL，窗口小得多。

为什么不双写：双写要先决定写库还是写缓存，两个并发写到达的顺序和数据库提交的顺序可能相反，缓存里留下旧值且很难自愈；更新缓存还要构造完整的值（url 加 expires_at），删一个 key 简单得多，还能顺手把 L1 一起清掉。代价是写后第一次读会 miss，短链读多写极少，可以接受。

可能的追问：
1. 删缓存失败了怎么办？现在没有重试也没有补偿，只有 TTL 兜底，而 Redis TTL 是 3600 秒，太长了。要改就是删失败进重试队列，或者把 TTL 降到分钟级，或者用 binlog 订阅异步失效。这个我没做。
2. 延迟双删了解吗？了解，它是「删、写库、延迟再删」，解决主从延迟下的脏读。我们单库没有主从，收益不大，还要引入延时任务，所以没上。

### Q3 L1 和 L2 的作用分别是什么，多进程下 L1 不一致怎么处理

面试官想听什么：你知道两层缓存各自解决什么，也敢承认 L1 的一致性是有损的。

怎么答：
L1 是进程内的 `TTLCache`，1 万条、30 秒，用 `OrderedDict` 做 LRU，`threading.Lock` 保线程安全，代码在 `app/services/l1_cache.py`。它的价值是省掉一次 Redis 网络往返，命中时连序列化都省了。L2 是 Redis，TTL 一小时，跨进程共享，进程重启后靠它快速恢复，生产 compose 里开了 AOF。

数据上 L1 命中率 61%，Redis 调用量从每轮 13.4 万次降到 5.2 万次，但 QPS 几乎没变，因为瓶颈是线程池排队不是网络。所以我讲的时候会说：L1 是卸载下游的，不是提吞吐的，它真正的价值在跨机器部署之后。

多进程的不一致：4 个 worker 各有一份 L1，DELETE 或 PATCH 只把处理这次请求的那个进程的 L1 删掉了，另外 3 个 worker 最多 30 秒内还会跳旧地址。我是有意接受这个不一致的，302 容忍秒级不一致，L1 的 TTL 只有 30 秒，自愈很快。要更强一致有三条路：Redis pub/sub 广播失效、值里带版本号在命中时校验、或者干脆关掉 L1 只留 L2。我选「短 TTL 自愈」，因为另外两条会把复杂度和一次网络往返加回来。

可能的追问：
1. L1 会不会把内存吃爆？`maxsize` 10000，超了 `popitem(last=False)` 淘汰最旧的，单条几百字节，量级几 MB，精确占用我没实测。
2. 为什么不用 cachetools？只要 get/set/delete 加容量上限，40 行就够，而且自己写能讲清锁的边界。

### Q4 空值缓存解决什么，TTL 为什么是 60 秒

面试官想听什么：你知道缓存穿透的触发条件和负信息的代价。

怎么答：
解决穿透。不存在的短码，比如爬虫扫 `/aaaaaaa`，每次都会穿过两层缓存打到 PG，加一层空值占位就能挡住。实现是 `app/services/cache.py` 里的 `MISS_MARKER = "__MISS__"`，L1 里存 `{"miss": True}`，而且 `set_missing` 显式传 `ttl=MISS_TTL`，因为 60 秒比 L1 默认的 30 秒长，不显式传会被 L1 提前淘汰。

TTL 为什么是 60 秒：空值缓存是负信息，它可能是错的，因为短码可能下一秒就被创建。TTL 越长挡扫描越好，但新码越晚可用。60 秒是「挡扫描」和「新码尽快生效」的折中，而且它比 L1 的 30 秒长，能让两层的节奏大致对齐。

可能的追问：
1. 创建新码会不会被 miss 挡住？会，这是我写这份面试准备时才发现的一个真实小坑：`create_link` 成功后没有删 `link:{code}` 的 miss 键，所以先访问了不存在的 `/abc`、再创建别名 `abc`，60 秒内跳转还是 404。现在在 `_insert` 里补了一次 `delete_cached_link`，`test_create_after_miss_clears_negative_cache` 锁住了这个行为。面试讲这条挺加分，因为它说明我不是只测正常路径。
2. 为什么不上布隆过滤器？没做。标准布隆不能删除，软删除的码会永远「可能存在」，要用计数布隆或者定期重建，我现在的空值缓存是它的简化版。

### Q5 singleflight 在你项目里到底有没有用

面试官想听什么：你敢不敢说「常规流量下没用」，以及你能不能造出它有用的场景。

怎么答：
常规流量下没用。D 组的计数器是 `singleflight_total{result="leader_ok"} 25350`、`shared` 一条都没有，200 并发跑 3 分钟一次合并都没发生。原因是 80/20 流量下每个冷码的未命中窗口只有几毫秒，200 个并发撞上同一个冷码的概率极低。这一条我在 `docs/benchmark.md` 里写明了，没硬说它提了性能。

它有用是我单独造场景测出来的：`bench/stampede.py` 钉死一个码、L1 TTL 压到 1 秒、关掉 L2，200 并发跑 60 秒，关单飞回源 604 次，开单飞 60 次，合并 677 次，回源降了 89.9%。所以它是防击穿的保险，不是提吞吐的优化，平时零收益甚至多一次锁开销。

实现上 `app/services/singleflight.py` 是一个 dict 加 `threading.Event`：同一个 key 第一个线程当 leader 去执行，其余 `wait()`，异常原样抛给所有等待者，`finally` 里 pop 并 `set()`。同步实现是因为路由是 `def`，跑在 anyio 线程池里。

可能的追问：
1. 多进程下还有效吗？这是它最大的局限：锁在进程内，4 个 worker 各放一个 leader，最坏 4 个并发回源。要跨进程得用 Redis SETNX 做分布式锁，代价是每次 miss 多一次往返。我没做。
2. leader 卡住了等待者怎么办？现在没有超时，等待者会一直等，最后被 nginx 的 `proxy_read_timeout 30s` 砍掉。正确写法是 `event.wait(timeout=...)`，超时自己去回源。这是个明确的待改点。

### Q6 点击统计为什么不直接 INSERT，丢数据怎么办（GETDEL 踩坑）

面试官想听什么：你知道埋点为什么必须旁路，以及「原子取出」不等于「安全处理」。

怎么答：
两个原因。第一，最早的埋点写在回源函数 `_load_from_db` 里，缓存命中根本不走那里，实测 20 次点击只记 1 条；第二，未命中时每次跳转都要 INSERT 加 COMMIT，写放大直接把跳转拖死。

现在跳转路径上只做一次 Redis pipeline：`INCR` 加 `EXPIRE` 3 天，代码在 `app/services/stats.py` 的 `record_click`，失败只打 warning，不影响跳转。后台 `app/main.py` 的 `_stats_flush_loop` 每 60 秒跑一次 `flush_once`，用一条 `INSERT ... SELECT ... ON CONFLICT DO UPDATE SET clicks = clicks + EXCLUDED.clicks` 同时完成 code 到 link_id 的转换和累加。

GETDEL 的坑：第一版是 GETDEL 一把取走再写库。GETDEL 是原子的，但原子地取出不等于安全地处理，后面 INSERT 一失败（当时表还没建），这批点击就永久蒸发了，实测丢了 20 次。现在改成三步：`snapshot_counts` 只 GET 不删，写库并 commit，`ack_counts` 用 DECRBY 只减掉已落库的那部分，减到 0 才删键。写库失败计数器原样保留，下一轮重试；落库期间新来的点击也不会被误删。测试 `test_flush_failure_keeps_counter` 用 monkeypatch 让 execute 抛异常，锁死了这个行为。

查询侧也要补：`read_stats` 把已落库的日聚合和 Redis 里今日未落库的计数合起来，返回 `pending_today`，用户点完立刻查也能看到数，不然「刚点的几下降查不到」是埋点旁路最常见的坑。

可能的追问：
1. 到底会不会丢？Redis 挂了这段时间的点击丢，但跳转不受影响；进程在 INCR 之后崩，计数在 Redis 里不丢；flush 写库失败会重试。真正的边界是「可能重复」：ack 的 DECRBY 如果失败，下一轮会把同一批再累加一次。
2. 多 worker 同时 flush 呢？这个原来真是个 bug：每个 worker 都有自己的 flush 协程，两个进程读到同一批计数、各 UPSERT 一次，点击数直接翻倍。压测是单 worker，所以一直没暴露，是写面试准备时顺出来的。现在加了一把 Redis 落库锁（`SET NX EX`），抢不到锁的进程跳过这一轮，锁 TTL 55 秒，进程崩了也会自己过期，`test_flush_skipped_when_another_worker_holds_lock` 覆盖了这条。剩下的边界是 ack 失败会重复累加一次，这个只能靠「宁可多记不可少记」的取舍兜着。

### Q7 限流为什么用固定窗口，Lua 解决什么，窗口边界问题

面试官想听什么：你知道原子性为什么必须靠脚本，也知道固定窗口的漏洞。

怎么答：
限流在 `app/middleware/ratelimit.py`，跳转按客户端 IP 600 次每分钟，创建按 API Key 60 次每分钟，超了返回 429 加 `Retry-After`。

Lua 解决的是 INCR 和 EXPIRE 之间的原子性。分两条命令发，中间进程崩了或者连接断了，会留下一个永不过期的计数键，那个 IP 从此永久被限流。`_LUA_INCR_EXPIRE` 在 Redis 内部一次执行完，要么都成功要么都不执行。`Retry-After` 取的是 key 的 `ttl`，也就是当前窗口还剩多少秒。

为什么用固定窗口：一个 key、一个脚本，实现和排查都简单，短链这种防刷场景不需要令牌桶那么平滑。边界问题我知道：第 59 秒打满 600 次、第 61 秒再打满 600 次，两秒内实际放过 1200 次，接近两倍突发。要平滑就换滑动窗口或者令牌桶，代价是内存和复杂度。

还有个顺序细节：`limit_create` 依赖 `verify_api_key`，先验 Key 再限流，反过来未鉴权的请求会共用一个 unknown 桶，还会把 401 变成 429。

可能的追问：
1. Redis 挂了会怎样？现在 `hit()` 没兜异常，会直接 500，等于限流组件把跳转打挂了。应该 fail-open：记日志放行，或者本地内存兜底计数。这是我没处理的可用性问题。
2. 多 worker 下是单机限流吗？计数在 Redis 里，多个 worker 共享同一个桶，比单机强；但它依赖这一个 Redis 实例，也没有按用户分级的配额。

### Q8 软删除 vs 物理删除，统计怎么保留

面试官想听什么：你知道删除语义会影响外键、统计和唯一索引。

怎么答：
DELETE 只把 `is_active` 置 false，所有读路径都带 `is_active.is_(True)` 过滤，跳转就变成 404。原因是 `link_stats_daily` 的外键是 `ondelete="CASCADE"`，物理删除会把历史点击记录一起带走，而删链接不该顺手删报表。软删之后统计接口还能查到历史数据，只是这个链接不再对外服务。写操作也会主动删缓存，PATCH 和 DELETE 都在 commit 之后调 `delete_cached_link`，不然用户点了还是跳老地址。

代价也要说：行会一直留着，表会变大；唯一索引还在，删掉的别名不能被重新创建，除非改成带条件的部分唯一索引；列表接口必须永远带过滤条件，漏一个地方就会把已删除的链接露出来。

可能的追问：
1. 合规要求真删呢？那就软删加定期归档或物理清理，或者先把统计和链接解耦，统计只存 code 不存外键，物理删链接时统计还能留下。
2. 软删的链接还能被缓存命中吗？不会，DELETE 会同时清 L1 和 Redis，下一次回源就查不到 `is_active` 的行。

### Q9 压测数据可信度（闭环模型、组间噪声、计数器 vs QPS）

面试官想听什么：你会不会把噪声当结论。

怎么答：
先承认边界：这是本机 loopback 压测，不是云上容量测试，数字只用于组间对比。locust 的 200 个用户是闭环模型，每个用户等响应回来才发下一个请求，所以 QPS 约等于用户数除以平均延迟，A 组 200/0.325 约 615，实测 566 到 631，对得上。这意味着「QPS 提升」和「延迟下降」在这套数据里是同一件事的两种写法，不能当两个独立结论。

噪声有多大：同一份配置重复跑差 10% 到 12%，所以小于 15% 的差异我一律不当结论，D 组 678 比 C 组 719 低就属于这种，我不会说 singleflight 拖慢了。五组是顺序跑的，越往后机器越热、后跑的更快，正确做法是交错执行取均值。

最硬的证据是计数器：回源 PG 从每秒 593 次降到 150 次，降 74.7%，L2 命中率 80%，这是应用内计数器，不吃机器噪声。还有个坑值得讲：早期环境里有个 `HTTP_PROXY=127.0.0.1:56475`，locust 把 `127.0.0.1:8000` 也交给代理转发，吞吐被吃一半，代理一抖就是成批 `status 0`，那批数据整体作废重跑了；现在脚本注入 `NO_PROXY`，`bench/loadtest.py` 里设了 `trust_env=False`。

可能的追问：
1. 为什么 4 worker 只有 2 倍？200 个闭环用户已经被榨干，P50 71ms，理论上限是 200 除以 0.071 约 2800 QPS，瓶颈转到压测机自己。要测真实上限得把 locust 放到另一台机器，或者改恒定速率模式。这一点我没实测，是按闭环模型推的。
2. 为什么不用均匀随机打 10 万条？L1 只有 1 万容量，均匀打命中率上限就是 10%，缓存价值被容量稀释，测出来没意义。真实短链流量是幂律的，80/20 才是要测的场景。

### Q10 如果 QPS 再翻十倍，你先改哪里

面试官想听什么：你有没有容量意识，动手顺序是不是按瓶颈排的，而不是「缺啥加啥」。

怎么答：
结论先说：先加 worker 和改异步，再挡回源，最后才是加机器和拆库。理由是现在单 worker 700 QPS 是被 40 个 anyio 线程和 30 个数据库连接卡住的，不是 PG 或 Redis 到顶了，回源只有每秒 150 次、L2 命中 80%，两边余量都很大。第一步把路由从 `def` 改成 `async def`，配 asyncpg 和 redis 异步客户端，把线程池这道队去掉；第二步加 worker，已验证 4 worker 到 1.45k、P50 从 250ms 降到 71ms，但这时压测机自己也到顶了，得先用另一台机器压出真实上限；第三步给长尾码加布隆，把「一定不存在」挡在 PG 之前；第四步才是 Redis 主从和 PG 只读副本。

容量怎么估：QPS 乘平均延迟大约等于需要的并发，7k QPS 乘 50ms 就是 350 并发，按现在 4 worker 1.45k 的水平，大概要 20 个 worker 或者几台机器；只要回源还能压在每秒一百多次，数据库就不用动。

可能的追问：
1. 为什么不先上 Redis 集群？现在 Redis 一次 GET 在本机是 0.1ms，量级根本不是瓶颈，先加只会多一套运维成本。
2. 数据量涨到一亿条呢？那是另一个问题：索引和分表。短码是随机的，范围查询用不上，只能按 code 哈希分片，缓存也要跟着分片。这个规模我没做过，只能说到思路。

---

## 3. 网络层补课

这一节按「一句话原理、在项目里的体现、面试怎么答」来写，能对上代码的就指文件，对不上的直接说没实测。

### 3.1 一次 HTTP 请求经过哪些层

以「用户在手机浏览器里打开一条短链」为例，表里的耗时是量级，不是精确值：

| 阶段 | 本机 loopback | 公网典型 | 容易出问题的地方 |
|---|---|---|---|
| DNS 解析 | 0，系统缓存直接命中 | 首次 10~80ms，之后按 TTL 缓存 | 记录不存在、TTL 太长换 IP 不生效 |
| TCP 三次握手 | 几十微秒 | 1 个 RTT，同城 1~5ms，跨省 20~40ms | SYN 被丢、backlog 满、防火墙 |
| TLS 1.3 握手 | 项目没启用 | 新建多 1 个 RTT，复用可 0-RTT | 证书过期、SNI、握手 CPU |
| HTTP 请求发出 | 忽略 | 1 个 RTT 起 | 请求头过大、HTTP/1.1 队头阻塞 |
| nginx 反向代理 | 0.1~0.5ms | 同上，加上到 upstream 的复用 | upstream IP 缓存、超时、buffer |
| 应用处理 | 无竞争 7.5ms，P50 240~340ms | 同左 | 线程池排队、连接池排队、慢 SQL |
| 响应返回 | 0.1ms，302 只有几百字节 | 1 个 RTT 加带宽 | gzip 对小响应无收益、缓冲 |
| 四次挥手 | 几十微秒 | 1~2 个 RTT | TIME_WAIT 堆积、本地端口耗尽 |

对着这张表讲，很容易把话题引到你熟的地方：302 只有几百字节，所以带宽和 gzip 不是瓶颈；真正贵的是「首字节之前」的 DNS、握手、TLS，和「进入应用之后」的排队。`nginx/nginx.conf` 只给 JSON 开 gzip、跳转路径 `proxy_buffering off`，就是按这个判断写的。

### 3.2 TCP 三次握手与四次挥手，为什么是三次不是两次

一句话原理：三次握手是 SYN、SYN+ACK、ACK，用来确认双方能收能发并同步初始序列号；四次挥手是因为 TCP 全双工，两个方向要各自关闭，主动关闭方最后进 TIME_WAIT。

在项目里的体现：本机压测走 loopback，握手只有几十微秒；但真实用户打开短链时，TCP 握手是首字节延迟的大头。这也是 nginx 到 app 之间要配 `keepalive 32` 的原因，那一段是 700 QPS 下唯一被高频重复握手的地方。

为什么不是两次：两次只能证明「客户端能发、服务端能收」，证明不了反方向；更实际的是网络里滞留的旧 SYN 会让服务端建立一个客户端根本不认的连接，三次握手的最后一个 ACK 让客户端有机会否决它。所以三次是「确认双向可达加防止历史连接」的最小次数。

面试怎么答：先说功能，再说两次为什么不够，最后落到项目：短链场景里握手比业务逻辑贵，所以反代那一段必须复用连接。

### 3.3 TIME_WAIT 是什么，压测时为什么堆一堆，SO_REUSEADDR 干嘛的

一句话原理：主动关闭方发完最后一个 ACK 后要等 2MSL（Linux 上固定 60 秒），让丢掉的 ACK 还能重传，同时让这条连接的重复包在网络里消失；它占的是四元组里的本地端口。

在项目里的体现：nginx 到 app 是长连接，那一段不会每个请求都堆 TIME_WAIT；压力大的是「谁主动关」。压测直连 uvicorn 时，主动关闭方取决于客户端连接池有没有复用。项目里我只用 `netstat` 查过端口占用（`docs/troubleshooting.md` 第 2 条），没统计过 TIME_WAIT 数量，这一段只能按原理说，算没实测。

SO_REUSEADDR 解决的是「服务重启时端口还在 TIME_WAIT，bind 报 Address already in use」，它允许 bind 一个还有 TIME_WAIT 残留的端口。它不是「两个进程同时监听同一端口」，那是 SO_REUSEPORT。uvicorn 默认会设 SO_REUSEADDR，所以改完代码重启不会卡 60 秒。

面试怎么答：TIME_WAIT 是主动关闭方的正常状态，不是 bug；量大了先看是不是每个请求都新建连接，能复用就复用；`tcp_tw_reuse` 只对出向连接有效，别乱开 `tcp_tw_recycle`。补一句「我没在压测里量化过 TIME_WAIT 数量」。

### 3.4 backlog 与 accept 队列满了会怎样

一句话原理：`listen(fd, backlog)` 背后有两个队列，SYN 队列放收到 SYN、还没完成握手的连接，accept 队列放已完成握手、等应用 `accept()` 的连接；accept 队列满了，Linux 默认丢弃 ACK，客户端重传，持续满就表现为连接超时或偶发失败。

在项目里的体现：`nginx/nginx.conf` 里 `proxy_connect_timeout 5s`，连不上 upstream 5 秒就放弃，返回 502。uvicorn 的 listen backlog 默认 2048，对我们这个量级够用，我没调过也没测过，这一块属于「知道原理但没有实践数据」。

和 502 的关系：accept 队列满时 nginx 连 upstream 会超时或被拒，error.log 里是 `connect() timed out` 或 `Connection refused`，前者现象更像 504，后者是 502，所以一定要看错误文本再判断，别只看状态码。

面试怎么答：先说两个队列的区别（半连接和全连接），再说满了的表现是「握手看起来成功但应用没接住」，最后说排查要看 `ss -lnt` 的 Send-Q 和 nginx error.log。

### 3.5 HTTP/1.1 keep-alive，nginx `keepalive 32` 复用了什么，队头阻塞

一句话原理：HTTP/1.1 默认长连接，一个 TCP 连接上串行发多个请求，省掉握手和慢启动；`upstream` 块里的 `keepalive 32` 是 nginx worker 到上游之间保留的空闲长连接数，不是客户端连接数，也不是并发上限。

在项目里的体现：`nginx/nginx.conf` 里三行是配套的，`keepalive 32`、`proxy_http_version 1.1`、`proxy_set_header Connection ""`。少任何一行都不生效：不清掉 `Connection`，nginx 会给上游发 `Connection: close`，每个请求新建连接，`keepalive` 白配。官方镜像默认 `worker_processes auto`，所以实际是 32 乘 worker 数。

队头阻塞：同一条 HTTP/1.1 连接上请求必须串行，前一个响应没回来后面的只能等。所以 nginx 用 32 条连接并行，而不是一条连接打天下；这也是单条长连接解决不了并发的原因。

面试怎么答：把「客户端到 nginx」和「nginx 到上游」分成两段讲，前者归 `keepalive_timeout`，后者才是 `upstream keepalive`；再说 HTTP/1.1 的队头阻塞是应用层的，靠多连接绕。

### 3.6 HTTP/2 多路复用解决了什么，为什么项目还是 HTTP/1.1

一句话原理：HTTP/2 用二进制分帧加 stream id，一条 TCP 连接上并发多个请求，头部用 HPACK 压缩，解决了 HTTP/1.1 的应用层队头阻塞；但 TCP 层丢包仍然会让所有 stream 一起等。

在项目里的体现：现在还是 HTTP/1.1。浏览器用 h2 基本要求 TLS 加 ALPN，我还没上域名和证书；nginx 到 app 是内网短距离，302 只有几百字节，keepalive 已经把握手省掉了，h2 的收益主要在「一个页面几十个资源」，短链是一次跳转一个请求。另外 uvicorn 默认的 h11 实现不支持 h2，要换 hypercorn。这块没实测。

面试怎么答：h2 解决的是「同一个域名下很多小请求」的并发，短链的模型是「一次请求、一个 302」，收益有限；上 h2 的前置条件是 TLS 和 ALPN，所以它和「上 HTTPS」是同一件事。

### 3.7 302 和 301 的区别，为什么短链用 302

一句话原理：301 是永久重定向，浏览器和 CDN 会缓存，甚至没设 `Cache-Control` 也会缓存；302 是临时重定向，默认不缓存。

在项目里的体现：`app/api/v1/redirect.py` 返回 `RedirectResponse(status_code=302)`。原因是短链的目标地址会改（PATCH）、会过期、会被软删，发 301 的话用户浏览器缓存了之后我改地址他还在跳老地址，我完全控制不了，CDN 也一样。

这里有一条自我更正：`nginx/nginx.conf` 里我原来写「不缓存 302」，其实 `proxy_buffering off` 关的是响应缓冲，跟缓存没关系。真正让 302 不被缓存的是浏览器默认不缓存临时重定向，加上我们没设 `Cache-Control: max-age`、也没配 `proxy_cache`。注释已经改成「不缓冲」，面试官要是抠这个点，主动说清楚比被问出来好。

面试怎么答：先讲语义和缓存行为的差别，再讲业务原因「短链要能改、能删、能过期」，最后补 CDN 场景的取舍：要命中率就得给短 max-age 或者用 301，用可控的不一致换性能。

### 3.8 429 加 Retry-After 的语义，客户端应该怎么退避

一句话原理：429 表示「你被限流了」，不是参数错；`Retry-After` 告诉客户端多少秒之后可以重试，值可以是秒数也可以是 HTTP 日期。

在项目里的体现：`app/exceptions.py` 的 `RateLimited` 带一个 `retry_after`，`app/middleware/ratelimit.py` 从 `redis_client.ttl(key)` 取当前窗口剩余秒数，`app/main.py` 的异常处理器把它写进响应头。固定窗口下这个值最大就是 60 秒。

客户端该怎么做：先尊重 `Retry-After`，再叠加指数退避加随机抖动，避免所有客户端同一秒重试造成第二次尖峰；限制重试次数，4xx 不要无脑重试。服务端如果同时给 `X-RateLimit-Remaining` 会更友好，项目没给。

面试怎么答：429 的语义是「限流不是错误」，`Retry-After` 是服务端给出的最早可重试时间；抖动是必须的，否则限流会把流量从「均匀超限」变成「齐步重试」。

### 3.9 反向代理下的客户端 IP，为什么读到的是 nginx 的 IP

一句话原理：TCP 连接是 nginx 和客户端建的，应用看到的对端地址永远是 nginx；真实客户端地址只能靠反代写进 `X-Forwarded-For` 或 `X-Real-IP` 带过来。

在项目里的体现：这个坑我真踩了。部署到 nginx 后面之后，`request.client.host` 全是 nginx 的容器 IP，所有用户共用一个限流桶，整站只有 600 次每分钟，既误伤用户又等于没限流，`app/config.py` 的注释里记了这个实测。

修法是 `TRUST_PROXY_HEADERS=true`（`docker-compose.prod.yml` 里设了），`_client_ip` 先看 `X-Real-IP`，没有就取 `X-Forwarded-For` 的最后一个，都没有才回落 `request.client.host`。取最右一跳的原因：nginx 用 `$proxy_add_x_forwarded_for` 把 `$remote_addr` 追加在末尾，这一跳是离我们最近的反代看到的地址，客户端伪造不了；最左边那段是客户端自己可以随便写的。

安全前提：只有 app 不能被公网直连时才可信。生产 compose 里 app 不映射端口，所以能信任 nginx；本机开发默认 False，不然客户端自己发一个 `X-Real-IP` 就绕过限流了。`tests/test_ratelimit.py` 里 `test_trusted_proxy_uses_rightmost_forwarded_for` 和 `test_proxy_header_ignored_when_not_trusted` 锁住了这两个行为。

面试怎么答：先说「应用只能看到最后一跳」，再说信任链的方向（从右往左数才可信），最后说安全边界（只有入口可信时才读头）。

### 3.10 nginx 502 / 504 / 499 分别是谁的问题

一句话原理：502 是 upstream 不可达或者返回了无效响应，504 是 nginx 等 upstream 响应超时，499 是 nginx 自己的日志码，表示客户端在响应回来之前先断开了。

在项目里的体现：真实踩的是 502。`docker compose up -d --force-recreate app` 之后 app 明明 healthy，访问一直是 502，error.log 是 `connect() failed (111: Connection refused)`，upstream 写的是 `172.18.0.4:8000`，而 `docker inspect` 显示 app 已经是 `172.18.0.5`。原因是 nginx 启动时把 upstream 里的主机名解析成 IP 并一直缓存，容器重建换了 IP 它不知道。修法是改完 app 必须 `restart nginx`，这条写进了 README。

另一条路是让 nginx 自己跟踪 IP：`resolver 127.0.0.11 valid=10s` 加变量 `proxy_pass http://$upstream_app`，代价是失去 upstream 块的 `keepalive`。短链这种高并发场景连接复用更值钱，所以我选了「保留 upstream，发布时重启 nginx」。504 在我们这里是 `proxy_read_timeout 30s` 触发，通常是 app 真的慢了，比如线程池排队；499 不在响应里，只出现在 access log，通常是客户端超时时间比服务端 P99 还短。

面试怎么答：这三个码分的是「谁先放弃」：nginx 连不上 upstream 是 502，nginx 等不及是 504，客户端等不及是 499。排查顺序是先看 error.log 的错误文本，再去 app 日志里找有没有对应请求。

### 3.11 DNS 解析与 TTL，容器名怎么解析的

一句话原理：域名解析结果会按 TTL 缓存在浏览器、操作系统和各级 DNS 上，TTL 太长，换了 IP 之后老地址还会被用很久；容器编排里服务名由 Docker 内置 DNS 127.0.0.11 解析。

在项目里的体现：`docker-compose.yml` 和 `docker-compose.prod.yml` 都覆盖了 `DATABASE_URL` 和 `REDIS_URL`，把 `localhost` 换成 `postgres`、`redis`，因为容器内的 localhost 是容器自己；nginx 的 upstream 写 `app:8000`，也是靠 127.0.0.11 解析。

这里和 502 那条是同一个根因的另一面：Docker DNS 会给记录一个 TTL，但 nginx 对 upstream 块里的主机名只在启动或 reload 时解析一次，所以 TTL 对它没有意义，必须重启。改成变量加 `resolver` 之后，`valid=10s` 才会真正生效。

面试怎么答：DNS 的 TTL 决定故障切换速度，但很多组件只在启动时解析一次，容器里尤其容易踩；要么重启，要么显式配 resolver。

### 3.12 TLS 1.3 握手几个 RTT，上 HTTPS 之后延迟为什么涨

一句话原理：TCP 三次握手之后，TLS 1.3 完整握手 1 个 RTT（1.2 是 2 个），会话复用可以 0-RTT；所以新建一条 HTTPS 连接，要在 TCP 的 1 个 RTT 之上再加 1 个 RTT 才能发第一个 HTTP 请求。

在项目里的体现：项目没上 HTTPS，nginx 只 `listen 80`，没有证书也没有域名，所以「上 HTTPS 延迟涨多少」我没实测，只能按原理说。真上了之后涨的是新建连接多 1 个 RTT，加上证书验证和非对称运算的 CPU；复用连接只多对称加解密。TLS 1.3 把证书也加密了，抓包看不到证书内容，排查要靠 key log。

面试怎么答：TLS 1.3 是 1-RTT，配合 TCP 就是 2-RTT 才发首字节；优化方向是会话复用、0-RTT、OCSP stapling、ECDSA 证书。补一句「这块我项目里没落地，是按原理理解的」。

### 3.13 本机压测和真实网络的差距

一句话原理：loopback 不出网卡，RTT 几十微秒、带宽几乎无限、不丢包、不用拥塞控制；真实网络有 RTT、带宽上限、丢包和拥塞窗口爬升。

在项目里的体现：PostgreSQL 和 Redis 都在同机 Docker 里，一次 Redis GET 是 0.1ms 量级，所以加 L1 只省 5 到 15ms，还被排队噪声盖住；700 QPS 里几乎没有时间花在网络上，全是线程池和连接池排队。`docs/benchmark.md` 里专门写了「这是本机压测，不是云上压测」。

真实部署后会变的：一次跳转至少多两个 RTT 的接入延迟；Redis 变成跨机，L1 省下的 0.5 到 1ms 才有意义；带宽和丢包会放大响应体积的影响，虽然 302 很小。

面试怎么答：本机压测测的是服务端处理能力上限，不是用户体验；要端到端延迟就得从另一台机器打，或者看真实用户侧的监控。

### 3.14 长连接、连接池，数据库侧的排队

一句话原理：连接池大小就是并发查询的上限，超出的请求在池里排队，`pool_timeout` 默认 30 秒，等不到就抛异常；这不是网络层队头阻塞，但现象一样：平均延迟很高，Min 很低。

在项目里的体现：A 组 200 并发时 `/metrics` 里 `db_pool_size 5`、`db_pool_checked_out 15`，5 加 10 个 overflow 全打满，单次查询 7.5ms 却要排队十几轮，平均延迟 310 到 400ms。把池调到 20 加 10 之后 A 组从 487 提到 631。然后 4 worker 时 4 乘 30 等于 120，超过 PG 的 `max_connections=100`，报 `FATAL: sorry, too many clients already`，18 次异常 5 个 500；修法是 `app/db.py` 按 `WEB_CONCURRENCY` 均分，4 worker 时每进程 5 加 2。

配套参数也要能解释：`pool_pre_ping=True` 每次取连接多一次往返，避免拿到被 PG 断掉的死连接；`pool_recycle=1800` 防止连接闲置超过 PG 或中间件的超时；`echo=False` 因为压测时 SQL 日志能把 stdout 写满。

面试怎么答：池子不是越大越好，PG 的连接是进程，超了直接拒绝新连接；正确做法是按 worker 数均分，用指标看 `checked_out` 水位，再看 `Min` 和 `P50` 的差距判断是不是在排队。

### 3.15 一条 302 响应走完链路，日志里能看到什么

把上面这些串起来，面试时可以照着讲一遍，很快就显得你熟悉线上排查：浏览器先解析域名，命中缓存就 0 开销；然后 TCP 握手，有 TLS 再加 1 个 RTT；接着发 `GET /abc1234`。请求到 nginx，nginx 从 `upstream shortlink_app` 取一条长连接转给 app，同时写 `X-Real-IP` 和 `X-Forwarded-For`。uvicorn 交给 FastAPI，跳转路由先被 `limit_redirect` 拦一次（一次 Lua EVAL），然后查 L1、查 Redis、必要时回源 PG，最后 `record_click` 写一次 Redis 计数器，返回 302。响应只有几百字节，nginx 不缓冲直接回浏览器，浏览器拿 `Location` 再发一次请求。

这一次请求里，能看到的东西分别是：nginx access log 的 `$request_time`（从收到请求到响应写完，包含 upstream 的时间）、nginx error log（502、504 和 upstream 连接错误的第一现场）、uvicorn access log（app 自己处理的耗时）、`/metrics`（分位数、分层缓存命中、singleflight 合并次数）。讲的时候把「nginx 的时间和 app 的时间分开看」说出来，这是排查网络问题最基本的一刀。

排查工具挑几个说就够：`netstat -ano | findstr :8000` 查端口占用（`docs/troubleshooting.md` 第 2 条就是这么定位的）、`curl.exe -w "%{time_total}"` 分阶段量延迟（`docs/DEMO.md` 里用它对比冷启动 7.0ms 和命中 4.7ms）、`docker logs` 加 `docker inspect`（502 那次靠它确认 upstream IP）。`ss -lnt` 和 `tcpdump` 我知道原理，但没在项目里实际用过。

### 3.16 302 响应的报文长什么样

一句话原理：重定向的本质是返回一个 3xx 状态码加 `Location` 头，浏览器读到之后自己再发一次请求，响应体通常是空的。

在项目里的体现：`app/api/v1/redirect.py` 的 `RedirectResponse(url=..., status_code=302)` 生成的报文大致是下面这样。body 是空的，所以只有 `content-length: 0`，连 `content-type` 都没有，这也说明 gzip 对跳转完全没收益，`nginx/nginx.conf` 只给 JSON 开 gzip 就是按这个写的。

```
HTTP/1.1 302 Found
location: https://example.com/a/long/path
content-length: 0
server: uvicorn
```

`server_tokens off` 只管 nginx 自己的 `Server` 头，app 返回的 `server: uvicorn` 是 uvicorn 加的，要不要抹掉是安全偏好，不影响功能。

面试怎么答：3xx 的响应体没有意义，客户端只认 `Location`；优化跳转不要往「压缩响应」上想，要往「少一次回源、少一次跨网调用」上想。

---

## 4. 如果面试官问「你这个项目有什么不足」

这五条都是真的，别粉饰，每条后面跟一句怎么改。

1. 单进程同步路由。所有路由都是 `def`，靠 anyio 默认 40 个线程跑，600 到 700 QPS 就饱和了，200 并发下 P50 的八成是排队。怎么改：改 `async def` 加 asyncpg 和 redis asyncio，或者先加 worker（已验证 4 worker 大约 2 倍，瓶颈转到压测机）。
2. 没有布隆过滤器。不存在的长尾码第一次一定回源，空值缓存只能挡 60 秒。怎么改：启动时或定时把全量 code 灌进布隆，挡掉「一定不存在」；软删除的码用计数布隆或者定期重建。
3. 限流是单机固定窗口。窗口边界可能放过接近两倍突发，而且依赖单个 Redis，Redis 挂了 `hit()` 直接 500，没有降级。怎么改：换滑动窗口或令牌桶，把限流前移到网关层，Redis 异常时 fail-open 并告警。
4. L1 在多进程下不一致。删改只失效当前进程，其他 worker 最多 30 秒旧值；singleflight 也是进程内的，跨进程不合并。怎么改：Redis pub/sub 广播失效、值里带版本号，或者跨机部署时干脆只留 L2。
5. 没有 HTTPS、域名和监控告警。nginx 只监听 80，没有证书；只有 `/metrics` 文本，没有 Prometheus、Grafana 和告警规则，出了问题要靠人看日志。怎么改：上域名加 ACME 证书、开 TLS 1.3 和 h2；metrics 接 Prometheus，配 QPS、错误率、P99、连接池水位的告警。

被问住的时候别硬编，可以这么说：这个我没实测，只能按原理讲，我理解是……；如果要验证，我会用……去测。这句比编一个数字安全得多，而且面试官通常接着问的就是「你会怎么验证」，主动权又回到你手上。

---

## 5. 反问面试官的问题

挑两三个问，别一次问完。问之前先听对方前面聊了什么，往上接。

1. 你们线上 Redis 和数据库的一致性是怎么保证的？是 Cache-Aside 删缓存，还是有 binlog 订阅或者延迟双删？删缓存失败有补偿机制吗？
2. 限流放在网关层还是应用层？如果是网关，应用层还做兜底吗？超限返回 429 还是直接拒绝连接？
3. 发布时怎么处理长连接和流量摘除？是先让 readiness 失败、等一个 keepalive 周期，还是靠 K8s 的 preStop 加 sleep？
4. 线上慢请求你们怎么定位？有全链路 trace，还是靠 access log 的 `request_time` 加 `upstream_response_time`，再对指标？
5. 热点 key 和缓存击穿在你们那边怎么防？有没有本地缓存加单飞，或者热点探测自动打散？

最后一个问题可以接自己项目：我这边用 singleflight，但它是进程内的，跨 worker 不合并，想听听你们在生产里是怎么做的。
