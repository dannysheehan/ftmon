"""Spike: what happens when EvtSubscribeStartAfterBookmark is given a bookmark
XML string whose RecordId no longer exists in the channel (log was cleared,
or the record aged out)? journald's cursor-not-found case is DM-15's closest
analogue on Linux; this checks whether Windows raises synchronously,
delivers via the error-action callback, or silently falls back.

    .venv-spike\\Scripts\\python.exe spikes\\windows-support\\evtlog_stale_bookmark_spike.py
"""

import queue
import time

import win32evtlog

CHANNEL = "Application"

# A RecordId far beyond anything a real Application log will have reached
# in this session -- simulates "the bookmarked record is gone".
FAKE_BOOKMARK_XML = (
    "<BookmarkList>\n"
    f"  <Bookmark Channel='{CHANNEL}' RecordId='999999999999' IsCurrent='true'/>\n"
    "</BookmarkList>"
)


def main() -> None:
    sink: "queue.Queue[tuple[str, object]]" = queue.Queue()

    def callback(action, _context, event):
        if action == win32evtlog.EvtSubscribeActionError:
            sink.put(("error", event))
        else:
            xml = win32evtlog.EvtRender(event, win32evtlog.EvtRenderEventXml)
            sink.put(("deliver", xml))
        return 0

    print("EvtCreateBookmark(fake XML with out-of-range RecordId) ...")
    try:
        bookmark = win32evtlog.EvtCreateBookmark(FAKE_BOOKMARK_XML)
        print("  succeeded (parses fine, no validation against live log yet)")
    except Exception as exc:  # noqa: BLE001 -- spike, we want to see the type
        print(f"  raised at create time: {type(exc).__name__}: {exc}")
        return

    print("EvtSubscribe(EvtSubscribeStartAfterBookmark, Bookmark=<stale>) ...")
    try:
        sub = win32evtlog.EvtSubscribe(
            CHANNEL,
            win32evtlog.EvtSubscribeStartAfterBookmark,
            Callback=callback,
            Bookmark=bookmark,
        )
        print("  EvtSubscribe call itself succeeded synchronously")
    except Exception as exc:  # noqa: BLE001
        print(f"  raised synchronously at subscribe time: {type(exc).__name__}: {exc}")
        return

    print("waiting up to 5s for an error-action callback or any delivery ...")
    deadline = time.monotonic() + 5
    seen = []
    while time.monotonic() < deadline:
        try:
            seen.append(sink.get(timeout=0.3))
        except queue.Empty:
            continue

    if not seen:
        print("RESULT: no callback fired at all (neither deliver nor error) within 5s")
    for kind, payload in seen:
        print(f"  callback fired: kind={kind} payload={payload!r}")

    print("now writing a fresh marker event to see if the stale subscription")
    print("silently falls through to 'deliver future events' behavior ...")
    import uuid

    import win32evtlogutil

    marker_c = str(uuid.uuid4())
    win32evtlogutil.ReportEvent(
        ".NET Runtime",
        9999,
        eventType=win32evtlog.EVENTLOG_INFORMATION_TYPE,
        strings=[f"ftmon-spike-marker {marker_c}"],
    )

    deadline = time.monotonic() + 5
    saw_c = False
    while time.monotonic() < deadline:
        try:
            kind, payload = sink.get(timeout=0.3)
        except queue.Empty:
            continue
        print(f"  callback fired: kind={kind}")
        if kind == "deliver" and marker_c in payload:
            saw_c = True

    print(f"RESULT: stale-bookmark subscription delivered the post-subscribe event: {saw_c}")

    del sub


if __name__ == "__main__":
    main()
