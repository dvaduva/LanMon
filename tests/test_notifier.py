import asyncio
from datetime import UTC, datetime, timedelta

from app.db.models import Device, Discovered, Setting
from app.events import Event, EventBus
from app.services.notifier import Notifier, load_config
from tests.test_web import post


class Outbox:
    def __init__(self, errors=None):
        self.sent = []
        self.errors = errors or []

    async def __call__(self, config, text):
        self.sent.append(text)
        return list(self.errors)


def configure(session_factory, **values):
    values = {"notify_telegram_token": "t", "notify_telegram_chat": "1", **values}
    with session_factory.begin() as s:
        for key, value in values.items():
            setting = s.get(Setting, key)
            if setting:
                setting.value = value
            else:
                s.add(Setting(key=key, value=value))


def add_device(session_factory, status="offline", changed_ago=timedelta(minutes=15), notify=True):
    with session_factory.begin() as s:
        d = Device(
            mac="aa:bb:cc:dd:ee:01",
            name="PC birou",
            status=status,
            notify=notify,
            status_changed_at=datetime.now(UTC) - changed_ago,
        )
        s.add(d)
        s.flush()
        return d.id


def set_status(session_factory, device_id, status):
    with session_factory.begin() as s:
        d = s.get(Device, device_id)
        d.status = status
        d.status_changed_at = datetime.now(UTC)


def test_defaults_seeded_by_migration(session_factory):
    with session_factory() as s:
        config = load_config(s)
    assert (config.offline_min, config.new_device, config.wol_failed) == (10, True, True)
    assert config.channels == []


def test_offline_and_back_online(session_factory):
    configure(session_factory)
    device_id = add_device(session_factory)
    outbox = Outbox()
    notifier = Notifier(session_factory, EventBus(), send=outbox)

    asyncio.run(notifier.check_offline())
    asyncio.run(notifier.check_offline())  # o singură notificare pentru aceeași oprire
    assert outbox.sent == ["PC birou e oprit de 15 min."]

    set_status(session_factory, device_id, "online")
    asyncio.run(notifier.check_offline())
    assert outbox.sent[-1] == "PC birou a revenit online."


def test_short_outage_and_unmarked_devices_are_quiet(session_factory):
    configure(session_factory)
    add_device(session_factory, changed_ago=timedelta(minutes=3))
    outbox = Outbox()
    notifier = Notifier(session_factory, EventBus(), send=outbox)
    asyncio.run(notifier.check_offline())
    assert outbox.sent == []


def test_no_channel_means_nothing_sent(session_factory):
    add_device(session_factory)
    outbox = Outbox()
    asyncio.run(Notifier(session_factory, EventBus(), send=outbox).check_offline())
    assert outbox.sent == []


def test_restart_does_not_repeat_old_outages(session_factory):
    configure(session_factory)
    add_device(session_factory, changed_ago=timedelta(hours=5))
    outbox = Outbox()
    notifier = Notifier(session_factory, EventBus(), send=outbox)
    notifier.prime()
    asyncio.run(notifier.check_offline())
    assert outbox.sent == []


def test_new_device_and_failed_wol(session_factory):
    configure(session_factory)
    device_id = add_device(session_factory, status="offline")
    with session_factory.begin() as s:
        s.add(Discovered(mac="11:22:33:44:55:66", ip="192.168.1.50", vendor="Apple"))
    outbox = Outbox()
    notifier = Notifier(session_factory, EventBus(), send=outbox)

    asyncio.run(notifier.handle(Event("device_discovered", {"mac": "11:22:33:44:55:66"})))
    asyncio.run(notifier.handle(Event("wol", {"device_id": device_id, "result": "timeout"})))
    asyncio.run(notifier.handle(Event("wol", {"device_id": device_id, "result": "woke"})))
    assert outbox.sent == [
        "Dispozitiv nou în rețea: 11:22:33:44:55:66 (192.168.1.50, Apple)",
        "Pornirea PC birou a eșuat: nu a răspuns în timpul de așteptare.",
    ]

    configure(session_factory, notify_new_device="0")
    asyncio.run(notifier.handle(Event("device_discovered", {"mac": "11:22:33:44:55:66"})))
    assert len(outbox.sent) == 2


def test_run_loop_handles_events_and_periodic_checks(session_factory):
    configure(session_factory)
    with session_factory.begin() as s:
        s.add(Discovered(mac="11:22:33:44:55:66"))
    bus = EventBus()
    outbox = Outbox()
    notifier = Notifier(session_factory, bus, send=outbox, check_every=0.05)

    async def scenario():
        task = asyncio.create_task(notifier.run())
        await asyncio.sleep(0.05)
        add_device(session_factory)
        bus.publish(Event("device_discovered", {"mac": "11:22:33:44:55:66"}))
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(scenario())
    # Ordinea depinde de ceas (pe Windows are rezoluție de ~15 ms); contează că ambele au plecat.
    assert sorted(outbox.sent) == [
        "Dispozitiv nou în rețea: 11:22:33:44:55:66",
        "PC birou e oprit de 15 min.",
    ]


def test_settings_page_saves_and_tests_notifications(admin):
    outbox = Outbox()
    admin.app.state.notify_send = outbox
    response = post(
        admin,
        "/settings/notifications",
        {
            "notify_telegram_token": "123:abc",
            "notify_telegram_chat": "42",
            "notify_offline_min": "5",
            "notify_wol_failed": "on",
        },
        page="/settings",
    )
    assert response.status_code == 303
    page = admin.get("/settings").text
    assert "Active pe Telegram" in page and "123:abc" not in page

    # Token gol la o nouă salvare: se păstrează cel vechi.
    post(admin, "/settings/notifications", {"notify_telegram_chat": "42"}, page="/settings")
    with admin.app.state.session_factory() as s:
        config = load_config(s)
    assert (config.telegram_token, config.offline_min, config.wol_failed) == ("123:abc", 10, False)

    post(admin, "/settings/notifications/test", page="/settings")
    assert outbox.sent == ["Mesaj de test de la LanMon."]
    assert "Mesaj de test trimis pe Telegram" in admin.get("/settings").text

    outbox.errors = ["Telegram: Unauthorized"]
    post(admin, "/settings/notifications/test", page="/settings")
    assert "Telegram: Unauthorized" in admin.get("/settings").text


def test_settings_rejects_bad_email_config(admin):
    response = post(
        admin,
        "/settings/notifications",
        {"notify_email_host": "smtp.x", "notify_email_port": "99999", "notify_offline_min": "10"},
        page="/settings",
    )
    assert response.status_code == 400
    assert "Portul SMTP" in response.text and "destinatarului" in response.text


def test_device_notify_checkbox(admin):
    with admin.app.state.session_factory.begin() as s:
        s.add(Device(mac="aa:bb:cc:dd:ee:01", name="PC"))
    post(admin, "/devices/1", {"name": "PC", "notify": "on"}, page="/devices/1")
    with admin.app.state.session_factory() as s:
        assert s.get(Device, 1).notify is True
    assert 'name="notify" checked' in admin.get("/devices/1").text
