# NANO/BANANO Work/Callback/Distributed POW Middleware (Betsy) v2.0

## What is Betsy?

Middleware for banano/nano applications and nodes.

## Why would I use this?

Here's some of the possible use cases

- You want to use distributed pow v3, but you don't want to change any application code.
- You want to use distributed pow v3, but you also want to use other work peers at the same time
- You want to precache work for accounts without using distributed pow
- You want to forward the nano/banano node callback to multiple services
- You want to use any combination of distributed pow and work peers, but you don't want things to collapse when they are unavailable

## Setup and Dependencies

Betsy requires python 3.6 or newer. Recommended operation is to use a virtualenv for all of the project dependencies.

```
# git clone https://github.com/bbedward/betsy-middleware.git
# cd betsy-middleware
# virtualenv -p python3.6 venv
# ./venv/bin/pip install -r requirements.txt
```

## Usage

You can run `./venv/bin/python main.py --help` for all options.

Betsy is configured using arguments.

| Argument    | Description                                                                                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| --host      | Host for betsy to listen on (default: `127.0.0.1`)                                                                                          |
| --port      | Port for betsy to listen on (default: `5555`)                                                                                               |
| --node-url  | URL of the local node for work_generate fallback If not specified, then will disable local node fallback. (optional, example: `[::1]:7072`) |
| --log-file  | Where betsy will output logging data to (default: `/tmp/betsy.log`)                                                                         |
| --work-urls | Work peer list, separated by spaces (NOT distributed pow) (optional, example: 'http://peer.com/api http://peer2.com/api`                    |
| --callbacks | APIs to forward the node callback to (optional, example: 'http://myapi.cc/api http://myapi2.cc/api`                                         |
| --precache  | Enable work precaching for work peers, excludes BoomPoW                                                                                     |
| --debug     | Enable debug mode (more verbose logging)                                                                                                    |

If I wanted to:

- Run on port 4545
- Enable work fallback to my nano node which has RPC on `[::1]:7076`
- Use the work peers `https://workpeerone.com` and `123.45.67.89:7176`
- Forward callbacks to `127.0.0.1:6000/callback`
- And enable work precaching

I would use the arguments as:

`main.py --port 4545 --node-url [::1]:7076 --work-urls https://workpeerone.com 123.45.67.89:7176 --callbacks 127.0.0.1:6000/callback --precache`

## Setting up with BoomPow v2 (BPoW)

You can use betsy with BANANO's PoW service as well ([BoomPow](https://github.com/BananoCoin/boompow)). You just need to add the key to the environment.

```
BPOW_KEY=124567
```

You can also add it to a file `.env`

**If you intend to use BoomPow with a NANO service!**

You need to also start betsy with the option `--bpow-nano-difficulty`

## Setting the node callback to point to betsy

If you want to use precaching or callback forwarding, betsy needs to receive callbacks. You can do this by editing the NanoData/config.json file as follows:

```
"callback_address": "127.0.0.1",
"callback_port": "5555",
"callback_target": "/callback",
```

`callback_address` and `callback_port` are the same as the `--host` and `--port` options you choose when you run betsy.

## Getting work from Betsy

Betsy does all the complicated stuff behind the scenes, all you need to do is post a standard `work_generate` request.

```
curl -g -d '{"action":"work_generate", "hash":"ECCB8CB65CD3106EDA8CE9AA893FEAD497A91BCA903890CBD7A5C59F06AB9113"}' '127.0.0.1:5555'
```

You can also use betsy as a work_peer in the NanoData/config.json

```
work_peers: [
  "::ffff:127.0.0.1:5555"
]
```

## Running as systemd service

1.) Create file `/etc/systemd/system/betsy.service`

```
[Unit]
Description=Betsy - Nano Middleware
After=network.target

[Service]
Type=simple
User=<your_user>
WorkingDirectory=/path/to/betsy
EnvironmentFile=/path/to/betsy/.env
ExecStart=/path/to/betsy/venv/bin/python main.py --host 127.0.0.1 --port 5555 --work-urls 123.45.67.89:6000/work --callbacks 123.45.67.89/callback --precache

[Install]
WantedBy=multi-user.target
```

2.) Enable and start

```
sudo systemctl enable betsy
sudo systemctl start betsy
```
