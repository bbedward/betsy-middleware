#!/usr/bin/env python
from dotenv import load_dotenv
load_dotenv()

import argparse
import asyncio
import ipaddress
import logging
import json
import aioredis
import datetime
from aiohttp import ClientSession, log, web
from logging.handlers import TimedRotatingFileHandler, WatchedFileHandler
import uvloop
import sys
import os

from dpow_wsclient import DPOWClient, ConnectionClosed

uvloop.install()

# Configuration arguments

parser = argparse.ArgumentParser(description="Betsy Work Distributer, Callback Forwarder, Work Precacher (NANO/BANANO)")
parser.add_argument('--host', type=str, help='Host for betsy to listen on', default='127.0.0.1')
parser.add_argument('--port', type=int, help='Port for betsy to listen on', default='5555')
parser.add_argument('--node-url', type=str, help='Node RPC Connection String', default='[::1]:7072')
parser.add_argument('--log-file', type=str, help='Log file location', default='/tmp/betsy.log')
parser.add_argument('--work-urls', type=list, nargs='*', help='Work servers to send work too (NOT for dPOW')
parser.add_argument('--callbacks', type=list, nargs='*', help='Endpoints to forward node callbacks to')
parser.add_argument('--dpow-url', type=str, help='dPOW HTTP URL', default='https://dpow.nanocenter.org/service/')
parser.add_argument('--dpow-ws-url', type=str, help='dPOW Web Socket URL', default='wss://dpow.nanocenter.org/service_ws/')
parser.add_argument('--precache', action='store_true', help='Enables work precaching if specified (does not apply to dPOW)', default=False)
parser.add_argument('--debug', action='store_true', help='Runs in debug mode if specified', default=False)
options = parser.parse_args()

# Callback forwarding
CALLBACK_FORWARDS = options.callbacks

# Work URLs
WORK_URLS = options.work_urls if options.work_urls is not None else []

# Precache
PRECACHE = options.precache

# Log
LOG_FILE = options.log_file

# Node URL
NODE_CONNSTR = options.node_url
NODE_FALLBACK = False
if NODE_CONNSTR is not None:
    try:
        LISTEN_HOST = str(ipaddress.ip_address(options.host))
        LISTEN_PORT = int(options.port)
        NODE_URL = options.node_url.split(':')[0]
        NODE_PORT = options.node_url.split(':')[1]
        NODE_FALLBACK = True
    except Exception:
        print(f"Invalid node connection string, should be url:port, not {NODE_CONNSTR}")
        parser.print_help()
        sys.exit(1)

DPOW_URL = options.dpow_url
DPOW_WS_URL = options.dpow_ws_url
DPOW_USER = os.getenv('DPOW_USER', None)
DPOW_KEY = os.getenv('DPOW_KEY', None)
DPOW_ENABLED = DPOW_USER is not None and DPOW_KEY is not None

DEBUG = options.debug

# Constants
PRECACHE_Q_KEY = 'betsy_pcache_q'

### PEER-related functions

async def json_post(url, request, timeout=10, app=None, dontcare=False):
    try:
        async with ClientSession() as session:
            async with session.post(url, json=request, timeout=timeout) as resp:
                if dontcare:
                    return resp.status
                else:
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
    for p in WORK_URLS:
        tasks.append(json_post(p, request))
    # Don't care about waiting for any responses on work_cancel
    for t in tasks:
        asyncio.ensure_future(t)

async def work_generate(hash, app):
    """RPC work_generate"""
    redis = app['redis']
    request = {"action":"work_generate", "hash":hash}
    tasks = []
    dpow_id = -1
    for p in WORK_URLS:
        tasks.append(json_post(p, request, app=app))
    if DPOW_ENABLED:
        dpow_id = await app['dpow'].get_id()
        success = await app['dpow'].request_work(hash, dpow_id)
        if success is ConnectionClosed:
            app.loop.create_task(app['dpow'].open_connection())
        else:
            tasks.append(app['redis'].blpop(f'dpow_{dpow_id}', timeout=30))

    if NODE_FALLBACK and app['failover']:
        # Failover to the node since we have some requests that are failing
        # If its been an hour since a work request failed, disable failover mode
        if app['failover_dt'] is not None and (datetime.datetime.utcnow() - app['failover_dt']).total_seconds() > 3600:
            app['failover'] = False
            app['failover_dt'] = None
        else:
            tasks.append(json_post(f"http://{NODE_CONNSTR}", request, timeout=30))

    while len(tasks):
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                result = task.result()
                if result is str:
                    result = json.loads(result)
                if result is not None and 'work' in result:
                    asyncio.ensure_future(work_cancel(hash))
                    await redis.set(hash, result['work'], expire=600000) # Cache work
                    return task.result()
            except Exception as exc:
                log.server_logger.exception("Task raised an exception");
    # Fallback method
    if NODE_FALLBACK:
        return await json_post(f"http://{NODE_CONNSTR}", request, timeout=30)

    return None

async def precache_queue_process(app):
    while True and PRECACHE:
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
        await asyncio.ensure_future(json_post(c, requestjson, dontcare=True))
    # Precache POW if necessary
    if not PRECACHE:
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

    async def init_dpow(app):
        if DPOW_ENABLED:
            app['dpow'] = DPOWClient(DPOW_WS_URL, DPOW_USER, DPOW_KEY, app)
            app.loop.create_task(app['dpow'].open_connection())
        else:
            app['dpow'] = None

    async def init_queue(app):
        """Initialize task queue"""
        app['precache_task'] = app.loop.create_task(precache_queue_process(app))

    async def clear_queue(app):
        app['precache_task'].cancel()
        await app['precache_task']

    if DEBUG:
        logging.basicConfig(level=logging.DEBUG)
    else:
        root = logging.getLogger('aiohttp.server')
        logging.basicConfig(level=logging.INFO)
        handler = WatchedFileHandler(LOG_FILE)
        formatter = logging.Formatter("%(asctime)s;%(levelname)s;%(message)s", "%Y-%m-%d %H:%M:%S %z")
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root.addHandler(TimedRotatingFileHandler(LOG_FILE, when="d", interval=1, backupCount=100))  
    app = web.Application()
    app['busy'] = False
    app['failover'] = False
    app['failover_dt'] = None
    app.add_routes([web.post('/', rpc)])
    app.add_routes([web.post('/callback', callback)])
    app.on_startup.append(open_redis)
    app.on_startup.append(init_queue)
    app.on_startup.append(init_dpow)
    app.on_cleanup.append(clear_queue)
    app.on_shutdown.append(close_redis)

    return app

work_app = asyncio.get_event_loop().run_until_complete(get_app())

def main():
    """Main application loop"""

    # Start web/ws server
    async def start():
        runner = web.AppRunner(work_app)
        await runner.setup()
        site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
        await site.start()

    async def end():
        await work_app.shutdown()

    asyncio.get_event_loop().run_until_complete(start())

    # Main program
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.get_event_loop().run_until_complete(end())

    asyncio.get_event_loop().close()

if __name__ == "__main__":
    main()