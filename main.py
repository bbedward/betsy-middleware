import asyncio
import logging

import aioredis
from aiohttp import ClientSession, log, web

import settings

### PEER-related functions

async def json_get(url, request):
    try:
        async with ClientSession() as session:
            async with session.post(url, json=request, timeout=settings.TIMEOUT) as resp:
                return await resp.json()
    except Exception as e:
        log.server_logger.error(e)
        return None

async def work_cancel(hash):
    """RPC work_cancel"""
    request = {"action":"work_cancel", "hash":hash}
    tasks = []
    for p in settings.WORK_SERVERS:
        tasks.append(json_get(p, request))
    # Don't care about waiting for any responses on work_cancel
    for t in tasks:
        asyncio.ensure_future(t)

async def work_generate(hash):
    """RPC work_generate"""
    request = {"action":"work_generate", "hash":hash}
    tasks = []
    for p in settings.WORK_SERVERS:
        tasks.append(json_get(p, request))

    while len(tasks):
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = task.result()
            if result is not None:
                work_cancel(hash)
                return task.result()
    # Fallback method
    if settings.NODE_FALLBACK:
        return await json_get(f"https://{settings.NODE_URL}:{settings.NODE_PORT}", request)
    return None

### END PEER-related functions

### API

async def rpc(request):
    requestjson = await request.json()
    if 'action' not in requestjson or requestjson['action'] != 'work_generate':
        return web.HTTPBadRequest(reason='invalid action')
    elif 'hash' not in requestjson:
        return web.HTTPBadRequest(reason='Missing hash in request')

    log.server_logger.debug(f"Received request {str(requestjson)}")
    respjson = await work_generate(requestjson['hash'])
    if respjson is None:
        return web.HTTPError(reason="Couldn't generate work")
    return web.json_response(respjson)

### END API

### APP setup

async def get_app():
    async def close_redis(app):
        """Close redis connection"""
        log.server_logger.info('Closing redis connection')
        app['redis'].close()

    async def open_redis(app):
        """Open redis connection"""
        log.server_logger.info("Opening redis connection")
        app['redis'] = await aioredis.create_redis(('localhost', 6379),
                                                db=1, encoding='utf-8')

    if settings.DEBUG:
        logging.basicConfig(level='DEBUG')
    else:
        logging.basicConfig(level='INFO')
    app = web.Application()
    app.add_routes([web.post('/', rpc)])
    app.on_startup.append(open_redis)
    app.on_shutdown.append(close_redis)

    return app

work_app = get_app()