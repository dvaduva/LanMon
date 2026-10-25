from app.services.oui import lookup_vendor


def test_known_vendor():
    assert lookup_vendor("00-11-32-AA-BB-CC") == "Synology Incorporated"


def test_random_or_unknown_mac_has_no_vendor():
    assert lookup_vendor("da:a1:19:00:00:01") is None
