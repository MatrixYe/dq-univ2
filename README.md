# dq-univ2

**面向 EVM 链的 Uniswap v2 链上数据同步器——全量历史回补 + 实时跟块，带断点续传。**

一个 Docker 镜像、N 个容器。每个容器索引一条链，写入自己独立的数据库。

![language](https://img.shields.io/badge/Python-3.12-3776ab) ![chain](https://img.shields.io/badge/EVM-web3.py%206.16-3c3c3d)

---

## 产出什么

可查询的每条链上"全部 Uniswap v2 池子 + 全部交易"的记录：

| 集合 | 内容 |
|---|---|
| `univ2_base` | 同步状态机（见下文） |
| `univ2_pairs` | 每个池子一条：两种代币、符号、精度、储备量、最新价格、创建者 |
| `univ2_swap` | 每笔 swap 一条：池子、交易者、两种代币数量、区块、交易哈希、**交易 nonce** |
| `tokens` | ERC-20 元数据缓存（符号、精度、总供应量） |
| `univ2_event` | 原始解码事件——*早期版本写入，现已是死代码* |
| `univ2_kline` | 5 分钟 OHLCV 聚合——*已实现但刻意未使用* |

数据库名为 `univ2_{chain}`，所以 `--chain ethereum` 和 `--chain arbitrum` 可以共用一个
MongoDB 实例而互不冲突。

### 为什么每笔 swap 都要存 `nonce`

之所以持久化交易者 nonce，是因为**某个钱包对某个代币的首次交易**（nonce 很小、账户很新）
是识别协同操作 / 内幕行为的有效信号——也就是典型的"公告前先埋伏"模式。
这只是一个廉价的整数，但后续能撑起一整个分析维度。

---

## 架构

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │  Task.run()                                                          │
   │    1. watchdog()          校验全部命令行参数                          │
   │    2. _init_db()          建索引，读取/初始化 univ2_base              │
   │    3. _sync_all_pairs()   回补池子元数据（--full_pair 开启时）         │
   │    4. _loop()             永远跟随链头                                │
   └────────────────────────────────┬─────────────────────────────────────┘
                                    │
          ┌─────────────────────────┴──────────────────────────┐
          │  _loop()                                           │
          │    x = 本地 sync_block                             │
          │    y = 远端 eth_blockNumber                        │
          │    for i in (x, y]:  _to_scan_block(i)             │
          │        失败 → break，下一轮重试                     │
          │        成功 → 置 sync_block = i，sleep 0.1s         │
          └─────────────────────────┬──────────────────────────┘
                                    │
                     ┌──────────────▼───────────────┐
                     │  _to_scan_block(块高)        │
                     │    block = get_block(i)      │  → 时间戳
                     │    logs  = get_logs(from=i, to=i)
                     └──────────────┬───────────────┘
                                    │  逐条 log
                     ┌──────────────┴──────────────────────────────┐
                     │  address == --factory ?                      │
                     │      → PairCreated 0x0d3648bd…               │
                     │  address 是已知池子？                         │
                     │      → Swap   0xd78ad95f…                    │
                     │      → Sync   0x1c411e9a…                    │
                     │  其他 → 忽略（无需任何 ABI 调用）              │
                     └──────────────┬──────────────────────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
   ┌──────────────────────┐                   ┌──────────────────────────┐
   │  Redis               │                   │  MongoDB                 │
   │  tx 缓存，TTL 120 秒  │                   │  pairs / swaps / tokens  │
   │  tx_hash → {from,    │                   │  + univ2_base 状态        │
   │   nonce}             │                   │                          │
   └──────────────────────┘                   └──────────────────────────┘
```

### 同步状态机

`univ2_base` 是单个文档（`_id: 1`），承载全部进度：

| 字段 | 含义 |
|---|---|
| `pair_index` | 已导入 `univ2_pairs` 的最大 `allPairs(i)` 索引 |
| `start_block` | 本次运行的起始区块（启动时若链头未知会被改写） |
| `sync_block` | 已**完整扫描**的最大区块——断点续传的恢复点 |
| `parse_block` | 预留给独立解析阶段 |

`sync_block` 只在某个区块的日志**处理完之后**才推进，所以 `kill -9` 最多丢掉正在处理的那
一个区块，绝不会丢已经完成的区块。启动时读回它继续跑。这就是"断点续传"，
也是这个索引器任何时刻都能安全重启的核心原因。

### 池子发现是一个独立、可恢复的过程

`_sync_all_pairs` 从 `pair_index + 1` 走到 `factory.allPairsLength() - 1`，
逐个调用 `factory.allPairs(i)`，再取 `token0`/`token1` 以及两种代币的
符号 / 精度 / 总供应量。它：

- **每处理完一个池子就推进 `pair_index`**，所以中途被中断可以继续；
- **跳过非标准代币**（没有 `symbol`/`decimals`/`totalSupply` 的记日志后丢弃），
  而不是让整个流程崩掉；
- 由 `--full_pair` 控制，元数据从别处导入时可以关掉。

因为 `_id` 是小写池子地址、写入用 `upsert`，重复执行这个过程是幂等的。

---

## 关键工程决策

| 决策                                         | 理由                                                                                                  |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| **可恢复的 `sync_block` 检查点**                  | 长期运行的索引器的全部意义就在于能挺过重启。"成功后打点"是最简单且正确的规则。                                                            |
| **swap 的 `_id = "N{block}I{log_index}"`**  | 天然的确定性主键。重扫产生的重复插入会大声失败，而不是静默污染数据集。                                                                 |
| **Redis tx 缓存，TTL 120 秒**                  | 一个区块里同一交易者的 N 条相关日志，本来会触发 N 次相同的 `get_transaction` 调用。缓存把它们合并；TTL 保证缓存有界，并在可能变陈旧之前就过期。              |
| **直接匹配 `topic0`，不按 ABI 名字解码**              | `eth_getLogs` 返回的是匿名日志。匹配三个签名哈希（`PairCreated`、`Swap`、`Sync`）再用 `eth_abi` 解码，省掉了构建和维护一整层 ABI 分发表的工作。 |
| **`--skip_history` 支持秒级可用**                | 对活跃链从 0 号区块开始要跑好几天。让新数据集从链头开始是一个正当的一等模式，不是权宜之计。                                                     |
| **一个镜像、多个容器**                              | `--chain` 只用来命名数据库和容器。加一条链是 `docker run`，不是改代码。                                                     |
| **做任何事之前先 `watchdog()`**                   | 一个打错的 factory 地址会静默地把整晚浪费在"什么都没索引到"上。要因为参数错误失败，而不是因为数据错误失败。                                         |
| **每块 `sleep(0.1)` 加上可配置的 `sync_interval`** | 两个独立旋钮：追赶时的节流节奏，和追平后的轮询频率。推荐的 `sync_interval` 是链的出块时间。                                              |
| **`Sync` 事件更新储备量，`Swap` 事件更新价格**           | 储备量和价格来自不同事件；把两条写入路径都写明确，数据里就不会出现"这个字段是不是最新的？"的歧义。                                                  |

---

## 运行

### Docker（推荐——生产就是这么跑的）

```bash
docker build -t dq-univ2 .

docker run -itd --name dq-univ2-ethereum --network dq dq-univ2 \
  --chain          ethereum \
  --endpoint_url   "https://your-archive-node" \
  --factory        "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f" \
  --full_pair      true \
  --skip_history   false \
  --start_block    21102952 \
  --sync_interval  10 \
  --mongo          "mongodb://user:pass@dq-mongo:27017/" \
  --redis          "redis://:pass@dq-redis:6379/0"

docker logs -f --tail 100 dq-univ2-ethereum
```

再加一条链就是把上面的命令改个容器名、`--chain`、`--factory` 和 RPC。

### 本地运行（仅用于开发）

```bash
pip install -r requirements.txt

python main.py \
  --chain ethereum \
  --endpoint_url "https://your-rpc-endpoint" \
  --factory "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f" \
  --full_pair false \
  --skip_history false \
  --start_block 0 \
  --sync_interval 10 \
  --mongo "mongodb://127.0.0.1:27017/" \
  --redis "redis://127.0.0.1:6379/0"
```

### 参数说明

| 参数 | 含义 |
|---|---|
| `--chain` | EVM 链名。**只用于命名数据库**（`univ2_<chain>`）。 |
| `--endpoint_url` | HTTP RPC 端点；必须与链匹配 |
| `--factory` | Uniswap v2 Factory 地址（V2 factory 在很多链上是同一个地址，但请按链核实） |
| `--full_pair` | 是否执行池子元数据回补 |
| `--skip_history` | 从当前链头开始，而不是从 `--start_block` |
| `--start_block` | 不跳过历史时的起始区块 |
| `--sync_interval` | 追平链头后每轮轮询的间隔秒数（与出块时间一致） |
| `--mongo` / `--redis` | 连接串 |

> **绝对不要把带 API key 的真实 RPC 服务商 URL 写进会提交到仓库的文件里。**
> 通过环境变量或密钥管理服务传入。

---

## 技术栈

```
语言       Python 3.12
链         web3.py 6.16（HTTPProvider、eth_getLogs、eth_getBlockByNumber）、eth_abi
存储       pymongo 4.5（MongoDB）、redis 5（StrictRedis）
配置       click 8.1、toml
打包       Docker（python:3.12.2 基础镜像，ENTRYPOINT python main.py）
```

---

## 局限与已知缺口

- **只支持 Uniswap v2。** v3 索引是另一个项目，不在本仓库内。
- **没有重组保护。** 循环扫到 `eth_blockNumber`，也就是链头，没有确认深度。
  一次短重组就可能留下从未进入主链的记录。加一个 `--confirmations` 偏移是标准修法。
- **顺序、单线程扫块。** 吞吐受限于逐区块的 RPC 往返延迟。因此从创世块开始追赶很慢，
  按范围批量拉取（`get_logs(from=a, to=b)`）是显然的优化方向。
- **`_fetch_logs` 会把区块内的*全部*日志拉回来，再在 Python 里过滤。**
  把 `address` / `topics` 直接传给 `eth_getLogs`，能让节点替你做这件事，
  并大幅削减传输量。
- **`price` 是用 swap 差额算的 `amount_0 / amount_1`**——是比率代理，不是做过精度归一化的
  价格，也不是预言机价格。只能当作方向性指标，不能当作报价。
- **留下了死代码：** `univ2_event`、`univ2_kline` / `_parse_kline`，以及重复的
  `_save_event`。文件里标注了"未使用"而不是删掉，这些应该清理。
- **没有结构化的限流处理。** RPC 失败后记日志、跳出区块循环，靠下一轮重试。
  没有指数退避，也没有服务商故障转移。
- **没有测试、没有 CI。** 三个事件解码器是对十六进制的纯函数，是最值得先覆盖的部分。
- **没有 LICENSE 文件。**

### 后续计划

1. `--confirmations` 确认深度 + 基于 `block_number` 的重组回滚路径。
2. 用 `address` + `topics` 过滤的区间式 `get_logs(from, to)` 提升追赶吞吐。
3. 统一的 `_id` 唯一性约束（或全部改为 upsert），让"不会重复"从期望变成保证。
4. 删除死掉的集合和 kline 代码路径。
