#!/bin/bash
# Runs ON THE BOARD (pushed by usb-link.ps1). Relays 0.0.0.0:18184 -> 127.0.0.1:18182, where
# `adb reverse` exposes the laptop's GenieX server. The app container reaches it as
# http://msgpack-rpc-router:18184/v1 (that hostname is the Docker host gateway).
pkill -f 'socat TCP-LISTEN:18184' 2>/dev/null
setsid nohup socat TCP-LISTEN:18184,fork,reuseaddr TCP:127.0.0.1:18182 </dev/null >/dev/null 2>&1 &
sleep 1
ss -ltn | grep -q ':18184' && echo "relay listening on 18184" || echo "relay FAILED to start"
docker exec sentinelq-main-1 python3 -c "import urllib.request;urllib.request.urlopen('http://msgpack-rpc-router:18184/v1/models',timeout=5);print('container can reach the laptop NPU server')" 2>/dev/null || echo "container cannot reach the laptop (is npu_server.py running?)"
