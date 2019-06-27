import asyncio
import os
import json

import aiohttp

class DPOWClient():
    def __init__(self, dpow_url : str, user : str, key : str, debug : bool = False):
        self.dpow_url = dpow_url
        self.debug = debug
        self.user = user
        self.key = key
        self.id = 0
        self.ws = None # None when socket is closed

    async def open_connection(self):
        """Create the websocket connection to dPOW service"""
        session = aiohttp.ClientSession()
        async with session.ws_connect(self.dpow_url) as ws:
            self.ws = ws
            async for msg in ws:
                if self.debug:
                    print(f'Message received {msg}')
                # Handle dPow message TODO
                if msg.type in (aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR):
                    self.ws = None
                    break

    async def request_work(self, hash: str) -> int:
        """Request work, return ID of the request"""
        if self.ws is None:
            raise Exception(f"Connection to {self.dpow_url} closed")
        req = {
            "user": self.user,
            "api_key": self.key,
            "hash": hash,
            "id": self.id
        }
        self.ws.send_str(json.dumps(req))
        self.id += 1
        return self.id