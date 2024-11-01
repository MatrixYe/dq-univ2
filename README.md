# dq-univ2

uniswap v2交易数据同步器

```shell
python main.py \
  --network ethereum \
  --endpoint_url "https://responsive-weathered-wave.quiknode.pro/bddde541192e648f9cfb99a1ad86d8846058d334/" \
  --factory "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f" \
  --full_pair false \
  --skip_history false \
  --start_block 0 \
  --sync_interval 10 \
  --mongo "mongodb://root:nopassword@64.130.51.53:5011/" \
  --redis "redis://:nopassword@127.0.0.1:5010/db"

``` 