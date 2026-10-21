"""Caller identity carried through an MCP request.

An API key is created under a Cognito login, so it is bound to the creator's
identity (email). When the key is used on /mcp we resolve it to that identity
and stash it here, so tools and logging can answer "who is calling".

This uses a contextvar, which propagates through the async task that handles
one request — hence /mcp is wrapped by a pure ASGI middleware (same task),
not FastAPI's BaseHTTPMiddleware (which hops tasks and would lose it).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass
class Caller:
    email: str          # the Cognito user who created the key
    key_id: str
    key_name: str


_caller: contextvars.ContextVar[Caller | None] = contextvars.ContextVar("caller", default=None)


def set_caller(caller: Caller):
    return _caller.set(caller)


def reset_caller(token) -> None:
    _caller.reset(token)


def current_caller() -> Caller | None:
    return _caller.get()
