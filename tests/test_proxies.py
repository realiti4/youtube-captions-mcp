"""Tests for env-driven proxy config construction."""

from __future__ import annotations

import pytest
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

from youtube_context_mcp.proxies import build_proxy_config

PROXY_ENV = [
    "WEBSHARE_PROXY_USERNAME",
    "WEBSHARE_PROXY_PASSWORD",
    "WEBSHARE_PROXY_LOCATIONS",
    "YT_TRANSCRIPT_HTTP_PROXY",
    "YT_TRANSCRIPT_HTTPS_PROXY",
]


@pytest.fixture(autouse=True)
def clear_proxy_env(monkeypatch):
    for key in PROXY_ENV:
        monkeypatch.delenv(key, raising=False)


def test_no_env_returns_none():
    assert build_proxy_config() is None


def test_webshare_config(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "user")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "pass")
    monkeypatch.setenv("WEBSHARE_PROXY_LOCATIONS", "us, de")
    assert isinstance(build_proxy_config(), WebshareProxyConfig)


def test_webshare_takes_precedence_over_generic(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "user")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "pass")
    monkeypatch.setenv("YT_TRANSCRIPT_HTTP_PROXY", "http://proxy:8080")
    assert isinstance(build_proxy_config(), WebshareProxyConfig)


def test_partial_webshare_is_ignored(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "user")  # password missing
    assert build_proxy_config() is None


def test_generic_http_proxy(monkeypatch):
    monkeypatch.setenv("YT_TRANSCRIPT_HTTP_PROXY", "http://proxy:8080")
    assert isinstance(build_proxy_config(), GenericProxyConfig)


def test_generic_https_proxy(monkeypatch):
    monkeypatch.setenv("YT_TRANSCRIPT_HTTPS_PROXY", "https://proxy:8443")
    assert isinstance(build_proxy_config(), GenericProxyConfig)
