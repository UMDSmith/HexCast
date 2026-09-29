import pytest

from hexcast_core.manifest import API_VERSION, ManifestError, parse_manifest


def ok(**kw):
    base = {"id": "twitch", "name": "Twitch", "version": "1.0.0"}
    base.update(kw)
    return parse_manifest(base)


def test_minimal_manifest_gets_defaults():
    m = ok()
    assert (m.id, m.name, m.version, m.api) == ("twitch", "Twitch", "1.0.0", API_VERSION)
    assert m.parent is None and m.requires == () and m.nav is None and not m.hidden


@pytest.mark.parametrize("bad", [
    {"id": "Twitch"}, {"id": "1abc"}, {"id": "a"}, {"id": "has-dash"}, {"id": "x" * 40},
    {"name": ""}, {"version": "abc"}, {"api": 99}, {"api": 0}, {"parent": "twitch"},
    {"requires": ["twitch"]}, {"requires": "x"}, {"color": "red"}, {"order": "1"},
    {"entry": "a b"}, {"nav": {"label": "x", "href": "nope"}}, {"nav": {"label": "x", "href": "//evil"}},
    {"nav": {"href": "/x", "status_js": "../x.js"}}, {"help": {"file": "/etc/passwd"}},
    {"legacy_config": ["a/b.json"]},
])
def test_bad_manifests_are_rejected(bad):
    with pytest.raises(ManifestError):
        ok(**bad)


def test_addon_requires_its_parent():
    m = ok(id="games_craps", parent="games")
    assert m.parent == "games" and m.requires[0] == "games"


def test_nav_is_normalised():
    m = ok(nav={"href": "/twitch", "status_url": "/twitch/api/status", "status_js": "static/nav.js"}, color="#a970ff")
    assert m.nav["label"] == "Twitch" and m.nav["color"] == "#a970ff" and m.nav["order"] == 100
    assert m.nav["status_js"] == "static/nav.js"


def test_folder_name_must_match_id(tmp_path):
    d = tmp_path / "other"
    d.mkdir()
    with pytest.raises(ManifestError, match="folder"):
        parse_manifest({"id": "twitch", "name": "T", "version": "1"}, d)
