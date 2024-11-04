# -*- coding: utf-8 -*-#
# -------------------------------------------------------------------------------
# Name:         main 
# Author:       yepeng
# Date:         2024/11/1 17:44
# Description: 
# -------------------------------------------------------------------------------
# -*- coding: utf-8 -*-#
# -------------------------------------------------------------------------------
# Name:         a
# Author:       yepeng
# Date:         2021/10/22 2:44 下午
# Description: uniswap v2 数据同步
# -------------------------------------------------------------------------------
import json
import logging
import time
from typing import Any

import click
from eth_abi import abi
from pymongo import MongoClient
from redis import StrictRedis
from web3 import Web3, HTTPProvider
from web3.contract import Contract

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
lg = logging.getLogger(__name__)

BASE = "univ2_base"
TOKENS = "tokens"
UNIV2_PAIRS = "univ2_pairs"
UNIV2_EVENT = "univ2_event"
UNIV2_SWAP = "univ2_swap"
UNIV2_RAT = "univ2_rat"
UNIV2_KLINE = "univ2_kline"


# ---------常量---------#
# 区块网络
# NETWORK = "ethereum"
# # uniswap v2 factory 合约地址
# FACTORY_ADDREESS = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
# # ethereum weth 代币合约地址
# WETH_ADDRESS = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


class Task:

    def __init__(self, **kwargs):
        #     conf = {
        #         'network': network,
        #         'endpoint_url': endpoint_url,
        #         'factory': factory,
        #         'full_pair': full_pair,
        #         'skip_history': skip_history,
        #         'start_block': start_block,
        #         'sync_interval': sync_interval,
        #         'mongo': mongo,
        #         'redis': redis,
        #     }
        self.network = kwargs.get('network')
        self.endpoint_url = kwargs.get('endpoint_url')
        self.factory = kwargs.get('factory')
        self.full_pair = kwargs.get('full_pair')

        self.skip_history = kwargs.get('skip_history')
        self.start_block = kwargs.get('start_block')
        self.sync_interval = kwargs.get('sync_interval')
        self.mongo_uri = kwargs.get('mongo')
        self.redis_uri = kwargs.get('redis')

        # 初始化组件
        self._factory_abi = self._read_factory_abi()
        self._pair_abi = self._read_pair_abi()
        self._erc20_abi = self._read_erc20_abi()

        self.db = self._connect_mongo_database()
        self.rs: StrictRedis = self._connect_redis()
        self.w3 = self._connect_eth_client()
        self.factory_instance = self._gen_factory_instance(self.factory)

    # 初始化操作
    def _init_db(self):
        result = self._get_base()
        if not result:
            data = {
                '_id': 1,
                'pair_index': -1,  # 没有同步时，新开始的索引为0=-1 +1
                'start_block': self.start_block,
                'sync_block': self.start_block,
                'parse_block': self.start_block
            }
            self.db[BASE].insert_one(data)
        else:
            lg.info(f"base:{result}")

        self.db[UNIV2_PAIRS].create_index([('create_time', 1)])
        self.db[UNIV2_PAIRS].create_index([('coin_addr', 1)])
        # swap 集合索引
        self.db[UNIV2_SWAP].create_index([("ts", 1)])
        self.db[UNIV2_SWAP].create_index([("token_0", 1)])
        self.db[UNIV2_SWAP].create_index([("token_1", 1)])
        self.db[UNIV2_SWAP].create_index([("pair", 1), ("ts", 1)])
        self.db[UNIV2_SWAP].create_index([("trader", 1), ("ts", 1)])
        # token 集合索引
        self.db[TOKENS].create_index([('address', 1)])
        self.db[TOKENS].create_index([('symbol', 1)])

    def _connect_redis(self) -> StrictRedis:
        try:
            lg.info("_connect_eth_client... ...")
            uri = self.redis_uri
            client = StrictRedis.from_url(uri)
            client.ping()
            print(f"Successfully connected to Redis:{uri}")
            return client
        except Exception as e:
            print(f"An error occurred: {e}")
            exit()

    def _connect_mongo_database(self):
        lg.info(f"_connect_mongo ... ...")
        dbname = f"univ2_{self.network}"
        lg.info(f"chose database: {dbname}")
        try:
            uri = self.mongo_uri
            client = MongoClient(uri)
            client.admin.command('ping')
            lg.info(f"Successfully connected to MongoDB:{uri}")
            return client[dbname]
        except Exception as e:
            lg.error(f"An error occurred: {e}")
            exit()

    # 加载以太坊客户端
    def _connect_eth_client(self) -> Web3:
        lg.info(f"_connect_eth_client ... ...")
        return Web3(HTTPProvider(endpoint_uri=self.endpoint_url))

    # 构建factory实例
    def _gen_factory_instance(self, factory_address: str) -> Contract:
        contract_address = self.w3.to_checksum_address(factory_address)
        return self.w3.eth.contract(address=contract_address, abi=self._factory_abi)

    # 构建pair实例
    def _gen_pair_instance(self, pair_address: str) -> Contract:
        contract_address = self.w3.to_checksum_address(pair_address)
        return self.w3.eth.contract(address=contract_address, abi=self._pair_abi)

    # 构建erc20 tolen 实例
    def _gen_erc20_instance(self, erc20_address: str) -> Contract:
        contract_address = self.w3.to_checksum_address(erc20_address)
        return self.w3.eth.contract(address=contract_address, abi=self._erc20_abi)

    # 获取本地同步最新的uniswap v2 pair index
    def _get_local_pair_index(self) -> int:
        result = self._get_base()
        return result['pair_index']

    # 设置最新同步uniswap v2池子索引
    def _set_local_pair_index(self, index: int):
        lg.info(f"_set_local_pair_index:{index}")
        self._update_base("pair_index", index)

    def _get_pair(self, addr: str):
        return self.db[UNIV2_PAIRS].find_one({'_id': addr.lower()})

    def _fetch_tx(self, tx_hash) -> dict | None:
        # tx_cache = self.rs.get(tx_hash)
        tx_cache = self.rs.get(tx_hash)
        if tx_cache:
            # lg.info(f"cache is exist:{tx_hash}")
            return json.loads(tx_cache)
        else:
            tx = self._get_remote_tx(tx_hash)
            if not tx:
                return None
            data = {
                'tx_hash': tx_hash.lower(),
                'from': tx['from'].lower(),
                'nonce': tx['nonce']
            }
            self.rs.set(tx_hash, json.dumps(data), 120)
            return data

    def _get_remote_tx(self, tx_hash):
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
            return tx
        except Exception as e:
            lg.error(f"_get_remote_tx:{e}")
            return None

    def _get_remote_pair_index(self) -> int:
        try:
            index = getattr(self.factory_instance.functions, 'allPairsLength')().call()
            return index
        except Exception as e:
            lg.error(f"_get_remote_pair_index:{e}")
            return 0

    # 设置初始高度
    def _set_start_block(self, height: int):
        self._update_base('sync_block', height)
        lg.info(f"set start block: {height}")

    # 设置最新同步高度
    def _set_sync_block(self, height: int):
        if height <= 0:
            return
        self._update_base('sync_block', height)
        lg.info(f"set sync block: {height}")

    # 获取base表
    def _get_base(self):
        return self.db[BASE].find_one({'_id': 1})

    def _update_base(self, field: str, new_data: Any):
        self.db[BASE].update_one({'_id': 1}, {'$set': {field: new_data}})

    # noinspection PyBroadException
    def _fetch_block(self, i: int):
        try:
            return self.w3.eth.get_block(i)
        except Exception as _:
            lg.error(f"net error:_fetch_block")
            return None

    # noinspection PyBroadException
    def _fetch_logs(self, i: int):
        try:
            logs = self.w3.eth.get_logs(filter_params={
                'fromBlock': i,
                'toBlock': i,
            })
            return logs
        except Exception as e:
            lg.error(f"net error _fetch_logs:{e}")
            return None

    def _to_scan_block(self, i: int) -> bool:
        lg.info(f'scan block: {i}')
        block = self._fetch_block(i)
        if block is None:
            # lg.info("block is None")
            return False
        ts = block['timestamp']
        logs = self._fetch_logs(i)
        if logs is None:
            return False
        for log in logs:
            tx_hash = log.get("transactionHash").hex()
            contract_addr = log.get('address').lower()
            # 判断是否来自factory的event
            if contract_addr.lower() == self.factory.lower():
                tx = self._fetch_tx(tx_hash)
                if not tx:
                    continue
                self._handle_factory_event(ts, tx, log)
                continue
            # 判断是否来自pair的event
            pair_obj = self._get_pair(contract_addr)
            if pair_obj:
                tx = self._fetch_tx(tx_hash)
                if not tx:
                    continue
                self._handle_pair_event(ts, tx, log, pair_obj)
                continue
        return True

    # 获取远程block高度
    def _get_remote_block_number(self) -> int:
        try:
            num = self.w3.eth.block_number
            return num
        except Exception as e:
            lg.error(f"_get_remote_block_number:{e}")
            return 0

    # 获取本地同步sync高度
    def _get_sync_block(self) -> int:
        base = self._get_base()
        return base.get('sync_block')

    def _to_sync_signpair(self, i: int):
        lg.info(f"to sync sign pair index:{i}")
        try:
            pair_addr: str = getattr(self.factory_instance.functions, 'allPairs')(i).call()
            pair_instance = self._gen_pair_instance(pair_addr)
            token0 = getattr(pair_instance.functions, "token0")().call()
            token1 = getattr(pair_instance.functions, "token1")().call()
            t0_info = self._fetch_erc20(addr=token0)
            t1_info = self._fetch_erc20(addr=token1)
            if not t0_info or not t1_info:
                lg.warning(f"token0:{token0} or token1{token1} is not a norm erc20 token --> pass")
                return
            t0_addr = t0_info['address']
            t0_symbol = t0_info['symbol']
            t0_decimal = t0_info['decimal']
            t0_total_supply = t0_info.get('total_supply')

            t1_addr = t1_info['address']
            t1_symbol = t1_info['symbol']
            t1_decimal = t1_info['decimal']
            t1_total_supply = t1_info.get('total_supply')

            self._to_save_pair(i, pair_addr, t0_addr, t0_symbol, t0_decimal, t0_total_supply, t1_addr, t1_symbol,
                               t1_decimal, t1_total_supply)
        except Exception as e:
            lg.error(f"_to_sync_signpair:{e}")

    def _to_save_pair(self, pindex: int, pair_addr: str, t0_addr: str, t0_symbol: str, t0_decimal: int,
                      t0_total_supply: int, t1_addr: str, t1_symbol: str, t1_decimal: int, t1_total_supply: int):
        lg.info(f"save pair:{pair_addr.lower()}")
        new_pair_data = {
            '_id': pair_addr.lower(),
            'eid': None,
            'pair': pair_addr.lower(),
            'pindex': pindex,
            'name': f"{t0_symbol}/{t1_symbol}",
            'token_0': t0_addr,
            'symbol_0': t0_symbol,
            'decimal_0': t0_decimal,
            'supply_0': t0_total_supply,
            'token_1': t1_addr,
            'symbol_1': t1_symbol,
            'decimal_1': t1_decimal,
            'supply_1': t1_total_supply,
            'create_time': None,
            'create_block': None,
            'create_tx': None,
            'creator': None
        }
        self._insert_docm(UNIV2_PAIRS, new_pair_data)

    def _find_and_set(self, coll: str, query: dict, new_data: dict, upsert: bool):
        try:
            self.db[coll].find_one_and_update(filter=query, update={'$set': new_data}, upsert=upsert)
        except Exception as e:
            lg.error(f"_find_and_set:{new_data} {e}")

    def _insert_docm(self, coll: str, data):
        try:
            self.db[coll].insert_one(data)
        except Exception as e:
            lg.error(f"_insert_docm:{coll} {e}")

    def _fetch_erc20(self, addr: str) -> dict | None:
        ltoken = self._get_local_erc20(addr)
        if ltoken:
            # lg.info(f"token is exist {ltoken['symbol']}")
            return ltoken
        else:
            lg.info(f"token is not in local:{addr}")
            rtoken = self._get_remote_erc20(addr)
            self._to_save_erc20(rtoken)
            return rtoken

    def _to_save_erc20(self, rtoken: dict):
        if not rtoken:
            return
        lg.info(f'save erc20 token:{rtoken["symbol"]}')
        data = {
            '_id': rtoken['address'].lower(),
            'type': 'erc20',
            'address': rtoken['address'].lower(),
            'symbol': rtoken['symbol'],
            'decimal': rtoken['decimal'],
            'total_supply': rtoken['total_supply']
        }
        self._insert_docm(TOKENS, data)

    def _get_remote_erc20(self, addr: str) -> dict | None:
        try:
            erc20_instance = self._gen_erc20_instance(addr)
            symbol = getattr(erc20_instance.functions, "symbol")().call()
            decimal = getattr(erc20_instance.functions, "decimals")().call()
            total_supply = getattr(erc20_instance.functions, "totalSupply")().call()
            return {
                'address': addr,
                'symbol': symbol,
                'decimal': decimal,
                'total_supply': total_supply / 10 ** decimal
            }
        except Exception as e:
            lg.error(f"_get_remote_erc20:{e}")
            return None

    def _get_local_erc20(self, addr: str) -> dict | None:
        token = self.db[TOKENS].find_one(filter={'_id': addr.lower()})
        return {
            'address': token.get('address'),
            'symbol': token.get('symbol'),
            'decimal': token.get('decimal'),
            'total_supply': token.get('total_supply')
        } if token else None

    @staticmethod
    def _read_factory_abi():
        with open('./source/abi/UniswapV2Factory.abi', 'r') as f:
            return f.read()

    @staticmethod
    def _read_pair_abi():
        with open('./source/abi/IUniswapV2Pair.abi', 'r') as f:
            return f.read()

    @staticmethod
    def _read_erc20_abi():
        with open('./source/abi/IERC20.abi', 'r') as f:
            return f.read()

    @staticmethod
    def _parse_com(log):
        address = log.get('address')
        # block_hash = log.get('blockHash')
        block_number = log.get('blockNumber')
        log_index = log.get('logIndex')
        topics = log.get('topics')
        tx_hash = log.get('transactionHash')
        tx_index = log.get('transactionIndex')
        return {
            '_id': f"N{block_number}I{log_index}",
            'address': address.lower(),
            'block_number': block_number,
            # 'block_hash': block_hash.hex().lower(),# 废弃字段
            'log_index': log_index,
            'tx_hash': tx_hash.hex().lower(),
            'tx_index': tx_index,
            'topic0': [a.hex() for a in topics]
        }

    def _save_event(self, event: dict):
        try:
            self.db[UNIV2_EVENT].insert_one(event)
            return True
        except Exception as e:
            lg.error(f"_save_event:{e}")
            return False

    # 处理factory的合约event
    def _handle_factory_event(self, ts: int, tx: dict, log):
        topics = log.get('topics')
        if not topics:
            return
        match topics[0].hex().lower():
            case '0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9':
                event_name = "PairCreated"
                lg.info(f"find event Factoy:{event_name}")
                self._handle_factory_event_paircreated(ts, tx, log, event_name)

    # 处理pair合约的event
    def _handle_pair_event(self, ts: int, tx: dict, log, pair_obj):
        topics = log.get('topics')
        if not topics:
            return
        match topics[0].hex().lower():
            case '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822':
                event_name = "Swap"
                # lg.info(f"find event Pair:{event_name}")
                self._handle_pair_event_swap(ts, tx, log, pair_obj, event_name)
            case '0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1':
                event_name = "Sync"
                # lg.info(f"find event Pair:{event_name}")
                self._handle_pair_event_sync(ts, tx, log, pair_obj, event_name)
            case _:
                pass

    def _handle_factory_event_paircreated(self, ts: int, tx: dict, log, event_name) -> object:

        event = self._parse_com(log)
        arg_types = ['address', 'uint256']
        data = log.get('data')
        topics = log.get("topics")
        # abi.decode(['address'], topics[1])[0]
        token0 = abi.decode(['address'], topics[1])[0]
        token1 = abi.decode(['address'], topics[2])[0]
        (pair, pindex) = abi.decode(arg_types, data)
        entity = {
            'token0': token0.lower(),
            'token1': token1.lower(),
            'pair': pair.lower(),
            'pindex': pindex
        }
        event['from'] = tx['from']
        event['nonce'] = tx['nonce']
        event['name'] = event_name
        event['ts'] = ts
        event['entity'] = entity

        t0_info = self._fetch_erc20(token0)
        if not t0_info:
            lg.warning(f"can not up new pair,token0 not erc20:{token0}")
            return
        # 获取token1的基本信息，非标准币不处理
        t1_info = self._fetch_erc20(token1)
        if not t1_info:
            lg.warning(f"can not up new pair,token1 not erc20:{token1}")
            return
        t0_address = t0_info['address']
        t1_address = t1_info['address']

        t0_symbol = t0_info['symbol']
        t1_symbol = t1_info['symbol']

        t0_decimal = t0_info['decimal']
        t1_decimal = t1_info['decimal']

        t0_total_supply = t0_info['total_supply']
        t1_total_supply = t1_info['total_supply']

        # stable_index = self._cal_stable_index(t0_address, t1_address)
        new_pair_data = {
            '_id': pair.lower(),
            'eid': event.get('_id'),
            'pair': pair.lower(),
            'pindex': pindex,
            'name': f"{t0_symbol}/{t1_symbol}",
            'token_0': t0_address,
            'symbol_0': t0_symbol,
            'decimal_0': t0_decimal,
            'supply_0': t0_total_supply,

            'token_1': t1_address,
            'symbol_1': t1_symbol,
            'decimal_1': t1_decimal,
            'supply_1': t1_total_supply,

            'create_time': ts,
            'create_block': event['block_number'],
            'create_tx': tx['tx_hash'],
            'creator': tx['from']
        }
        lg.info(f"save new pair:{pair.lower()}")
        self._find_and_set(UNIV2_PAIRS, {'_id': pair.lower()}, new_pair_data, upsert=True)
        self._set_local_pair_index(pindex)

    def _handle_pair_event_swap(self, ts, tx, log, pair_obj, event_name):
        # ndex_topic_1 address sender, uint256 amount0In, uint256 amount1In, uint256 amount0Out, uint256 amount1Out, index_topic_2 address to
        event = self._parse_com(log)
        topics = log.get("topics")
        sender = abi.decode(['address'], topics[1])[0]
        s_to = abi.decode(['address'], topics[2])[0]
        # s_to = topics[2].hex().replace("000000000000000000000000", "")
        arg_types = ['uint256', 'uint256', 'uint256', 'uint256']
        data = log.get('data')
        (amount0in, amount1in, amount0out, amount1out) = abi.decode(arg_types, data)
        event['from'] = tx['from']
        event['nonce'] = tx['nonce']
        event['name'] = event_name
        event['ts'] = ts
        entity = {
            'sender': sender,
            'amount0in': str(amount0in),
            'amount1in': str(amount1in),
            'amount0out': str(amount0out),
            'amount1out': str(amount1out),
            'to': s_to
        }

        event['entity'] = entity

        # 插入swap数据
        # lg.info(f"log index:{event['log_index']}")
        # lg.info(f"sync:{entity}")
        pair = pair_obj.get('pair')
        token_0 = pair_obj.get('token_0')
        symbol_0 = pair_obj.get('symbol_0')
        decimal_0 = pair_obj.get('decimal_0')

        token_1 = pair_obj.get('token_1')
        syymbol_1 = pair_obj.get('symbol_1')
        decimal_1 = pair_obj.get('decimal_1')

        trader = tx['from']
        tx_hash = tx['tx_hash']
        nonce = tx['nonce']

        a0 = amount0out - amount0in  # 得到t0 数量
        a1 = amount1out - amount1in  # 得到t1的数量

        amount_0 = a0 / 10 ** decimal_0
        amount_1 = a1 / 10 ** decimal_1

        new_swap = {
            '_id': event['_id'],
            'eid': event['_id'],
            'pair': pair.lower(),
            'trader': trader.lower(),
            'token_0': token_0,
            'symbol_0': symbol_0,
            'decimal_0': decimal_0,
            'amount_0': amount_0,
            'amount_1': amount_1,
            'token_1': token_1,
            'syymbol_1': syymbol_1,
            'decimal_1': decimal_1,
            'ts': ts,
            'block_number': event['block_number'],
            'tx_hash': tx_hash,
            'nonce': nonce
        }
        price = round(a0 / a1, 8)
        # 插入最新的swap记录
        self._insert_docm(UNIV2_SWAP, new_swap)
        # 更新pair最新价格
        self._find_and_set(UNIV2_PAIRS, {'_id': pair.lower()}, {'price': price, 'update_time': ts}, upsert=False)

    # 处理k线数据
    def _parse_kline(self, new_swap: dict):

        start_time = 300 * int(new_swap['ts'] / 300)  # 计算k线bar起始点时间戳
        pair = new_swap['pair']
        price = new_swap['price']
        value = new_swap['value']
        is_buy = new_swap['is_buy']
        trader = new_swap['trader']

        query = {'start_time': start_time, 'pair': pair}
        update = {
            '$setOnInsert': {'open_price': price},
            '$max': {'high_price': price},
            '$min': {'low_price': price},
            '$set': {'close_price': price},
            '$inc': {
                'txs': 1,
                'txs_buy': 1 if is_buy else 0,
                'txs_sell': 1 if not is_buy else 0,
                'vol': value,
                'vol_buy': value if is_buy else 0,
                'vol_sell': value if not is_buy else 0,
            },
            '$addToSet': {'trader': trader}
        }  # 将 txs 字段加 1

        self.db[UNIV2_KLINE].find_one_and_update(filter=query, update=update, upsert=True)

    def _handle_pair_event_sync(self, ts, tx, log, pair_obj, event_name):
        event = self._parse_com(log)
        event['from'] = tx['from']
        event['name'] = event_name
        event['nonce'] = tx['nonce']
        event['ts'] = ts
        arg_types = ['uint112', 'uint112']
        data = log.get('data')
        (reserve0, reserve1) = abi.decode(arg_types, data)
        entity = {
            'reserve0': str(reserve0),
            'reserve1': str(reserve1),
        }
        event['entity'] = entity

        # 更新池子储备
        pair = pair_obj.get('pair')
        decimal_0 = pair_obj.get('decimal_0')
        decimal_1 = pair_obj.get('decimal_1')
        reserve_0 = reserve0 / 10 ** decimal_0
        reserve_1 = reserve1 / 10 ** decimal_1

        new_reserve = {
            'reserve_0': reserve_0,
            'reserve_1': reserve_1,
        }
        self._find_and_set(UNIV2_PAIRS, {'_id': pair}, new_reserve, upsert=False)

    # 迭代扫描区块
    def _loop(self):
        lg.info(f"Loop Start: skip history={self.skip_history}")
        if self.skip_history:
            remote_height = self._get_remote_block_number()
            self._set_sync_block(remote_height)

        while True:
            time.sleep(self.sync_interval)
            x = self._get_sync_block()
            y = self._get_remote_block_number()
            lg.info(f"loop sync local block:{x} remote block:{y}")
            if y == 0:
                lg.error("_loop:failed to get remote block!")
                continue
            if x > y:
                lg.warning("_loop:local block > remote block")
                continue
            if x == y:
                continue
            if x == 0:
                x = y - 1
                lg.info(f"_loop:x=0,transf to x=y-1={x},scan by current block")
                self._set_start_block(x)
            self._update_base("remote_block", y)
            # 开始扫描区块
            for i in range(x + 1, y + 1):
                lg.debug(f"_loop:to scan block {i}")
                ok = self._to_scan_block(i)
                lg.info(f"scan block {i}: {'success' if ok else 'failed'}")
                if not ok:
                    break
                self._set_sync_block(i)
                time.sleep(0.1)

    # 根据Univ2的特性，可以优先同步全部Pool信息
    def _sync_all_pairs(self, debug: bool):
        lg.info(f"Sync All Pairs:{self.full_pair}")
        while self.full_pair:
            x = self._get_local_pair_index()
            y = self._get_remote_pair_index()  # 获取远程的pair 最新索引
            lg.info(f"get local pair index:{x},get remote pair length:{y}")
            if not y:
                lg.warning("failed to get remote block!")
                break
            if x > y - 1:
                lg.warning(f"local pair index:{x} > remote pair index!what happen")
                break
            if x == y - 1:
                break
            for i in range(x + 1, y):
                time.sleep(0.1)
                self._to_sync_signpair(i)
                self._set_local_pair_index(i)
                if debug:
                    lg.info(f"sync_all_pairs is complete!")
                    return
        lg.info(f"Sync All Pairs Complete!")

    def watchdog(self):
        if not self.network:
            raise Exception(f"failed args 'network':{self.network}")
        if not self.endpoint_url:
            raise Exception(f"failed args 'endpoint_url':{self.endpoint_url}")
        if not self.factory:
            raise Exception(f"failed args 'factory':{self.factory}")
        if self.full_pair is None:
            raise Exception(f"failed args 'full_pair':{self.full_pair}")
        if not self.sync_interval or self.sync_interval <= 0:
            raise Exception(f"failed args 'sync_interval':{self.sync_interval}")
        if not self.mongo_uri:
            raise Exception(f"failed args 'mongo_uri':{self.mongo_uri}")
        if not self.redis_uri:
            raise Exception(f"failed args 'redis_uri':{self.redis_uri}")
        lg.info(f"startup: {self.network=}")
        lg.info(f"startup: {self.endpoint_url=}")
        lg.info(f"startup: {self.factory=}")
        lg.info(f"startup: {self.full_pair=}")
        lg.info(f"startup: {self.skip_history=}")
        lg.info(f"startup: {self.sync_interval=}")
        lg.info(f"startup: {self.start_block=}")
        lg.info(f"startup: {self.mongo_uri=}")
        lg.info(f"startup: {self.redis_uri=}")

    # 核心功能代码入口
    def run(self):
        self.watchdog()
        self._init_db()
        self._sync_all_pairs(False)
        self._loop()


@click.command()
@click.option('--network', type=str, required=True, help='The evm network,eg:ethereum')
@click.option('--endpoint_url', type=str, required=True, help='The endpoint URL')
@click.option('--factory', type=str, required=True, help='The endpoint URL')
@click.option('--full_pair', type=bool, required=True, help='Full pair flag')
@click.option('--skip_history', type=bool, required=True, help='Skip history flag')
@click.option('--start_block', type=int, required=True, help='Start block number')
@click.option('--sync_interval', type=int, required=True, help='Sync interval in seconds')
@click.option('--mongo', type=str, required=True, help='MongoDB connection string')
@click.option('--redis', type=str, required=True, help='Redis connection string')
def main(network, endpoint_url, factory, full_pair, skip_history, start_block, sync_interval, mongo, redis):
    click.echo(f'network: {network}')
    click.echo(f'Endpoint URL: {endpoint_url}')
    click.echo(f'factory: {factory}')
    click.echo(f'Full Pair: {full_pair}')
    click.echo(f'Skip History: {skip_history}')
    click.echo(f'Start Block: {start_block}')
    click.echo(f'Sync Interval: {sync_interval}')
    click.echo(f'MongoDB: {mongo}')
    click.echo(f'Redis: {redis}')

    conf = {
        'network': network,
        'endpoint_url': endpoint_url,
        'factory': factory,
        'full_pair': full_pair,
        'skip_history': skip_history,
        'start_block': start_block,
        'sync_interval': sync_interval,
        'mongo': mongo,
        'redis': redis,
    }
    task = Task(**conf)
    task.run()


if __name__ == '__main__':
    lg.info("start to sync uniswap v2,good luck ... ...")
    main()
