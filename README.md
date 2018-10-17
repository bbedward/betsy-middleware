# NANO/BANANO Work/Callback Middleware (Betsy)

## What is Betsy?

Middleware for banano/nano applications and nodes.

## Features

- Take a work-peer list, and handle making asynchronous work_generate
  and work_cancel requests. With an optional fallback option to use the local node in the event of work
  peer downtime
- Support for distributed POW, specify the url with a key parameter. e.g. http://1.2.3.4:8000?key=mysecretkey
- Work caching
- Work precaching (requires callback)
- Callback forwarding (forward the node callback to any other number of services)

## Configuration

1.) Copy settings example

`cp settings.py.example settings.py`

2.) Setup work peer list - if you want to call the work_generate API, ex.

```
WORK_SERVERS=['https://1.2.3.4:5000', 'https://5.6.7.8:5001?key=123456', 'http://[::1]:7076']
```

3.) Setup local node configuration if you want to use local node as the backup work peer option

```
NODE_URL='[::1]'
NODE_PORT=7076
NODE_FALLBACK=True
```

4.) Setup callback forwarding, if you want

```
CALLBACK_FORWARD=['http://127.0.0.1:5001']
```

This requires you to setup the callback in node config as follows:

```
"callback_address": "127.0.0.1",
"callback_port": "5555",
"callback_target": "/callback",
```

## Setup

1.) Install python 3.6+

2.) Install requirements

```
cd betsy-middleware
virtualenv -p python3.6 venv
./venv/bin/pip install -r requirements.txt
```

## Running as systemd service

1.) Create file `/etc/systemd/system/betsy.service`

```
[Unit]
Description=Betsy - Gunicorn Worker
After=network.target

[Service]
PIDFile=/tmp/betsy.pid
User=<your_user>
WorkingDirectory=/path/to/betsy
ExecStart=/path/to/betsy/venv/bin/gunicorn main:work_app --pid /tmp/betsy.pid   \
          --bind 127.0.0.1:5555 -w 1 --worker-class aiohttp.worker.GunicornWebWorker --error-log=/tmp/betsy.log 
ExecReload=/bin/kill -s HUP $MAINPID
ExecStop=/bin/kill -s TERM $MAINPID

[Install]
WantedBy=multi-user.target
```

Change bind address, port to whatever you want. If you want multiple gunicorn worker processes then increase the `-w` paramter

2.) Enable and start

```
sudo systemctl enable betsy
sudo systemctl start betsy
```