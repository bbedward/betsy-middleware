import asyncio
import os
import json
import nanolib

from aiohttp import log, ClientSession, WSMsgType, WSMessage, web
from aiographql.client import (GraphQLClient, GraphQLRequest)

class ConnectionClosed(Exception):
    pass

class BPOWClient():
    NANO_DIFFICULTY_CONST = 'ffffffc000000000'

    def __init__(self, bpow_url : str, key : str, app : web.Application, force_nano_difficulty: bool = False):
        self.bpow_url = bpow_url
        self.key = key
        self.id = 0
        self.app = app
        self.ws = None # None when socket is closed
        self.difficulty = 64 if force_nano_difficulty else 1
        self.bpow_client = GraphQLClient(
            endpoint=bpow_url,
            headers={"Authorization": f"{key}"},
        )

    async def get_id(self) -> int:
        """Get ID that should be used for this request"""
        self.id += 1
        return self.id

    async def request_work(self, hash: str, difficulty: str = None):
        difficulty_multiplier = self.difficulty if difficulty is None else int(nanolib.work.derive_work_multiplier(difficulty, base_difficulty="fffffe0000000000"))
        """Request work, return ID of the request"""
        request = GraphQLRequest(
            validate=False,
            query="""
                mutation($hash:String!, $difficultyMultiplier: Int!) {
                    workGenerate(input:{hash:$hash, difficultyMultiplier:$difficultyMultiplier})
                }
            """,
            variables={"hash": hash, "difficultyMultiplier":  difficulty_multiplier}
        )
        return await self.bpow_client.query(request=request)
