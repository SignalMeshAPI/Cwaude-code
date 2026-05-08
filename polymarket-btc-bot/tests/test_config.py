"""Tests for the Settings/config layer: env aliases and floor enforcement."""

from __future__ import annotations

import pytest

from polymarket_btc_bot.config import Settings


def test_polymarket_pk_alias_accepted():
    s = Settings(polymarket_pk="0xabc")
    assert s.polymarket_private_key.get_secret_value() == "0xabc"


def test_max_position_size_alias_accepted():
    s = Settings(polymarket_private_key="", max_position_size=5.0)
    assert s.max_bet_usd == 5.0


def test_redis_url_built_from_host_port_db():
    s = Settings(polymarket_private_key="", redis_host="myredis", redis_port=6380, redis_db=2)
    assert s.effective_redis_url() == "redis://myredis:6380/2"


def test_redis_url_falls_back_to_default_when_host_missing():
    s = Settings(polymarket_private_key="", redis_url="redis://elsewhere:6379/1")
    assert s.effective_redis_url() == "redis://elsewhere:6379/1"


def test_signal_thresholds_have_sensible_defaults():
    s = Settings(polymarket_private_key="")
    assert s.spike_threshold_z == pytest.approx(3.0)
    assert s.divergence_threshold_bps == pytest.approx(5.0)


def test_signal_thresholds_overridable():
    s = Settings(polymarket_private_key="", spike_threshold_z=2.0, divergence_threshold_bps=10.0)
    assert s.spike_threshold_z == 2.0
    assert s.divergence_threshold_bps == 10.0


def test_max_bet_usd_floor_enforced():
    with pytest.raises(ValueError):
        Settings(polymarket_private_key="", max_bet_usd=999.0)


def test_max_bet_usd_floor_enforced_via_alias():
    with pytest.raises(ValueError):
        Settings(polymarket_private_key="", max_position_size=999.0)
