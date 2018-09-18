# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017-2018 reverendus
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import array
import copy
import logging
import random
from pprint import pformat
from typing import List, Optional

import requests
from eth_abi import encode_single, encode_abi, decode_single
from hexbytes import HexBytes
from web3 import Web3
from web3.utils.events import get_event_data

from pymaker import Contract, Address, Transact
from pymaker.numeric import Wad
from pymaker.sign import eth_sign, to_vrs
from pymaker.token import ERC20Token
from pymaker.util import bytes_to_hexstring, hexstring_to_bytes, http_response_summary


class Asset:
    @staticmethod
    def deserialize(asset: str):
        if ERC20Asset.ID.upper() == asset[0:10].upper():
            return ERC20Asset(token_address=Address("0x" + asset[-40:]))

        else:
            return UnknownAsset(asset=asset)

    def serialize(self) -> str:
        raise Exception("serialize() not implemented")

    def __repr__(self):
        return pformat(vars(self))

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class ERC20Asset(Asset):
    ID = "0xf47261b0"

    def __init__(self, token_address: Address):
        assert(isinstance(token_address, Address))

        self.token_address = token_address

    def serialize(self) -> str:
        return self.ID + self.token_address.address[2:]


class UnknownAsset(Asset):
    def __init__(self, asset: str):
        assert(isinstance(asset, str))

        self.asset = asset

    def serialize(self) -> str:
        return self.asset


class Order:
    def __init__(self, exchange, sender: Address, maker: Address, taker: Address, maker_fee: Wad, taker_fee: Wad,
                 pay_asset: Asset, pay_amount: Wad, buy_asset: Asset, buy_amount: Wad, salt: int, fee_recipient: Address,
                 expiration: int, exchange_contract_address: Address, signature: Optional[str]):

        assert(isinstance(sender, Address))
        assert(isinstance(maker, Address))
        assert(isinstance(taker, Address))
        assert(isinstance(maker_fee, Wad))
        assert(isinstance(taker_fee, Wad))
        assert(isinstance(pay_asset, Asset))
        assert(isinstance(pay_amount, Wad))
        assert(isinstance(buy_asset, Asset))
        assert(isinstance(buy_amount, Wad))
        assert(isinstance(salt, int))
        assert(isinstance(fee_recipient, Address))
        assert(isinstance(expiration, int))
        assert(isinstance(exchange_contract_address, Address))
        assert(isinstance(signature, str) or (signature is None))

        self._exchange = exchange
        self.sender = sender
        self.maker = maker
        self.taker = taker
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.pay_asset = pay_asset
        self.pay_amount = pay_amount
        self.buy_asset = buy_asset
        self.buy_amount = buy_amount
        self.salt = salt
        self.fee_recipient = fee_recipient
        self.expiration = expiration
        self.exchange_contract_address = exchange_contract_address
        self.signature = signature

    # this is not a proper 0x order_id, it's just so `OrderBookManager` can uniquely identify orders
    @property
    def order_id(self):
        return hash(self)

    @property
    def sell_to_buy_price(self) -> Wad:
        return self.pay_amount / self.buy_amount

    @property
    def buy_to_sell_price(self) -> Wad:
        return self.buy_amount / self.pay_amount

    @property
    def remaining_buy_amount(self) -> Wad:
        return self.buy_amount - self._exchange.get_unavailable_buy_amount(self)

    @property
    def remaining_sell_amount(self) -> Wad:
        return self.pay_amount - (self._exchange.get_unavailable_buy_amount(self)
                                  * self.pay_amount / self.buy_amount)

    @staticmethod
    def from_json(exchange, data: dict):
        assert(isinstance(data, dict))

        return Order(exchange=exchange,
                     sender=Address(data['sender']),
                     maker=Address(data['maker']),
                     taker=Address(data['taker']),
                     maker_fee=Wad(int(data['makerFee'])),
                     taker_fee=Wad(int(data['takerFee'])),
                     pay_asset=Asset.deserialize(str(data['makerAssetData'])),
                     pay_amount=Wad(int(data['makerTokenAmount'])),
                     buy_asset=Asset.deserialize(str(data['takerAssetData'])),
                     buy_amount=Wad(int(data['takerTokenAmount'])),
                     salt=int(data['salt']),
                     fee_recipient=Address(data['feeRecipient']),
                     expiration=int(data['expirationUnixTimestampSec']),
                     exchange_contract_address=Address(data['exchangeContractAddress']),
                     signature=data['signature'] if 'signature' in data else None)

    def to_json_without_fees(self) -> dict:
        return {
            "exchangeContractAddress": self.exchange_contract_address.address,
            #TODO shall `sender` be here?
            "maker": self.maker.address,
            "taker": self.taker.address,
            "makerAssetData": self.pay_asset.serialize(),
            "takerAssetData": self.buy_asset.serialize(),
            "makerTokenAmount": str(self.pay_amount.value),
            "takerTokenAmount": str(self.buy_amount.value),
            "expirationUnixTimestampSec": str(self.expiration),
            "salt": str(self.salt)
        }

    def to_json(self) -> dict:
        return {
            "exchangeContractAddress": self.exchange_contract_address.address,
            #TODO shall `sender` be here?
            "maker": self.maker.address,
            "taker": self.taker.address,
            "makerAssetData": self.pay_asset.serialize(),
            "takerAssetData": self.buy_asset.serialize(),
            "feeRecipient": self.fee_recipient.address,
            "makerTokenAmount": str(self.pay_amount.value),
            "takerTokenAmount": str(self.buy_amount.value),
            "makerFee": str(self.maker_fee.value),
            "takerFee": str(self.taker_fee.value),
            "expirationUnixTimestampSec": str(self.expiration),
            "salt": str(self.salt),
            "Signature": self.signature
        }

    def __eq__(self, other):
        assert(isinstance(other, Order))
        return self.sender == other.sender and \
               self.maker == other.maker and \
               self.taker == other.taker and \
               self.maker_fee == other.maker_fee and \
               self.taker_fee == other.taker_fee and \
               self.pay_asset == other.pay_asset and \
               self.pay_amount == other.pay_amount and \
               self.buy_asset == other.buy_asset and \
               self.buy_amount == other.buy_amount and \
               self.salt == other.salt and \
               self.fee_recipient == other.fee_recipient and \
               self.expiration == other.expiration and \
               self.exchange_contract_address == other.exchange_contract_address and \
               self.signature == other.signature

    def __hash__(self):
        return hash((self.sender,
                     self.maker,
                     self.taker,
                     self.maker_fee,
                     self.taker_fee,
                     self.pay_asset,
                     self.pay_amount,
                     self.buy_asset,
                     self.buy_amount,
                     self.salt,
                     self.fee_recipient,
                     self.expiration,
                     self.exchange_contract_address,
                     self.signature))

    def __str__(self):
        return f"('{self.buy_asset}', '{self.buy_amount}'," \
               f" '{self.pay_asset}', '{self.pay_amount}'," \
               f" '{self.exchange_contract_address}', '{self.salt}')"

    def __repr__(self):
        return pformat(vars(self))


#TODO get back to it
class LogCancel:
    def __init__(self, log):
        self.maker = Address(log['args']['maker'])
        self.fee_recipient = Address(log['args']['feeRecipient'])
        self.pay_token = Address(log['args']['makerToken'])
        self.buy_token = Address(log['args']['takerToken'])
        self.cancelled_pay_amount = Wad(int(log['args']['cancelledMakerTokenAmount']))
        self.cancelled_buy_amount = Wad(int(log['args']['cancelledTakerTokenAmount']))
        self.tokens = bytes_to_hexstring(array.array('B', [ord(x) for x in log['args']['tokens']]).tobytes())
        self.order_hash = bytes_to_hexstring(array.array('B', [ord(x) for x in log['args']['orderHash']]).tobytes())
        self.raw = log

    def __repr__(self):
        return pformat(vars(self))


class LogFill:
    def __init__(self, log):
        self.sender = Address(log['args']['senderAddress'])
        self.maker = Address(log['args']['makerAddress'])
        self.taker = Address(log['args']['takerAddress'])
        self.fee_recipient = Address(log['args']['feeRecipientAddress'])
        self.pay_asset = Asset.deserialize(bytes_to_hexstring(array.array('B', [ord(x) for x in log['args']['makerAssetData']]).tobytes()))
        self.buy_asset = Asset.deserialize(bytes_to_hexstring(array.array('B', [ord(x) for x in log['args']['takerAssetData']]).tobytes()))
        self.filled_pay_amount = Wad(int(log['args']['makerAssetFilledAmount']))
        self.filled_buy_amount = Wad(int(log['args']['takerAssetFilledAmount']))
        self.paid_maker_fee = Wad(int(log['args']['makerFeePaid']))
        self.paid_taker_fee = Wad(int(log['args']['takerFeePaid']))
        self.order_hash = bytes_to_hexstring(array.array('B', [ord(x) for x in log['args']['orderHash']]).tobytes())
        self.raw = log

    @classmethod
    def from_event(cls, event: dict):
        assert(isinstance(event, dict))

        topics = event.get('topics')
        if topics and topics[0] == HexBytes('0x0bcc4c97732e47d9946f229edb95f5b6323f601300e4690de719993f3c371129'):
            log_fill_abi = [abi for abi in ZrxExchangeV2.abi if abi.get('name') == 'Fill'][0]
            event_data = get_event_data(log_fill_abi, event)

            return LogFill(event_data)

    def __eq__(self, other):
        assert(isinstance(other, LogFill))
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return pformat(vars(self))


class ZrxExchangeV2(Contract):
    """A client for the 0x V2 exchange contract.

    You can find the `0x V2` exchange contract here:
    <https://etherscan.io/address/0x4f833a24e1f95d70f028921e27040ca56e09ab0b>.

    Attributes:
        web3: An instance of `Web` from `web3.py`.
        address: Ethereum address of the _0x_ `Exchange` contract.
    """

    abi = Contract._load_abi(__name__, 'abi/ExchangeV2.abi')
    bin = Contract._load_bin(__name__, 'abi/ExchangeV2.bin')

    _ZERO_ADDRESS = Address("0x0000000000000000000000000000000000000000")

    ORDER_INFO_TYPE = '(address,address,address,address,uint256,uint256,uint256,uint256,uint256,uint256,bytes,bytes)'

    @staticmethod
    def deploy(web3: Web3, zrx_asset: str):
        """Deploy a new instance of the 0x `Exchange` contract.

        Args:
            web3: An instance of `Web` from `web3.py`.
            zrx_token: The address of the ZRX token this exchange will use.

        Returns:
            A `ZrxExchange` class instance.
        """
        return ZrxExchangeV2(web3=web3,
                           address=Contract._deploy(web3, ZrxExchangeV2.abi, ZrxExchangeV2.bin, []))

    def __init__(self, web3: Web3, address: Address):
        assert(isinstance(web3, Web3))
        assert(isinstance(address, Address))

        self.web3 = web3
        self.address = address
        self._contract = self._get_contract(web3, self.abi, address)

    def zrx_asset(self) -> str:
        """Get the asset data of the ZRX token contract associated with this `ExchangeV2` contract.

        Returns:
            The asset data of the `ZRX` token.
        """
        return str(bytes_to_hexstring(self._contract.call().ZRX_ASSET_DATA()))

    def zrx_token(self) -> Address:
        """Get the address of the ZRX token contract associated with this `ExchangeV2` contract.

        Returns:
            The address of the `ZRX` token.
        """
        return Address("0x" + self.zrx_asset()[-40:])

    def asset_transfer_proxy(self, proxy_id: str) -> Address:
        """Get the address of the `ERC20Proxy` contract associated with this `Exchange` contract.

        Returns:
            The address of the `ERC20Proxy` token.
        """
        assert(isinstance(proxy_id, str))

        return Address(self._contract.call().getAssetProxy(hexstring_to_bytes(proxy_id)))

    def approve(self, tokens: List[ERC20Token], approval_function):
        """Approve the 0x ERC20Proxy contract to fully access balances of specified tokens.

        In case of 0x V2, it's the ERC20Proxy contract that actually gets the approvals,
        not the 0x Exchange contract itself. In addition to the tokens specified as the `tokens`
        parameter, the ZRX token always gets approved as well as without it the 0x Exchange
        contract wouldn't be able to charge maker and taker fees.

        For available approval functions (i.e. approval modes) see `directly` and `via_tx_manager`
        in `pymaker.approval`.

        Args:
            tokens: List of :py:class:`pymaker.token.ERC20Token` class instances.
            approval_function: Approval function (i.e. approval mode).
        """
        assert(isinstance(tokens, list))
        assert(callable(approval_function))

        for token in tokens:  # TODO  + [ERC20Token(web3=self.web3, address=self.zrx_token())]
            approval_function(token, self.asset_transfer_proxy(ERC20Asset.ID), '0x ERC20Proxy contract')

    def past_fill(self, number_of_past_blocks: int, event_filter: dict = None) -> List[LogFill]:
        """Synchronously retrieve past LogFill events.

        `LogFill` events are emitted by the 0x contract every time someone fills an order.

        Args:
            number_of_past_blocks: Number of past Ethereum blocks to retrieve the events from.
            event_filter: Filter which will be applied to returned events.

        Returns:
            List of past `LogFill` events represented as :py:class:`pymaker.zrx.LogFill` class.
        """
        assert(isinstance(number_of_past_blocks, int))
        assert(isinstance(event_filter, dict) or (event_filter is None))

        return self._past_events(self._contract, 'Fill', LogFill, number_of_past_blocks, event_filter)

    def past_cancel(self, number_of_past_blocks: int, event_filter: dict = None) -> List[LogCancel]:
        """Synchronously retrieve past LogCancel events.

        `LogCancel` events are emitted by the 0x contract every time someone cancels an order.

        Args:
            number_of_past_blocks: Number of past Ethereum blocks to retrieve the events from.
            event_filter: Filter which will be applied to returned events.

        Returns:
            List of past `LogCancel` events represented as :py:class:`pymaker.zrx.LogCancel` class.
        """
        assert(isinstance(number_of_past_blocks, int))
        assert(isinstance(event_filter, dict) or (event_filter is None))

        return self._past_events(self._contract, 'LogCancel', LogCancel, number_of_past_blocks, event_filter)

    def create_order(self,
                     pay_asset: Asset,
                     pay_amount: Wad,
                     buy_asset: Asset,
                     buy_amount: Wad,
                     expiration: int) -> Order:
        """Creates a new order.

        The `maker_fee`, `taker_fee` and `fee_recipient` fields are by default set to zero.
        Before signing the order and submitting it to the relayer, they may need to be
        populated using the `calculate_fees()` method of the `ZrxRelayerApi` class.

        Args:
            pay_asset: The asset you want to put on sale.
            pay_amount: Amount of the `pay_asset` token you want to put on sale.
            buy_asset: The asset you want to be paid with.
            buy_amount: Amount of the `buy_asset` you want to receive.
            expiration: Unix timestamp (in seconds) when the order will expire.

        Returns:
            New order as an instance of the :py:class:`pymaker.zrx.Order` class.
        """
        assert(isinstance(pay_asset, Asset))
        assert(isinstance(pay_amount, Wad))
        assert(isinstance(buy_asset, Asset))
        assert(isinstance(buy_amount, Wad))
        assert(isinstance(expiration, int))

        return Order(exchange=self,
                     sender=self._ZERO_ADDRESS,
                     maker=Address(self.web3.eth.defaultAccount),
                     taker=self._ZERO_ADDRESS,
                     maker_fee=Wad(0),
                     taker_fee=Wad(0),
                     pay_asset=pay_asset,
                     pay_amount=pay_amount,
                     buy_asset=buy_asset,
                     buy_amount=buy_amount,
                     salt=self.random_salt(),
                     fee_recipient=self._ZERO_ADDRESS,
                     expiration=expiration,
                     exchange_contract_address=self.address,
                     signature=None)

    def _get_order_info(self, order):
        assert(isinstance(order, Order))

        method_signature = self.web3.sha3(text=f"getOrderInfo({self.ORDER_INFO_TYPE})")[0:4]
        method_parameters = encode_single(f"({self.ORDER_INFO_TYPE})", self._order_tuple(order))

        request = bytes_to_hexstring(method_signature + method_parameters)
        response = self.web3.eth.call({'to': self.address.address, 'data': request})
        response_decoded = decode_single("((uint8,bytes32,uint256))", response)

        return response_decoded

    def get_order_hash(self, order: Order) -> str:
        """Calculates hash of an order.

        Args:
            order: Order you want to calculate the hash of.

        Returns:
            Order hash as a hex string starting with `0x`.
        """
        assert(isinstance(order, Order))

        # the hash depends on the exchange contract address as well
        assert(order.exchange_contract_address == self.address)

        return bytes_to_hexstring(self._get_order_info(order)[0][1])

    def get_unavailable_buy_amount(self, order: Order) -> Wad:
        """Return the order amount which was either taken or cancelled.

        Args:
            order: Order you want to get the unavailable amount of.

        Returns:
            The unavailable amount of the order (i.e. the amount which was either taken or cancelled),
            expressed in terms of the `buy_token` token.
        """
        assert(isinstance(order, Order))

        order_info = self._get_order_info(order)[0]

        if order_info[0] in [0,         # INVALID,                     // Default value
                             1,         # INVALID_MAKER_ASSET_AMOUNT,  // Order does not have a valid maker asset amount
                             2,         # INVALID_TAKER_ASSET_AMOUNT,  // Order does not have a valid taker asset amount
                             4,         # EXPIRED,                     // Order has already expired
                             5,         # FULLY_FILLED,                // Order is fully filled
                             6]:        # CANCELLED                    // Order has been cancelled
            return order.buy_amount

        else:
            return Wad(order_info[2])

    def sign_order(self, order: Order) -> Order:
        """Signs an order so it can be submitted to the relayer.

        Order will be signed by the `web3.eth.defaultAccount` account.

        Args:
            order: Order you want to sign.

        Returns:
            Signed order. Copy of the order passed as a parameter with the `signature` field filled with signature.
        """
        assert(isinstance(order, Order))

        signature = eth_sign(hexstring_to_bytes(self.get_order_hash(order)), self.web3)
        v, r, s = to_vrs(signature)

        signed_order = copy.copy(order)
        signed_order.signature = bytes_to_hexstring(bytes([v])) + \
                                 bytes_to_hexstring(r)[2:] + \
                                 bytes_to_hexstring(s)[2:] + \
                                 "03"  # EthSign

        return signed_order

    def fill_order(self, order: Order, fill_buy_amount: Wad) -> Transact:
        """Fills an order.

        Args:
            order: The order to be filled.
            fill_buy_amount: The amount (in terms of `buy_token` of the original order) to be filled.

        Returns:
            A :py:class:`pymaker.Transact` instance, which can be used to trigger the transaction.
        """
        assert(isinstance(order, Order))
        assert(isinstance(fill_buy_amount, Wad))

        return Transact(self, self.web3, self.abi, self.address, self._contract, 'fillOrder',
                        [self._order_addresses(order), self._order_values(order), fill_buy_amount.value,
                         True, order.ec_signature_v,
                         hexstring_to_bytes(order.ec_signature_r),
                         hexstring_to_bytes(order.ec_signature_s)])

    def cancel_order(self, order: Order) -> Transact:
        """Cancels an order.

        Args:
            order: Order you want to cancel.

        Returns:
            A :py:class:`pymaker.Transact` instance, which can be used to trigger the transaction.
        """
        assert(isinstance(order, Order))

        method_signature = self.web3.sha3(text=f"cancelOrder({self.ORDER_INFO_TYPE})")[0:4]
        method_parameters = encode_single(f"({self.ORDER_INFO_TYPE})", self._order_tuple(order))

        request = bytes_to_hexstring(method_signature + method_parameters)

        return Transact(self, self.web3, self.abi, self.address, self._contract, None,
                        [request])

    @staticmethod
    def _order_tuple(order):
        return [(order.maker.address,
                 order.taker.address,
                 order.fee_recipient.address,
                 order.sender.address,
                 order.pay_amount.value,
                 order.buy_amount.value,
                 order.maker_fee.value,
                 order.taker_fee.value,
                 order.expiration,
                 order.salt,
                 hexstring_to_bytes(order.pay_asset.serialize()),
                 hexstring_to_bytes(order.buy_asset.serialize()))]

    #TODO to be removed
    @staticmethod
    def _order_values(order):
        return [order.pay_amount.value,
                order.buy_amount.value,
                order.maker_fee.value,
                order.taker_fee.value,
                order.expiration,
                order.salt]

    #TODO to be removed
    @staticmethod
    def _order_addresses(order):
        return [order.maker.address,
                order.taker.address,
                order.pay_token.address,
                order.buy_token.address,
                order.fee_recipient.address]

    @staticmethod
    def random_salt() -> int:
        return random.randint(1, 2**256 - 1)

    def __repr__(self):
        return f"ZrxExchange('{self.address}')"


class ZrxRelayerApi:
    """A client for the Standard 0x Relayer API V0.

    <https://github.com/0xProject/standard-relayer-api>

    Attributes:
        exchange: The 0x Exchange contract.
        api_server: Base URL of the Standard Relayer API server.
    """
    logger = logging.getLogger()
    timeout = 15.5

    def __init__(self, exchange: ZrxExchangeV2, api_server: str):
        assert(isinstance(exchange, ZrxExchangeV2))
        assert(isinstance(api_server, str))

        self.exchange = exchange
        self.api_server = api_server

    def get_orders(self, pay_token: Address, buy_token: Address, per_page: int = 100) -> List[Order]:
        """Returns active orders filtered by token pair (one side).

        In order to get them, issues a `/v0/orders` call to the Standard Relayer API.

        Args:
            per_page: Maximum number of orders to be downloaded per page. 0x Standard Relayer API
                limitation is 100, but some relayers can handle more so that's why this parameter
                is exposed.

        Returns:
            Orders, as a list of instances of the :py:class:`pymaker.zrx.Order` class.
        """
        assert(isinstance(pay_token, Address))
        assert(isinstance(buy_token, Address))

        url = f"{self.api_server}/v0/orders?" \
              f"exchangeContractAddress={self.exchange.address.address}&" \
              f"makerTokenAddress={pay_token.address}&" \
              f"takerTokenAddress={buy_token.address}&" \
              f"per_page={per_page}"

        response = requests.get(url, timeout=self.timeout)
        if not response.ok:
            raise Exception(f"Failed to fetch 0x orders from the relayer: {http_response_summary(response)}")

        return list(map(lambda item: Order.from_json(self.exchange, item), response.json()))

    def get_orders_by_maker(self, maker: Address, per_page: int = 100) -> List[Order]:
        """Returns all active orders created by `maker`.

        In order to get them, issues a `/v0/orders` call to the Standard Relayer API.

        Args:
            maker: Address of the `maker` to filter the orders by.
            per_page: Maximum number of orders to be downloaded per page. 0x Standard Relayer API
                limitation is 100, but some relayers can handle more so that's why this parameter
                is exposed.

        Returns:
            Active orders created by `maker`, as a list of instances of the :py:class:`pymaker.zrx.Order` class.
        """
        assert(isinstance(maker, Address))

        url = f"{self.api_server}/v0/orders?" \
              f"exchangeContractAddress={self.exchange.address.address}&" \
              f"maker={maker.address}&" \
              f"per_page={per_page}"

        response = requests.get(url, timeout=self.timeout)
        if not response.ok:
            raise Exception(f"Failed to fetch 0x orders from the relayer: {http_response_summary(response)}")

        return list(map(lambda item: Order.from_json(self.exchange, item), response.json()))

    def calculate_fees(self, order: Order) -> Order:
        """Takes and order and returns the same order with proper relayer fees.

        Issues a call to the `/v0/fees` endpoint of the Standard Relayer API, as a result of it
        new order is returned being the copy of the original one with the `maker_fee`, `taker_fee`
        and `fee_recipient` fields filled in according to the relayer.

        Relayers will very likely reject orders submitted if proper fees are not set first.
        The standard approach is to call `calculate_fees()` first and then call `submit_order()`
        passing the order received from `calculate_fees()` as parameter.

        Args:
            order: Order which should have fees calculated. The values of `maker_fee`, `taker_fee`
                and `fee_recipient` are irrelevant and may as well be zeros as they will be overwritten
                by this method anyway.

        Returns:
            Copy of the order received as a parameter with the `maker_fee`, `taker_fee` and `fee_recipient`
            fields updated according to the relayer.
        """
        assert(isinstance(order, Order))

        response = requests.post(f"{self.api_server}/v0/fees", json=order.to_json_without_fees(), timeout=self.timeout)
        if response.status_code == 200:
            data = response.json()

            order_with_fees = copy.copy(order)
            order_with_fees.maker_fee = Wad(int(data['makerFee']))
            order_with_fees.taker_fee = Wad(int(data['takerFee']))
            order_with_fees.fee_recipient = Address(data['feeRecipient'])
            return order_with_fees
        else:
            raise Exception(f"Failed to fetch fees for 0x order: {http_response_summary(response)}")

    def submit_order(self, order: Order) -> bool:
        """Submits the order to the relayer.

        Posts the order to the `/v0/order` endpoint of the Standard Relayer API

        Args:
            order: Order to be submitted.

        Return:
            `True` if order submission was successful. `False` otherwise.
        """
        assert(isinstance(order, Order))

        response = requests.post(f"{self.api_server}/v0/order", json=order.to_json(), timeout=self.timeout)
        if response.status_code in [200, 201]:
            self.logger.info(f"Placed 0x order: {order}")
            return True
        else:
            self.logger.warning(f"Failed to place 0x order: {http_response_summary(response)}")
            return False

    def __repr__(self):
        return f"ZrxRelayerApi()"