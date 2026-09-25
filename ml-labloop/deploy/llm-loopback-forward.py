#!/usr/bin/env python3
"""Expose the host LLM endpoint on 127.0.0.1 inside a container.

Listens on container-loopback:LLM_PORT and forwards each connection to
LLM_TARGET (host:port). Any in-container client hardwired to
127.0.0.1:<port> reaches the real server without reconfiguration.

Env:
  LLM_PORT    loopback listen port            (default 1234)
  LLM_TARGET  upstream host:port              (default 192.168.122.1:1234)

Inject with deploy/llm-forward (podman cp into /tmp + exec -d).
"""
import os
import socket
import threading

PORT = int(os.environ.get("LLM_PORT", "1234"))
_host, _, _port = os.environ.get("LLM_TARGET", "192.168.122.1:1234").partition(":")
TARGET = (_host, int(_port or 1234))


def pipe(src, dst):
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        src.close()
        dst.close()


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT))
srv.listen(128)

while True:
    client, _ = srv.accept()
    try:
        upstream = socket.create_connection(TARGET)
    except OSError:
        client.close()
        continue
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()
