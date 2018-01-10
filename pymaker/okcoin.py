# -*- coding: utf-8 -*-

import http.client
import urllib
import json
import hashlib


class OKCoinApi:
    """OKCoin and OKEX API interface.

    Developed according to the following manual:
    <https://www.okex.com/intro_apiOverview.html>.

    Inspired by the following example:
    <https://github.com/OKCoin/rest>, <https://github.com/OKCoin/rest/tree/master/python>.
    """

    def __init__(self, api_server: str, api_key: str, secret_key: str, timeout: float):
        assert(isinstance(api_server, str))
        assert(isinstance(api_key, str))
        assert(isinstance(secret_key, str))
        assert(isinstance(timeout, float))

        self.api_server = api_server
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout

    def ticker(self, symbol):
        return self._http_get("/api/v1/ticker.do", 'symbol=%(symbol)s' % {'symbol':symbol})

    def depth(self, symbol):
        return self._http_get("/api/v1/depth.do", 'symbol=%(symbol)s' % {'symbol':symbol})

    def trades(self, symbol):
        return self._http_get("/api/v1/trades.do", 'symbol=%(symbol)s' % {'symbol':symbol})
    
    def user_info(self):
        return self._http_post("/api/v1/userinfo.do", {})

    def place_order(self, symbol, tradeType, price='', amount=''):
        params = {
            'symbol':symbol,
            'type':tradeType
        }
        if price:
            params['price'] = price
        if amount:
            params['amount'] = amount

        return self._http_post("/api/v1/trade.do", params)

    def batch_place_order(self, symbol, trade_type, orders_data):
        params = {
            'symbol':symbol,
            'type':trade_type,
            'orders_data':orders_data
        }
        return self._http_post("/api/v1/batch_trade.do", params)

    def cancel_order(self, symbol, order_id):
        params = {
             'symbol':symbol,
             'order_id':order_id
        }
        return self._http_post("/api/v1/cancel_order.do", params)

    def orderinfo(self, symbol, order_id):
        params = {
         'symbol':symbol,
         'order_id':order_id
        }
        return self._http_post("/api/v1/order_info.do", params)

    def ordersinfo(self, symbol, order_id, trade_type):
        params = {
         'symbol':symbol,
         'order_id':order_id,
         'type':trade_type
        }
        return self._http_post("/api/v1/orders_info.do", params)

    def order_history(self, symbol, status, current_page, page_length):
        params = {
          'symbol':symbol,
          'status':status,
          'current_page':current_page,
          'page_length':page_length
        }
        return self._http_post("/api/v1/order_history.do", params)

    def _create_signature(self, params):
        sign = ''
        for key in sorted(params.keys()):
            sign += key + '=' + str(params[key]) + '&'
        data = sign + 'secret_key=' + self.secret_key
        return hashlib.md5(data.encode("utf8")).hexdigest().upper()

    def _http_get(self, resource: str, params: str):
        assert(isinstance(resource, str))
        assert(isinstance(params, str))

        conn = http.client.HTTPSConnection(self.api_server, timeout=self.timeout)
        conn.request("GET", resource + '?' + params)
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        return json.loads(data)

    def _http_post(self, resource: str, params: dict):
        assert(isinstance(resource, str))
        assert(isinstance(params, dict))

        headers = {
            "Content-type": "application/x-www-form-urlencoded",
        }
        conn = http.client.HTTPSConnection(self.api_server, timeout=self.timeout)
        params['api_key'] = self.api_key
        params['sign'] = self._create_signature(params)
        temp_params = urllib.parse.urlencode(params)
        conn.request("POST", resource, temp_params, headers)
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        params.clear()
        conn.close()
        return data
