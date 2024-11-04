# dq-univ2

uniswap v2交易数据同步器，采用扫描区块的方式进行同步，支持多链。在启动任务时采用同一镜像`dq-univ2`
可以使用不同的容器进行同步，比如`dq-univ2-ethereum` `dq-univ2-coinbase`
- 同步Pool数据
- 同步Swap数据

## 环境

`docker` `python3。12`

## 安装与部署
编译

```shell
docker build -t dq-univ2 .
```
运行

```shell
docker run -itd --name dq-univ2-ethereum --network dq dq-univ2 \
  --chain ethereum \
  --endpoint_url "https://responsive-weathered-wave.quiknode.pro/bddde541192e648f9cfb99a1ad86d8846058d334/" \
  --factory "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f" \
  --full_pair true \
  --skip_history false \
  --start_block 21102952 \
  --sync_interval 10 \
  --mongo "mongodb://root:nopassword@dq-mongo:27017/" \
  --redis "redis://:nopassword@dq-redis:6379/db"
```
参数说明:
- chain 区块网络,仅限EVM系列，比如ethereum,coibase
- endpoint_url 节点url，必须与区块网络匹配
- factory uniswapv2的Factory合约地址，不同的链可能不一样
- full_pair 是否优先同步全部Pool，也就是历史的Pool
- skip_history 是否跳过历史交易，如果跳过则从当前高度开始，如果不跳过，则从历史记录的高度开始，即断点续传，默认为fasle
- start_block 指定开始高度
- sync_interval 扫描区块周期，建议与网络的出快时间一致，比如以太坊主网出快为10秒
- mongo mongodb数据库地址，如果采用docker内网，那么使用网络别名代替IP
- redis redis数据库地址，如果采用docker内网，那么使用网络别名代替IP


### 脚本运行，本地测试
```shell
python main.py \
  --chain ethereum \
  --endpoint_url "https://responsive-weathered-wave.quiknode.pro/bddde541192e648f9cfb99a1ad86d8846058d334/" \
  --factory "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f" \
  --full_pair false \
  --skip_history false \
  --start_block 0 \
  --sync_interval 10 \
  --mongo "mongodb://root:nopassword@127.0.0.1:5011/" \
  --redis "redis://:nopassword@127.0.0.1:5010/db"
``` 

## 数据说明
同步的数据存储在mongodb中的 `univ2-ethereum`中，根据不同的网络进行区分
集合：
- tokens 涉及的token信息
- univ2_base 同步状态信息
- univ2_pairs 交易池信息
- univ2_swaps 掉期信息

```text
{
  "_id": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",//赤字id
  "eid": null,//事件id
  "pair": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc",//池地址
  "pindex": 0,//池索引
  "name": "USDC/WETH",//名称
  "token_0": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",//代币0 地址
  "symbol_0": "USDC",//代币0名称
  "decimal_0": 6,//代币0 精度
  "supply_0": null,//代币0 总量
  "token_1": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
  "symbol_1": "WETH",
  "decimal_1": 18,
  "supply_1": 3075246.0630798005,
  "create_time": null,//创建时间
  "create_block": null,//创建高度
  "create_tx": null,//创建哈希
  "creator": null//创建者
}
```


### Swap数据
```text
{
  "_id": "N21112947I249",//ID
  "eid": "N21112947I249",
  "pair": "0xa478c2975ab1ea89e8196811f51a7b7ade33eb11",//池地址
  "trader": "0x5552c66c21ba2575ae0cd4e87b64d8990a92455b",//交易者
  "token_0": "0x6B175474E89094C44Da98b954EedeAC495271d0F",//代币0
  "symbol_0": "DAI",//代币0名称
  "decimal_0": 18,//代币0精度
  "amount_0": 0.2455699942765748,//代币0的数量0
  "amount_1": -0.0001,//代币1的数量
  "token_1": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
  "syymbol_1": "WETH",
  "decimal_1": 18,
  "ts": 1730707163,
  "block_number": 21112947,//区块高度
  "tx_hash": "0x76aed95ae508fcd56315d4e22db64c5f962035500350c754fe6ab93536ed3a29",//交易哈希
  "nonce": 176//交易nonce，老鼠仓有时候使用
}
```