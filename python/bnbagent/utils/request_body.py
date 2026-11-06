"""Bounded JSON input for the optional ASGI examples."""

import asyncio
import json


class RequestBodyTooLarge(ValueError):
    pass


async def read_json_body(request, *, max_bytes=16 * 1024):
    length = request.headers.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > max_bytes):
        raise RequestBodyTooLarge("Request body exceeds byte limit")

    async def read():
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > max_bytes:
                raise RequestBodyTooLarge("Request body exceeds byte limit")
            body.extend(chunk)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    return await asyncio.wait_for(read(), timeout=15)
