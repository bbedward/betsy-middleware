# Define servers to request work from
WORK_SERVERS=['https://1.2.3.4:5000', 'https://5.6.7.8:5001']
# Local node address and port
NODE_URL='[::1]'
NODE_PORT=7072
# General settings
DEBUG=False
NODE_FALLBACK=True # Set to False if you never want to request work from node specified above
TIMEOUT=10 # How long to wait for each individual work peer to respond, before trying fallback