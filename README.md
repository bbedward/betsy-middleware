# NANO/BANANO Work Distributor

# Distribute work among a variety of servers, and pre-cache work so it's available

1) Configure settings

2) Run server

3) Use as standard work peer

# running:

gunicorn main:work_app --bind localhost:8080 --worker-class aiohttp.worker.GunicornWebWorker