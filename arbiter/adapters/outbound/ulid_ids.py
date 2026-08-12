from __future__ import annotations

import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class UlidDecisionIdGenerator:
    def new_decision_id(self) -> str:
        ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
        rand = secrets.randbits(80)
        n = (ts_ms << 80) | rand
        chars: list[str] = []
        for _ in range(26):
            chars.append(_CROCKFORD[n & 31])
            n >>= 5
        return "d-" + "".join(reversed(chars))
