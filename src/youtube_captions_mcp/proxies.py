"""Build a youtube-transcript-api proxy config from environment variables.

YouTube blocks most datacenter/cloud IPs, so on a server the underlying library can raise
``RequestBlocked`` / ``IpBlocked``. These env vars let the user opt into a proxy without any
code change. With nothing set, requests go out directly.
"""

from __future__ import annotations

import os

from youtube_transcript_api.proxies import (
    GenericProxyConfig,
    ProxyConfig,
    WebshareProxyConfig,
)


def build_proxy_config() -> ProxyConfig | None:
    """Return a ``ProxyConfig`` derived from env vars, or ``None`` for direct requests.

    Precedence:
        1. ``WEBSHARE_PROXY_USERNAME`` + ``WEBSHARE_PROXY_PASSWORD`` -> ``WebshareProxyConfig``
           (optional ``WEBSHARE_PROXY_LOCATIONS``, a CSV of country codes such as ``us,de``).
        2. ``YT_TRANSCRIPT_HTTP_PROXY`` / ``YT_TRANSCRIPT_HTTPS_PROXY`` -> ``GenericProxyConfig``.
    """
    webshare_user = os.environ.get("WEBSHARE_PROXY_USERNAME")
    webshare_pass = os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if webshare_user and webshare_pass:
        locations = os.environ.get("WEBSHARE_PROXY_LOCATIONS", "")
        filter_ip_locations = [c.strip() for c in locations.split(",") if c.strip()]
        return WebshareProxyConfig(
            proxy_username=webshare_user,
            proxy_password=webshare_pass,
            filter_ip_locations=filter_ip_locations or None,
        )

    http_proxy = os.environ.get("YT_TRANSCRIPT_HTTP_PROXY")
    https_proxy = os.environ.get("YT_TRANSCRIPT_HTTPS_PROXY")
    if http_proxy or https_proxy:
        return GenericProxyConfig(http_url=http_proxy, https_url=https_proxy)

    return None
