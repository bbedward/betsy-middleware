import asyncio
import logging
import json
import aioredis
import datetime
from aiohttp import ClientSession, log, web
from logging.handlers import TimedRotatingFileHandler, WatchedFileHandler
import uvloop
import sys

import settings

uvloop.install()

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

PRECACHE_Q_KEY = 'betsy_pcache_q'

### PEER-related functions

async def json_post(url, request, timeout=settings.TIMEOUT, app=None):
    try:
        async with ClientSession() as session:
            async with session.post(url, json=request, timeout=timeout) as resp:
                return await resp.json(content_type=None)
    except Exception as e:
        log.server_logger.exception(e)
        if app is not None:
            app['failover'] = True
            if app['failover_dt'] is None:
                app['failover_dt'] = datetime.datetime.utcnow()
        return None

async def work_cancel(hash):
    """RPC work_cancel"""
    request = {"action":"work_cancel", "hash":hash}
    tasks = []
    for p in settings.WORK_SERVERS:
        if '?key' not in p: # dPOW doesn't support work_cancel
            tasks.append(json_post(p, request))
    # Don't care about waiting for any responses on work_cancel
    for t in tasks:
        asyncio.ensure_future(t)

async def work_generate(hash, app):
    """RPC work_generate"""
    redis = app['redis']
    request = {"action":"work_generate", "hash":hash}
    tasks = []
    for p in settings.WORK_SERVERS:
        split_p = p.split('?key=')
        if len(split_p) > 1:
            request['key'] = split_p[1]
        else:
            if 'key' in request:
                del request['key']
        tasks.append(json_post(split_p[0], request, app=app))

    if settings.NODE_FALLBACK and app['failover'] and NODE_URL and NODE_PORT:
        # Failover to the node since we have some requests that are failing
        # If its been an hour since a work request failed, disable failover mode
        if app['failover_dt'] is not None and (datetime.datetime.utcnow() - app['failover_dt']).total_seconds() > 3600:
            app['failover'] = False
            app['failover_dt'] = None
        else:
            tasks.append(json_post(f"http://{NODE_URL}:{NODE_PORT}", request, timeout=30))

    while len(tasks):
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = task.result()
            if result is not None and 'work' in result:
                asyncio.ensure_future(work_cancel(hash))
                await redis.set(hash, result['work'], expire=600000) # Cache work
                return task.result()

    # Fallback method
    if settings.NODE_FALLBACK and NODE_URL and NODE_PORT:
        return await json_post(f"http://{NODE_URL}:{NODE_PORT}", request, timeout=30)

    return None

async def precache_queue_process(app):
    while True and settings.PRECACHE:
        if app['busy']:
            # Wait and try precache again later
            await asyncio.sleep(5)
            continue

        # Pop item off the queue
        hash = await app['redis'].lpop(PRECACHE_Q_KEY)
        if hash is None:
            await asyncio.sleep(3) # Delay before checking again
            continue
        # See if already have this hash cached
        have_pow = await app['redis'].get(hash)
        if have_pow is not None:
            continue # Already cached
        log.server_logger.info(f"precaching {hash}")
        await work_generate(hash, app)

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
    try:
        request.app['busy'] = True # Halts the precaching process
        respjson = await work_generate(requestjson['hash'], request.app)
        if respjson is None:
            request.app['busy'] = False
            return web.HTTPInternalServerError(reason="Couldn't generate work")
        request.app['busy'] = False
        return web.json_response(respjson)
    except Exception as e:
        request.app['busy'] = False
        log.server_logger.exception(e)
        return web.HTTPInternalServerError(reason=str(sys.exc_info()))

async def callback(request):
    requestjson = await request.json()
    hash = requestjson['hash']
    log.server_logger.debug(f"callback received {hash}")
    # Forward callback
    for c in CALLBACK_FORWARDS:
        await asyncio.ensure_future(json_post(c, requestjson))
    # Precache POW if necessary
    if not settings.PRECACHE:
        return web.Response(status=200)
    block = json.loads(requestjson['block'])
    if 'previous' not in block:
        log.server_logger.info(f"previous not in block from callback {str(block)}")
        return web.Response(status=200)
    previous_pow = await request.app['redis'].get(block['previous'])
    if previous_pow is None:
        return web.Response(status=200) # They've never requested work from us before so we don't care
    else:
        await request.app['redis'].rpush(PRECACHE_Q_KEY, hash)
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
        app['precache_task'] = app.loop.create_task(precache_queue_process(app))

    async def clear_queue(app):
        app['precache_task'].cancel()
        await app['precache_task']

    if settings.DEBUG:
        logging.basicConfig(level=logging.DEBUG)
    else:
        root = logging.getLogger('aiohttp.server')
        logging.basicConfig(level=logging.INFO)
        handler = WatchedFileHandler(settings.LOG_FILE)
        formatter = logging.Formatter("%(asctime)s;%(levelname)s;%(message)s", "%Y-%m-%d %H:%M:%S %z")
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root.addHandler(TimedRotatingFileHandler(settings.LOG_FILE, when="d", interval=1, backupCount=100))  
    app = web.Application()
    app['busy'] = False
    app['failover'] = False
    app['failover_dt'] = None
    app.add_routes([web.post('/', rpc)])
    app.add_routes([web.post('/callback', callback)])
    app.on_startup.append(open_redis)
    app.on_startup.append(init_queue)
    app.on_cleanup.append(clear_queue)
    app.on_shutdown.append(close_redis)

    return app

work_app = asyncio.get_event_loop().run_until_complete(get_app())