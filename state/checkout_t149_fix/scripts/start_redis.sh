#!/bin/sh
set -eu

mkdir -p /workspace/redisdata
exec redis-server --bind 127.0.0.1 --protected-mode yes --port 6379 \
    --dir /workspace/redisdata --appendonly yes --save 60 1 --daemonize no
