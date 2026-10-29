import pytest

from app.mac import is_random_mac, normalize_mac


@pytest.mark.parametrize(
    "raw",
    [
        "AA:BB:CC:DD:EE:FF",
        "aa-bb-cc-dd-ee-ff",
        "aabb.ccdd.eeff",
        "AABBCCDDEEFF",
        " aa:bb:cc:dd:ee:ff ",
    ],
)
def test_normalize_mac_formats(raw):
    assert normalize_mac(raw) == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("raw", ["", "aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:ff:00", "gg:bb:cc:dd:ee:ff"])
def test_normalize_mac_invalid(raw):
    with pytest.raises(ValueError):
        normalize_mac(raw)


def test_random_mac_detection():
    assert is_random_mac("da:a1:19:00:00:01")  # "locally administered" bit set
    assert not is_random_mac("00:11:32:aa:bb:cc")  # a real OUI (Synology)
