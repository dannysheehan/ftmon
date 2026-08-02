"""Spike: does win32evtlog.EvtSubscribe + EvtBookmark round-trip the way
SPEC.md SS4.1/DM-15 expects (persist a cursor string, resume later without
duplicate or missed delivery)? Not part of the shipped package -- run
manually with the spike venv:

    .venv-spike\\Scripts\\python.exe spikes\\windows-support\\evtlog_spike.py

Requires an existing, already-registered event source that the current
(non-admin) user can write to -- ".NET Runtime" under the Application
channel works on a stock Windows install. See spikes/windows-support/NOTES.md
for what this proved.
"""

import queue
import time
import uuid

import win32evtlog

CHANNEL = "Application"
SOURCE = ".NET Runtime"
EVENT_ID = 9999


def write_marker_event(marker: str) -> None:
    import win32evtlogutil

    win32evtlogutil.ReportEvent(
        SOURCE,
        EVENT_ID,
        eventType=win32evtlog.EVENTLOG_INFORMATION_TYPE,
        strings=[f"ftmon-spike-marker {marker}"],
    )


def make_callback(sink: "queue.Queue[tuple[int, str]]"):
    def callback(action, _context, event):
        if action == win32evtlog.EvtSubscribeActionDeliver:
            xml = win32evtlog.EvtRender(event, win32evtlog.EvtRenderEventXml)
            sink.put((action, xml))
        elif action == win32evtlog.EvtSubscribeActionError:
            sink.put((action, f"<error status={event!r}>"))
        return 0

    return callback


def drain(sink: "queue.Queue[tuple[int, str]]", seconds: float) -> list[str]:
    deadline = time.monotonic() + seconds
    out = []
    while time.monotonic() < deadline:
        try:
            _action, xml = sink.get(timeout=0.2)
            out.append(xml)
        except queue.Empty:
            continue
    return out


def main() -> None:
    print(f"pywin32 module: {win32evtlog.__file__}")

    marker_a = str(uuid.uuid4())
    marker_b = str(uuid.uuid4())

    # --- Phase 1: subscribe to future events, capture marker A + its bookmark ---
    # NB: the Event handle passed to the callback is only valid for the
    # duration of the callback invocation (EvtNext/EvtSubscribe semantics
    # mirror EvtNext's documented handle lifetime) -- rendering/bookmarking
    # must happen *inside* the callback, not on a handle stashed for later.
    # Confirmed empirically: doing it outside raised
    # pywintypes.error(6, 'EvtUpdateBookmark', 'The handle is invalid.').
    sink1: "queue.Queue[tuple[int, str]]" = queue.Queue()
    bookmark_xml_holder: list[str] = []

    def callback1(action, _context, event):
        if action == win32evtlog.EvtSubscribeActionDeliver:
            xml = win32evtlog.EvtRender(event, win32evtlog.EvtRenderEventXml)
            if marker_a in xml and not bookmark_xml_holder:
                bookmark = win32evtlog.EvtCreateBookmark(None)
                win32evtlog.EvtUpdateBookmark(bookmark, event)
                bookmark_xml_holder.append(
                    win32evtlog.EvtRender(bookmark, win32evtlog.EvtRenderBookmark)
                )
            sink1.put((action, xml))
        elif action == win32evtlog.EvtSubscribeActionError:
            sink1.put((action, f"<error status={event!r}>"))
        return 0

    sub1 = win32evtlog.EvtSubscribe(
        CHANNEL,
        win32evtlog.EvtSubscribeToFutureEvents,
        Callback=callback1,
    )
    print("subscription 1 open (EvtSubscribeToFutureEvents), writing marker A ...")
    time.sleep(0.5)  # let the subscription's internal thread spin up
    write_marker_event(marker_a)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not bookmark_xml_holder:
        try:
            sink1.get(timeout=0.5)
        except queue.Empty:
            continue

    if not bookmark_xml_holder:
        print("FAIL: never observed marker A via subscription 1")
        return
    print("observed marker A via callback, bookmarked in-callback")

    bookmark_xml = bookmark_xml_holder[0]
    print(f"bookmark XML persisted ({len(bookmark_xml)} chars):")
    print(bookmark_xml)

    # Drop subscription 1 (simulates cursor persisted across process restart).
    del sub1
    del callback1

    # --- Phase 2: write marker B *while nobody is subscribed* ---
    print("writing marker B with no active subscription ...")
    write_marker_event(marker_b)
    time.sleep(1.0)

    # --- Phase 3: resume from the persisted bookmark XML on a fresh handle ---
    sink2: "queue.Queue[tuple[int, str]]" = queue.Queue()
    resumed_bookmark = win32evtlog.EvtCreateBookmark(bookmark_xml)
    sub2 = win32evtlog.EvtSubscribe(
        CHANNEL,
        win32evtlog.EvtSubscribeStartAfterBookmark,
        Callback=make_callback(sink2),
        Bookmark=resumed_bookmark,
    )
    print("subscription 2 open (EvtSubscribeStartAfterBookmark) ...")
    collected = drain(sink2, seconds=8.0)

    saw_a = any(marker_a in xml for xml in collected)
    saw_b = any(marker_b in xml for xml in collected)

    print(f"events collected after resume: {len(collected)}")
    print(f"re-delivered marker A (should be False): {saw_a}")
    print(f"delivered marker B written while unsubscribed (should be True): {saw_b}")

    if saw_b and not saw_a:
        print("RESULT: PASS -- bookmark round-trip matches journald-cursor semantics")
    else:
        print("RESULT: FAIL -- see NOTES.md, does not match expected cursor semantics")


if __name__ == "__main__":
    main()
