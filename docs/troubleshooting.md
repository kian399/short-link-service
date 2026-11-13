# 踩坑记录

> 这些是开发过程中真实遇到并解决的问题，不是抄来的最佳实践。
> 面试被问「你遇到过什么坑」时，从这里挑一条讲透，比背概念有用。

---

## 1. 改了代码但功能没变：`docker compose up` 不重建镜像

**现象**：`/metrics` 接口一直返回 `{"detail":"短链接不存在"}`，代码里明明写了。

**原因**：`docker compose up -d` 用的是**已有镜像**。镜像构建于两周前，代码早就改了但没进镜像。
更糟的是 `Dockerfile` 里 `COPY --from=ghcr.io/astral-sh/uv:latest` 在国内拉不动，
`docker compose build` 会卡 8 分钟没有任何输出，所以一直没敢重建。

**解决**：

```dockerfile
# 换成 pip 从国内镜像装，构建从 8 分钟降到 50 秒
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ "uv>=0.13"
```

```bash
docker compose up -d --build   # 改代码后用这个
```

**教训**：排查「代码没生效」时，第一步看 `docker images` 的 `CreatedAt`，别怀疑代码。

---

## 2. 端口 10048：三个不同的占用者

**现象**：`[ERROR] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次`

先后遇到过三种占用来源：

| 占用者 | 场景 |
|---|---|
| `com.docker.backend.exe` + `wslrelay.exe` | app 容器映射了 `0.0.0.0:8000` |
| 残留的 `python.exe` | 上一次的 uvicorn 没退干净 |
| 实际是自己的服务 | 忘了它正在跑，还想再起一个 |

**解决**：

```powershell
netstat -ano | findstr :8000          # 先看谁占着
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

**根因**：`uvicorn --workers 1` 会起一个多进程管理器，`Ctrl+C` 经常只杀掉父进程，
子进程攥着端口不放。**去掉 `--workers`** 用单进程模式，`Ctrl+C` 能干净退出。

---

## 3. `GETDEL` 把数据吃掉了

**现象**：点击统计偶尔整批丢失，Redis 计数清零了但数据库里没有对应记录。

**原因**：第一版用 `GETDEL` 一把取走计数再写库。`GETDEL` 是原子的，但**原子地取出 ≠ 安全地处理**。
只要后面的 `INSERT` 失败（比如表还没建），这批点击就永久蒸发。实测丢了 20 次点击。

**解决**：改成三步「读快照 → 写库 → DECRBY 扣减」：

```python
rows = snapshot_counts()      # GET，不删
db.execute(upsert_sql, ...)   # 写库
db.commit()                   # 提交成功
ack_counts(rows)              # DECRBY 只减已落库的部分，归零才删键
```

好处：
- 写库失败时计数器原样保留，下次重试能补回来
- 落库期间新来的点击不会被一起删掉

**测试锁死**：`test_flush_failure_keeps_counter` 用 monkeypatch 让 `db.execute` 抛异常，
断言计数还在。

---

## 4. 测试把开发库搞坏了：alembic 版本和表结构错位

**现象**：容器启动报 `relation "link_stats_daily" already exists`，然后无限重启。

**原因**：`tests/conftest.py` 的 `engine` fixture 直接对 `settings.DATABASE_URL`（**开发库**）
执行 `Base.metadata.create_all()`，把新表建了；但 `alembic_version` 还停在上一版。
于是 alembic 以为表不存在，去 `CREATE TABLE`，撞了。

**临时解决**：

```sql
UPDATE alembic_version SET version_num='0003_fk_cascade';
```

然后 `alembic upgrade head` 重新建表。

**正确做法**（待办）：测试用独立的库（`shortlink_test`）或内存 SQLite，别碰开发库。

---

## 5. 压测数据不可比：上一组的缓存没清

**现象**：B 组跑出 773 QPS，C 组只比它高一点点，D 组反而更低。五组数据看起来毫无规律。

**原因**：每组开始前没清 Redis。B 组跑完留下 10 万个码的缓存，C 组开局就白占便宜；
加上单次压测本身有 10%–20% 波动，噪声直接把结论淹没了。

**解决**：

```powershell
docker compose exec redis redis-cli flushdb     # 每组开始前
```

并且**每组跑两次取中位数**。

**顺带发现**：A 组连跑三次得到 487 / 566 / 631，一路递增，不是随机波动——
是 PostgreSQL 缓冲池在预热（10 万行数据从磁盘读进内存）。**只有第三次是稳定值。**

---

## 6. 压测跑了个寂寞：服务根本没起

**现象**：locust 显示 QPS 94、延迟 2000ms、**失败 100%**，错误是 `unexpected status 0`。

**原因**：`status 0` 表示连接被拒绝。压测脚本启动时服务还没起（或者已经退了），
所有请求都打空。三组数据加起来 1.7 万次失败，全部作废。

**解决**：压测前先确认服务活着：

```powershell
curl.exe -s http://127.0.0.1:8000/healthz    # 必须返回 {"status":"ok"}
```

**教训**：`--headless` 模式下 locust 不会因为失败率高就停下，它会安静跑完三分钟。
看结果先看 **Failure Count**，不是 QPS。

---

## 7. 单向命令：`--host` 后面没值

**现象**：

```
locust: error: argument -H/--host: expected one argument
```

**原因**：从文档里复制多行命令，粘贴时被换行截断了，`--host` 和它的值被拆成两条命令。
预热那条也是同样问题。

**解决**：命令写成一行；非要换行用 PowerShell 反引号 `` ` `` 续行，**反引号后不能有空格**。

---

## 8. 判断「平均延迟高」到底是谁的问题

**现象**：A 组平均延迟 400ms，看着像数据库很慢。

**实际**：`Min` 只有 7.5ms。差值 390ms 全是排队。

用两条信息定位：

```bash
curl -s localhost:8000/metrics | grep db_pool
# db_pool_size 5
# db_pool_checked_out 15     ← 5 + 10 overflow 全满
```

200 并发抢 15 个连接，每次查询 7ms，但要排队等十几轮 → 400ms。

**结论**：看延迟永远先看 `Min` 和 `P50` 的差距，再看资源池的占用。
`Min` 是系统在无竞争时的真实能力。
