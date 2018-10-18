import asyncio
import logging
import json
import aioredis
from aiohttp import ClientSession, log, web

import settings

# Callback forwarding
CALLBACK_FORWARDS = []
try:
	CALLBACK_FORWARDS=settings.CALLBACK_FORWARD
except Exception:
	pass

# Node configuration
NODE_URL = None
NODE_PORT = None
try:
    NODE_URL=settings.NODE_URL
    NODE_PORT=settings.NODE_PORT
except Exception:
    pass

### PEER-related functions

async def json_get(url, request, timeout=settings.TIMEOUT):
    try:
        async with ClientSession() as session:
            async with session.post(url, json=request, timeout=timeout) as resp:
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
        split_p = p.split('?key=')
        if len(split_p) > 1:
            request['key'] = split_p[1]
        else:
            if 'key' in request:
                del request['key']
        tasks.append(json_get(split_p[0], request))

    while len(tasks):
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = task.result()
            if result is not None:
                asyncio.ensure_future(work_cancel(hash))
                return task.result()
    # Fallback method
    if settings.NODE_FALLBACK and NODE_URL and NODE_PORT:
        return await json_get(f"http://{NODE_URL}:{NODE_PORT}", request, timeout=300)
    return None

async def precache_queue_process(app, queue):
    while True:
        item = await queue.get()
        log.server_logger.info(f"precaching {str(item)}")
        if item is None:
            continue
        work_response = await work_generate(str(item))
        if work_response is None or 'work' not in work_response:
            continue
        await app['redis'].set(str(item), work_response['work'], expire=600000)

### END PEER-related functions

### API

async def rpc(request):
    requestjson = await request.json()
    log.server_logger.info(f"Received request {str(requestjson)}")
    if 'action' not in requestjson or requestjson['action'] != 'work_generate':
        return web.HTTPBadRequest(reason='invalid action')
    elif 'hash' not in requestjson:
        return web.HTTPBadRequest(reason='Missing hash in request')

    # See if work is in cache
    work = await request.app['redis'].get(requestjson['hash'])
    if work is not None:
        return web.json_response({"work":work})

    # Not in cache, request it from peers
    respjson = await work_generate(requestjson['hash'])
    if respjson is None:
        return web.HTTPError(reason="Couldn't generate work")
    else:
        await request.app['redis'].set(requestjson['hash'], respjson['work'], expire=600000)
    return web.json_response(respjson)

async def callback(request):
    requestjson = json.loads(await request.read())
    hash = requestjson['hash']
    log.server_logger.debug(f"callback received {hash}")
    # Forward callback
    for c in CALLBACK_FORWARDS:
        await asyncio.ensure_future(json_get(c, requestjson))
    # Precache POW if necessary
    if not settings.PRECACHE:
        return web.Response(status=200)
    have_pow = await request.app['redis'].get(hash)
    if have_pow is not None:
        return web.Response(status=200) # Already computed POW for this hash
    block = json.loads(requestjson['block'])
    if 'previous' not in block:
        log.server_logger.info(f"previous not in block from callback {str(block)}")
        return web.Response(status=200)
    previous_pow = await request.app['redis'].get(block['previous'])
    if previous_pow is None:
        return web.Response(status=200) # They've never requested work from us before so we don't care
    else:
        request.app['precache_queue'].put(block['previous'])
    return web.Response(status=200)

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

    async def init_queue(app):
        """Initialize task queue"""
        app['precache_queue'] = asyncio.Queue(loop=app.loop)
        app['precache_task'] = app.loop.create_task(precache_queue_process(app, app['precache_queue']))

    async def clear_queue(app):
        app['precache_task'].cancel()
        await app['precache_task']

    if settings.DEBUG:
        logging.basicConfig(level='DEBUG')
    else:
        logging.basicConfig(level='INFO')
    app = web.Application()
    app.add_routes([web.post('/', rpc)])
    app.add_routes([web.post('/callback', callback)])
    app.on_startup.append(open_redis)
    app.on_startup.append(init_queue)
    app.on_cleanup.append(clear_queue)
    app.on_shutdown.append(close_redis)

    return app

work_app = asyncio.get_event_loop().run_until_complete(get_app())