"""Reverse proxy from the manager to a session container.

Sessions publish no ports, so this is the only way in, and every byte of the
stream passes through here. That is what makes idle detection exact rather than
a guess: a live WebSocket is a player watching, and its close is them leaving.

Selkies is told its own prefix through WEB_SUBFOLDER, so paths are forwarded
unchanged rather than rewritten.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

log = logging.getLogger("proxy")

# Headers that describe one connection and must not be relayed to the next.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def _forwardable(headers, extra_drop=()) -> dict:
    drop = HOP_BY_HOP | {h.lower() for h in extra_drop}
    return {k: v for k, v in headers.items() if k.lower() not in drop}


async def proxy_websocket(request: web.Request, target: str,
                          client: aiohttp.ClientSession,
                          inject: dict | None = None) -> web.StreamResponse:
    """Splice a client WebSocket to an upstream one.

    autoping is off on both sides and frames are relayed verbatim, so Selkies'
    own heartbeat and its uplink accounting see what they expect rather than
    something this proxy invented.
    """
    ws_client = web.WebSocketResponse(max_msg_size=0, autoping=False, compress=False)
    await ws_client.prepare(request)
    log.info("ws open  %s", request.path)

    headers = _forwardable(request.headers, extra_drop=(
        "host", "sec-websocket-key", "sec-websocket-version",
        "sec-websocket-extensions", "sec-websocket-protocol", "cookie"))
    headers.update(inject or {})

    try:
        async with client.ws_connect(
            target,
            headers=headers,
            max_msg_size=0,
            autoping=False,
            heartbeat=None,
            protocols=tuple(
                p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
                if p.strip()),
        ) as ws_upstream:

            async def pump(src, dst, name):
                try:
                    async for msg in src:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await dst.send_str(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await dst.send_bytes(msg.data)
                        elif msg.type == aiohttp.WSMsgType.PING:
                            await dst.ping(msg.data)
                        elif msg.type == aiohttp.WSMsgType.PONG:
                            await dst.pong(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                          aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                except (aiohttp.ClientError, ConnectionResetError, asyncio.CancelledError) as exc:
                    log.debug("ws %s pump ended: %s", name, exc)
                except Exception:                   # noqa: BLE001 - report, do not vanish
                    log.exception("ws %s pump failed", name)
                finally:
                    if not dst.closed:
                        await dst.close()

            await asyncio.gather(
                pump(ws_client, ws_upstream, "up"),
                pump(ws_upstream, ws_client, "down"),
            )
    except aiohttp.ClientError as exc:
        log.warning("ws upstream %s failed: %s", target, exc)
        if not ws_client.closed:
            await ws_client.close()
    except Exception:                               # noqa: BLE001
        log.exception("ws proxy failed for %s", target)
        if not ws_client.closed:
            await ws_client.close()
    log.info("ws close %s (client_close=%s)", request.path, ws_client.close_code)
    return ws_client


async def proxy_http(request: web.Request, target: str,
                     client: aiohttp.ClientSession,
                     inject: dict | None = None,
                     cookie: dict | None = None) -> web.StreamResponse:
    headers = _forwardable(request.headers, extra_drop=("host", "cookie"))
    headers.update(inject or {})
    try:
        async with client.request(
            request.method, target,
            headers=headers,
            data=request.content if request.can_read_body else None,
            allow_redirects=False,
            # A streaming response must not be cut off by a read timeout.
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None),
        ) as upstream:
            response = web.StreamResponse(
                status=upstream.status,
                headers=_forwardable(upstream.headers, extra_drop=("content-length",)),
            )
            # Must happen before prepare(): headers are on the wire after that.
            if cookie:
                response.set_cookie(**cookie)
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response
    except aiohttp.ClientError as exc:
        log.info("upstream %s failed: %s", target, exc)
        raise web.HTTPBadGateway(text="The session is not reachable.") from exc
