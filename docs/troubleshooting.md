# 踩坑记录

做这个项目遇到的真问题，按时间顺序记。面试被问「你踩过什么坑」，从里面挑一条讲透，比背概念管用。

## 1. 改了代码没生效：`docker compose up` 不会重建镜像

有一次 `/metrics` 一直返回 `{"detail":"短链接不存在"}`，我盯着代码看了半小时，代码是对的。

原因是 `docker compose up -d` 用的还是旧镜像。更麻烦的是那时候 `Dockerfile` 用 `COPY --from=ghcr.io/astral-sh/uv` 拉 uv，国内根本拉不动，`docker compose build` 卡八分钟没输出，所以一直在回避重建这件事。

后来改成从阿里云镜像源装：

```dockerfile
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ "uv>=0.13"
```

构建从八分钟降到五十秒，问题也算根治了。现在改完代码一律 `docker compose up -d --build`。

排查这类「代码没生效」，第一步先看 `docker images` 的 CreatedAt，别怀疑代码。

## 2. 端口 10048，三种不同的占用者

报错是那句很经典的「通常每个套接字地址只允许使用一次」。前后遇到三次，占用者不一样：

| 谁占的 | 什么情况 |
| --- | --- |
| `com.docker.backend.exe` + `wslrelay.exe` | app 容器映射了 8000 |
| 残留的 `python.exe` | 上一次的 uvicorn 没退干净 |
| 自己的服务 | 忘了它正在跑，还想再起一个 |

```powershell
netstat -ano | findstr :8000
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

根因是 `uvicorn --workers 1` 会起一个多进程管理器，Ctrl+C 经常只杀掉父进程，子进程攥着端口不放。后来本地开发干脆不加 `--workers`，单进程反而退得干净。

## 3. `GETDEL` 把点击吃掉了

现象是统计数据偶尔整批少，Redis 计数清零了但库里没有对应记录。

第一版落库逻辑是 `GETDEL` 一把取走计数再写库。`GETDEL` 本身是原子的，但原子地取出不等于安全地处理：只要后面的 `INSERT` 失败（当时 `link_stats_daily` 还没建表），这批点击就永久没了。实测丢了 20 次点击，日志里一条错误都没有。

改成三步：

```python
rows = snapshot_counts()      # GET，不删
db.execute(upsert_sql, ...)   # 写库
db.commit()                   # 提交成功才算数
ack_counts(rows)              # DECRBY 只减已落库的部分，归零才删键
```

写库失败时计数器原样保留，下次重试能补回来；落库期间新来的点击也不会被一起删掉。

`test_flush_failure_keeps_counter` 用 monkeypatch 让 `db.execute` 抛异常，断言计数还在，防止以后改回去。

## 4. 测试把开发库搞坏了

容器启动报 `relation "link_stats_daily" already exists`，然后无限重启。

`tests/conftest.py` 的 `engine` fixture 直接对 `settings.DATABASE_URL`（开发库）执行 `create_all()`，把新表建了；但 `alembic_version` 还停在上一版。于是 alembic 以为表不存在，去 `CREATE TABLE`，撞了。

临时处理是手动把版本号改回去：

```sql
UPDATE alembic_version SET version_num='0003_fk_cascade';
```

然后 `alembic upgrade head` 重新建表。

正确做法是测试用独立的库（`shortlink_test`）或者内存 SQLite，别碰开发库。现在本地跑测试都显式给 `DATABASE_URL=sqlite:///...`，CI 里本来就是独立的服务容器，所以一直没再出过。

## 5. 五组数据看起来毫无规律：上一组的缓存没清

B 组 773 QPS，C 组只高一点点，D 组反而更低，五组数字完全没有梯度。

两个原因叠在一起：每组开始前没清 Redis，B 组跑完留下十万个码的缓存，C 组开局白占便宜；再加上单次压测本身有 10%–20% 波动，噪声把结论淹了。

现在每组前面固定 `redis-cli flushdb`，并且同一配置跑两次取中位数。

顺带发现 A 组连跑三次是 487 / 566 / 631，一路涨，不是随机波动。是 PG 缓冲池在预热，十万行数据第一次是从磁盘读的。只有第三次算稳定值，前两次都得扔。

## 6. 压测跑了个寂寞：服务根本没起

locust 显示 QPS 94、延迟 2000ms、失败 100%，错误是 `unexpected status 0`。

`status 0` 是连接被拒绝。压测脚本启动时服务还没起来，所有请求打空，三组加起来 1.7 万次失败全作废。

`--headless` 模式下 locust 不会因为失败率高就停下，会安安静静跑完三分钟，CSV 照样生成，所以事后看文件是看不出问题的。

现在压测前先 `curl /healthz`，结果先看 Failure Count 再看 QPS。

## 7. `--host` 后面没值

`locust: error: argument -H/--host: expected one argument`。

从文档里复制多行命令，粘贴时被换行截断了，`--host` 和它的值变成两条命令，预热那条也一样。

命令尽量写一行。非要换行就用 PowerShell 的反引号续行，反引号后面不能有空格，这个很坑。

## 8. 平均延迟 400ms，到底是谁慢

A 组平均延迟 400ms，看着像数据库慢。但 Min 只有 7.5ms，差的那 390ms 全是排队。

```bash
curl -s localhost:8000/metrics | grep db_pool
# db_pool_size 5
# db_pool_checked_out 15     ← 5 + 10 个 overflow 全满
```

200 并发抢 15 个连接，单次查询 7ms，但要排队等十几轮，平均就 400ms 了。

看延迟先看 Min 和 P50 的差距，再看资源池占用。Min 是系统没有竞争时的真实能力。

## 9. nginx 502：容器重建后 upstream 还在打老 IP

`docker compose ... up -d --force-recreate app` 之后，app 明明 `Up (healthy)`，访问 8080 一直 502。

```bash
docker logs --tail 5 short-link-service-nginx-1
# connect() failed (111: Connection refused)
# upstream: "http://172.18.0.4:8000/healthz"

docker inspect short-link-service-app-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
# 172.18.0.5     ← 实际已经是 .5，nginx 还在打 .4
```

nginx 启动时把 `upstream` 里的主机名解析成 IP 并一直用（开源版不做动态解析）。app 容器重建后 Docker 分了新 IP，nginx 不知道。

所以发完 app 必须重启 nginx：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod restart nginx
```

想让 nginx 自己跟 IP，得放弃 upstream 块改用变量加 Docker 内置 DNS：

```nginx
resolver 127.0.0.11 valid=10s ipv6=off;
set $upstream_app app:8000;
proxy_pass http://$upstream_app;
```

代价是失去 upstream 的 `keepalive` 连接复用。短链这种高并发场景连接复用更值钱，所以选了「保留 upstream、发布时重启 nginx」。

## 10. 复跑压测，PG 没起来，一晚上白跑

11 月 17 号想把限流加进来重新测一遍。`bench/run_all_groups.py` 跑得很正常，日志也一直滚，三组跑完差不多一个小时。

回头看数据才发现不对：A 组 1385 个请求 1385 个失败，延迟 25000ms；B 组 2186 个请求全失败，20000ms。

翻 `A_uvicorn.log`（这文件堆到 161MB），满屏都是：

```
connection to server at "localhost" (::1), port 5432 failed: Connection refused
```

Docker Desktop 那次只起了 redis，postgres 容器没起来。而 `/healthz` 不碰数据库，所以一直 200，脚本的「服务就绪」检查形同虚设。

更亏的是三组的 CSV 是按固定文件名写的，失败那次直接把 C 组之前的好数据覆盖了。现在脚本里加了两道：

- 开跑前真打一次 `/{code}`，要求 302，否则直接退出；
- 每组跑完检查 `stats.csv` 的失败数和 P50，不达标就 `SystemExit`，别让人拿着废数据写报告。

教训：健康检查只证明进程活着。想证明链路通，得打一个真正查库的接口。
