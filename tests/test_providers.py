import pytest

from kissget.providers import KisskhProvider, get_provider
from kissget.providers.kisskh import KisskhProvider as KisskhProviderClass


def test_matches_kisskh_domains():
    assert KisskhProvider.matches("https://kisskh.nl/Drama/X?id=1")
    assert KisskhProvider.matches("https://kisskh.co/Drama/X?id=1")
    assert not KisskhProvider.matches("https://example.com/show?id=1")


def test_parse_url_drama_level():
    p = KisskhProvider()
    target = p.parse_url("https://kisskh.nl/Drama/Some-Show?id=1234")
    assert target.drama_id == 1234
    assert target.drama_slug == "Some-Show"
    assert target.episode_ids is None


def test_parse_url_single_episode():
    p = KisskhProvider()
    target = p.parse_url("https://kisskh.nl/Drama/A-Business-Proposal/Episode-3?id=4608&ep=86439&page=0&pageSize=100")
    assert target.drama_id == 4608
    assert target.drama_slug == "A-Business-Proposal"
    assert target.episode_ids == {3.0: 86439}


def test_parse_url_missing_id_raises():
    p = KisskhProvider()
    with pytest.raises(ValueError):
        p.parse_url("https://kisskh.nl/Drama/Some-Show")


def test_get_provider_selects_kisskh_and_defaults():
    assert isinstance(get_provider("https://kisskh.co/Drama/X?id=1"), KisskhProviderClass)
    # Unknown URL and bare-query (no url) both fall back to kisskh.
    assert isinstance(get_provider("https://example.com/show?id=1"), KisskhProviderClass)
    assert isinstance(get_provider(), KisskhProviderClass)


def test_get_provider_derives_base_url_from_kisskh_co():
    provider = get_provider("https://kisskh.co/Drama/X?id=1")
    # The wrapped API should target kisskh.co, not the kisskh.nl default.
    assert provider._api.site_domain == "https://kisskh.co"


def test_get_provider_explicit_base_url_wins():
    provider = get_provider("https://kisskh.co/Drama/X?id=1", base_url="https://kisskh.me")
    assert provider._api.site_domain == "https://kisskh.me"
