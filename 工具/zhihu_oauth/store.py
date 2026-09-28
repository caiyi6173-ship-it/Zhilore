"""Bounded, ephemeral sessions. One process only; tokens are never serialized."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from .errors import OAuthError
from .provider import Identity, TokenGrant

FLOW_TTL = 10 * 60
SESSION_TTL = 8 * 60 * 60


@dataclass(frozen=True)
class PendingFlow:
    state: str = field(repr=False)
    expires_at: float
    consumed: bool = False
    # Already sanitised by safe_return_path before it reaches the store; kept
    # server-side so it cannot be rewritten by the callback query string.
    return_to: str | None = None
    # True only when the operator enabled the cookie-bound fallback and the
    # platform callback arrived without `state` (documented behaviour).
    stateless: bool = False


@dataclass(frozen=True)
class Session:
    user: Identity
    access_token: str = field(repr=False)
    csrf: str = field(repr=False)
    expires_at: float
    stateless: bool = False


class SessionStore:
    def __init__(self, clock: Callable[[], float] = time.time, capacity: int = 256):
        self.clock = clock
        self.capacity = capacity
        self._lock = threading.RLock()
        self._pending: dict[str, PendingFlow] = {}
        self._sessions: dict[str, Session] = {}

    def prune(self) -> None:
        with self._lock:
            now = self.clock()
            self._pending = {key: flow for key, flow in self._pending.items() if flow.expires_at > now}
            self._sessions = {key: session for key, session in self._sessions.items() if session.expires_at > now}

    def begin(self, old_flow: str | None = None, return_to: str | None = None) -> tuple[str, str]:
        with self._lock:
            self.prune()
            self.cancel(old_flow)
            if len(self._pending) >= self.capacity:
                raise OAuthError("server_busy", 503)
            handle, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._pending[handle] = PendingFlow(state, self.clock() + FLOW_TTL, return_to=return_to)
            return handle, state

    def consume(self, handle: str | None, state: str | None, allow_stateless: bool = False) -> PendingFlow:
        with self._lock:
            flow = self._pending.get(handle or "")
            if not state:
                # Strict by default. The cookie-bound fallback still requires a
                # live, unconsumed flow that this very browser holds in an
                # HttpOnly single-use cookie; it never accepts a bare visit.
                if not allow_stateless:
                    raise OAuthError("state_missing", 400)
                if not flow or flow.consumed:
                    raise OAuthError("state_invalid", 400)
                if flow.expires_at <= self.clock():
                    self._pending.pop(handle, None)
                    raise OAuthError("flow_expired", 400)
                claimed = replace(flow, consumed=True, stateless=True)
                self._pending[handle] = claimed  # Atomic, single use before upstream I/O.
                return claimed
            if not flow or flow.consumed:
                raise OAuthError("state_invalid", 400)
            if flow.expires_at <= self.clock():
                self._pending.pop(handle, None)
                raise OAuthError("flow_expired", 400)
            if not secrets.compare_digest(flow.state, state):
                raise OAuthError("state_invalid", 400)
            claimed = replace(flow, consumed=True)
            self._pending[handle] = claimed  # Atomic, single use before upstream I/O.
            return claimed

    def complete(self, handle: str, claimed: PendingFlow, grant: TokenGrant, user: Identity) -> tuple[str, Session]:
        with self._lock:
            if self._pending.get(handle) is not claimed:
                raise OAuthError("state_invalid", 400)
            if claimed.expires_at <= self.clock():
                raise OAuthError("flow_expired", 400)
            self._pending.pop(handle, None)
            return self.create(grant, user, stateless=claimed.stateless)

    def cancel(self, handle: str | None) -> None:
        with self._lock:
            self._pending.pop(handle or "", None)

    def create(self, grant: TokenGrant, user: Identity, stateless: bool = False) -> tuple[str, Session]:
        with self._lock:
            self.prune()
            if grant.expires_at <= self.clock():
                raise OAuthError("token_expired", 401)
            if len(self._sessions) >= self.capacity:
                raise OAuthError("server_busy", 503)
            handle = secrets.token_urlsafe(32)
            record = Session(user, grant.access_token, secrets.token_urlsafe(32), min(grant.expires_at, self.clock() + SESSION_TTL), stateless)
            self._sessions[handle] = record
            return handle, record

    def get(self, handle: str | None) -> Session:
        with self._lock:
            if not handle:
                raise OAuthError("login_required", 401)
            record = self._sessions.get(handle)
            if record is None:
                raise OAuthError("session_expired", 401)
            if record.expires_at <= self.clock():
                self._sessions.pop(handle, None)
                raise OAuthError("token_expired", 401)
            return record

    def revoke(self, handle: str | None) -> None:
        with self._lock:
            self._sessions.pop(handle or "", None)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
            self._sessions.clear()
