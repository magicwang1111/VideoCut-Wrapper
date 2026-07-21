from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from videocut.subtitle.config import SubtitleSettings


def _authorization(settings: SubtitleSettings, object_key: str, now: int, valid_for: int = 3600) -> str:
    sign_time = f"{now};{now + valid_for}"
    sign_key = hmac.new(settings.secret_key.encode(), sign_time.encode(), hashlib.sha1).hexdigest()
    host = f"{settings.cos_bucket}.cos.{settings.region}.myqcloud.com"
    http_string = f"get\n/{object_key.lstrip('/')}\n\nhost={quote(host, safe='-_.~')}\n"
    string_to_sign = f"sha1\n{sign_time}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()
    return (
        f"q-sign-algorithm=sha1&q-ak={settings.secret_id}&q-sign-time={sign_time}&q-key-time={sign_time}"
        f"&q-header-list=host&q-url-param-list=&q-signature={signature}"
    )


def signed_cos_url(settings: SubtitleSettings, path_or_url: str, valid_for: int = 3600) -> str:
    import time

    parsed = urlparse(path_or_url)
    object_key = parsed.path.lstrip("/") if parsed.scheme else path_or_url.lstrip("/")
    if not object_key:
        raise ValueError("COS subtitle path is empty.")
    host = f"{settings.cos_bucket}.cos.{settings.region}.myqcloud.com"
    return f"https://{host}/{quote(object_key, safe='/')}?{_authorization(settings, object_key, int(time.time()), valid_for)}"


def download_subtitle(settings: SubtitleSettings, path_or_url: str, target: str | Path) -> Path:
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    url = signed_cos_url(settings, path_or_url)
    with requests.get(url, timeout=settings.request_timeout, stream=True) as response:
        response.raise_for_status()
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    handle.write(chunk)
    if not target_path.is_file() or target_path.stat().st_size == 0:
        raise RuntimeError("Tencent COS returned an empty subtitle file.")
    return target_path
