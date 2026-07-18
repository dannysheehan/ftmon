"""[NO-02] Desktop adapter tray hygiene: transient kinds, one replaceable
slot per incident, bounded urgency — and graceful fallback without the flags.
"""

from types import SimpleNamespace

import ftmon.notify.desktop as desktop_mod
from ftmon.model import Notification
from ftmon.notify.desktop import DesktopNotifier

_HELP_FULL = "-e, --transient\n-p, --print-id\n-r, --replace-id=REPLACE_ID\n"
_HELP_OLD = "-u, --urgency=LEVEL\n"


class FakeNotifySend:
    """Records argv per call; answers --help with a fixed flag list."""

    def __init__(self, help_text=_HELP_FULL, ids=("11", "12", "13")):
        self.help_text = help_text
        self.ids = list(ids)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        if "--help" in argv:
            return SimpleNamespace(stdout=self.help_text, returncode=0)
        self.calls.append(list(argv))
        printed = self.ids.pop(0) if "--print-id" in argv and self.ids else ""
        return SimpleNamespace(stdout=printed + "\n", returncode=0)


def _notifier(monkeypatch, fake):
    monkeypatch.setattr(desktop_mod.shutil, "which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr(desktop_mod.subprocess, "run", fake)
    return DesktopNotifier()


def _note(kind="open", severity=2, incident_id=7):
    return Notification(
        incident_id=incident_id, kind=kind, severity=severity,
        title="t", body="b", created_ts=0.0,
    )


def test_recover_and_renotify_are_transient_open_and_escalate_are_not_no_02(monkeypatch):
    """[NO-02] Churn kinds must not accumulate tray entries; endpoints persist."""
    fake = FakeNotifySend()
    notifier = _notifier(monkeypatch, fake)
    for kind in ("open", "escalate", "renotify", "recover", "digest"):
        notifier.deliver(_note(kind=kind))
    transient = ["--transient" in argv for argv in fake.calls]
    assert transient == [False, False, True, True, False]


def test_incident_reuses_one_replaceable_slot_and_recover_frees_it_no_02(monkeypatch):
    """[NO-02] open->escalate->recover updates a single tray entry; the next
    incident lifecycle starts a fresh one."""
    fake = FakeNotifySend(ids=("41", "41", "41", "52"))
    notifier = _notifier(monkeypatch, fake)
    notifier.deliver(_note(kind="open"))
    notifier.deliver(_note(kind="escalate", severity=3))
    notifier.deliver(_note(kind="recover", severity=0))
    notifier.deliver(_note(kind="open"))  # same incident id reopened later
    replace_ids = [
        argv[argv.index("--replace-id") + 1] if "--replace-id" in argv else None
        for argv in fake.calls
    ]
    assert all("--print-id" in argv for argv in fake.calls)
    assert replace_ids == [None, "41", "41", None]


def test_old_notify_send_degrades_to_plain_persistent_delivery_no_02(monkeypatch):
    """[NO-02] Absent flags must mean fallback, not failure."""
    fake = FakeNotifySend(help_text=_HELP_OLD)
    notifier = _notifier(monkeypatch, fake)
    notifier.deliver(_note(kind="renotify"))
    (argv,) = fake.calls
    assert "--transient" not in argv
    assert "--print-id" not in argv
    assert "--replace-id" not in argv


def test_only_severity_four_maps_to_critical_urgency_no_02(monkeypatch):
    """[NO-02] GNOME never expires `critical`; errors must stay expirable."""
    fake = FakeNotifySend()
    notifier = _notifier(monkeypatch, fake)
    for severity in (0, 2, 3, 4):
        notifier.deliver(_note(severity=severity))
    urgencies = [argv[argv.index("-u") + 1] for argv in fake.calls]
    assert urgencies == ["low", "normal", "normal", "critical"]


def test_id_map_is_bounded_and_ignores_unparseable_ids_no_02(monkeypatch):
    """[NO-02] Adapter state cannot grow unbounded; garbage stdout is dropped."""
    fake = FakeNotifySend(ids=[str(n) for n in range(600)])
    notifier = _notifier(monkeypatch, fake)
    for incident in range(desktop_mod._ID_MAP_MAX + 10):
        notifier.deliver(_note(incident_id=incident))
    assert len(notifier._ids) == desktop_mod._ID_MAP_MAX
    assert 0 not in notifier._ids  # oldest evicted

    fake_bad = FakeNotifySend(ids=("not-a-number",))
    notifier = _notifier(monkeypatch, fake_bad)
    notifier.deliver(_note(kind="open"))
    assert notifier._ids == {}
