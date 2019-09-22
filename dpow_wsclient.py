import asyncio
import os
import json

from aiohttp import log, ClientSession, WSMsgType, WSMessage, web

class ConnectionClosed(Exception):
    pass

class DPOWClient():
    NANO_DIFFICULTY_CONST = 'ffffffc000000000'

    def __init__(self, dpow_url : str, user : str, key : str, app : web.Application, force_nano_difficulty: bool = False):
        self.dpow_url = dpow_url
        self.user = user
        self.key = key
        self.id = 0
        self.app = app
        self.ws = None # None when socket is closed
        self.difficulty = DPOWClient.NANO_DIFFICULTY_CONST if force_nano_difficulty else None

    async def open_connection(self):
        """Create the websocket connection to dPOW service"""
        session = ClientSession()
        async with session.ws_connect(self.dpow_url) as ws:
            self.ws = ws
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    if msg.data == 'close':
                        await ws.close()
                    else:
                        # Handle Reply
                        log.server_logger.debug(f'WS Message Received {msg.data}')
                        msg_json = json.loads(msg.data)
                        await self.app['redis'].rpush(f'dpow_{msg_json["id"]}', msg.data)
                        await self.app['redis'].expire(f'dpow_{msg_json["id"]}', 60)
                elif msg.type == WSMsgType.CLOSE:
                    log.server_logger.info('WS Connection closed normally')
                    break
                elif msg.type == WSMsgType.ERROR:
                    log.server_logger.info('WS Connection closed with error %s', ws.exception())
                    break

    async def get_id(self) -> int:
        """Get ID that should be used for this request"""
        self.id += 1
        return self.id

    async def request_work(self, hash: str, id: int, difficulty: str = None):
        """Request work, return ID of the request"""
        try:
            if self.ws is None or self.ws.closed:
                raise ConnectionClosed()
            req = {
                "user": self.user,
                "api_key": self.key,
                "hash": hash,
                "id": id
            }
            if difficulty is not None:
                req['difficulty'] = difficulty
            elif self.difficulty is not None:
                req['difficulty'] = self.difficulty
            await self.ws.send_str(json.dumps(req))
        except Exception:
            raise ConnectionClosed()
