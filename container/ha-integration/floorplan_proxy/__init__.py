"""Reverse proxy: serves the 3D Floorplan container under Home Assistant's own address.

The floorplan runs as a second container (Home Assistant Container has no add-on store,
#489). This component mounts it at "/floorplan/", so the plan is reachable at every
address Home Assistant itself is reachable at (LAN IP:8123, a reverse-proxied domain,
the Tailscale address, ...), always same-origin with the dashboard. The dashboard card
embeds "/floorplan/?card=1" relatively, and the container's single sign-on shares Home
Assistant's localStorage, so nobody logs in twice.

The floorplan's own login protects everything it serves (as when opened directly); the
original Host header and the scheme are forwarded, so the container sees the address the
browser used and its cookies, redirects and login flows work unchanged.
"""
import asyncio
import logging

import aiohttp
from aiohttp import web
from multidict import CIMultiDict

from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

TARGET = "http://127.0.0.1:8100"
PREFIX = "/floorplan"

# hop-by-hop headers plus the ones the proxy or aiohttp must own itself.
# "host" is deliberately NOT skipped: the container must see the address the
# browser used, or every URL it builds (login redirect, client id) is wrong.
_SKIP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
    "content-length", "content-encoding", "accept-encoding",
}


async def async_setup(hass: HomeAssistant, config):
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
    ws = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))

    async def close(_event):
        await http.close()
        await ws.close()

    hass.bus.async_listen_once("homeassistant_stop", close)

    def target_url(request: web.Request, scheme: str) -> str:
        path = request.path.removeprefix(PREFIX) or "/"
        qs = f"?{request.query_string}" if request.query_string else ""
        if scheme == "ws":
            return "ws" + TARGET[4:] + path + qs
        return TARGET + path + qs

    def forward_headers(request: web.Request) -> dict:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _SKIP}
        headers.setdefault("X-Forwarded-Proto", request.scheme)
        return headers

    async def relay(src, dst):
        try:
            async for msg in src:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await dst.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await dst.send_bytes(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                  aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except (aiohttp.ClientError, ConnectionError, asyncio.CancelledError):
            pass

    async def proxy_ws(request: web.Request, url: str) -> web.WebSocketResponse:
        ws_server = web.WebSocketResponse()
        await ws_server.prepare(request)
        try:
            ws_target = await ws.ws_connect(url, headers=forward_headers(request))
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("Floorplan container is not reachable: %s", err)
            await ws_server.close()
            return ws_server
        t1 = asyncio.create_task(relay(ws_server, ws_target))
        t2 = asyncio.create_task(relay(ws_target, ws_server))
        _, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await ws_target.close()
        await ws_server.close()
        return ws_server

    async def handler(request: web.Request) -> web.StreamResponse:
        if request.path == PREFIX:
            qs = f"?{request.query_string}" if request.query_string else ""
            raise web.HTTPFound(PREFIX + "/" + qs)
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await proxy_ws(request, target_url(request, "ws"))
        url = target_url(request, "http")
        headers = forward_headers(request)
        body = await request.read() if request.can_read_body else None
        try:
            r = await http.request(request.method, url, data=body, headers=headers,
                                   allow_redirects=False)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("Floorplan container is not reachable: %s", err)
            return web.json_response({"error": "floorplan unreachable"}, status=502)
        out = CIMultiDict()
        for k, v in r.headers.items():
            if k.lower() not in _SKIP and k.lower() != "content-length":
                out.add(k, v)
        return web.Response(status=r.status, body=await r.read(), headers=out)

    @callback
    def register():
        app = hass.http.app
        app.router.add_route("*", f"{PREFIX}/{{path:.*}}", handler)
        app.router.add_route("*", PREFIX, handler)
        _LOGGER.info("3D Floorplan served at %s (target %s)", PREFIX, TARGET)

    register()
    return True
