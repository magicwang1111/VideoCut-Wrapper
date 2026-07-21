from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from typing import Any


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_tc3_headers(*, secret_id: str, secret_key: str, service: str, host: str,
                      action: str, version: str, region: str, payload: dict[str, Any],
                      timestamp: int | None = None) -> tuple[dict[str, str], str]:
    timestamp = int(timestamp or time.time())
    date_str = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime("%Y-%m-%d")
    body = canonical_json(payload)
    hashed_body = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = "\n".join([
        "POST", "/", "",
        f"content-type:application/json; charset=utf-8\nhost:{host}\nx-tc-action:{action.lower()}\n",
        "content-type;host;x-tc-action", hashed_body,
    ])
    scope = f"{date_str}/{service}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256", str(timestamp), scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    secret_date = hmac.new(("TC3" + secret_key).encode(), date_str.encode(), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode(), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
        f"SignedHeaders=content-type;host;x-tc-action, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Region": region,
        "X-TC-Timestamp": str(timestamp),
    }, body
