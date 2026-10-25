from app.services.arptable import parse_arp_table

SAMPLE = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.10     0x1         0x2         AA:BB:CC:DD:EE:01     *        eth0
192.168.1.11     0x1         0x0         00:00:00:00:00:00     *        eth0
192.168.1.12     0x1         0x6         aa:bb:cc:dd:ee:02     *        eth0
"""


def test_parse_keeps_complete_entries_only():
    assert parse_arp_table(SAMPLE) == {
        "aa:bb:cc:dd:ee:01": "192.168.1.10",
        "aa:bb:cc:dd:ee:02": "192.168.1.12",
    }
