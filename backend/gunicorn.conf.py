"""Gunicorn 生产配置，面向 2C2G 的小机器。

worker 数维持 2 个，避免把 2G 内存打满；max_requests 用于定期回收可能的内存增长。
"""
bind = "127.0.0.1:3015"
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"
threads = 1
worker_connections = 100

max_requests = 800
max_requests_jitter = 80

timeout = 90
graceful_timeout = 30
keepalive = 5
preload_app = False

accesslog = "-"
errorlog = "-"
loglevel = "warning"
