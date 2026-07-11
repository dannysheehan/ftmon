"""[IN-08][IN-05] Episode state machine: open/refresh/cooldown/quiet-clear/
reopen/flap/ack, confirm-count bursts, and the normative msg_hash vectors."""

from __future__ import annotations

from dataclasses import replace

from ftmon.engine.episodes import (
    EpisodeConfig,
    EpisodeState,
    msg_hash,
    step_episode,
)
from ftmon.model import NotifyEffect, RecordEffect

CFG = EpisodeConfig(
    monitor="events", rule_id="oom", entity_key="kernel:abc123",
    severity=4, cooldown_s=300.0, clear_after_s=1800.0,
)

T = 1_700_000_000.0


def kinds(effects):
    out = []
    for e in effects:
        if isinstance(e, NotifyEffect):
            out.append(f"notify:{e.notification.kind}")
        elif isinstance(e, RecordEffect):
            out.append(e.kind)
    return out


def match(t, msg="Out of memory: Killed process 4001"):
    return (t, msg)


class TestMsgHash:
    """[IN-08] SPEC 7.7.3 normative normalization vectors."""

    def test_digit_and_hex_runs_collapse(self):
        # different pids/addresses -> same episode
        assert msg_hash("Killed process 4001 (chrome)") == \
               msg_hash("Killed process 987654 (chrome)")
        assert msg_hash("fault at 0xdeadbeef4444") == \
               msg_hash("fault at 0x1234abcd9999")

    def test_case_and_whitespace_insensitive(self):
        assert msg_hash("OOM  Killer\tfired") == msg_hash("oom killer fired")

    def test_different_messages_differ(self):
        assert msg_hash("disk read error") != msg_hash("network unreachable")

    def test_shape(self):
        h = msg_hash("anything")
        assert len(h) == 16 and int(h, 16) >= 0  # 16 hex chars


class TestLifecycle:
    def test_single_event_opens_immediately(self):
        """[IN-08] confirm_count=1 (default): first match opens + notifies."""
        st, effects = step_episode(CFG, EpisodeState(), (match(T),), T)
        assert kinds(effects) == ["open", "notify:open"]
        assert st.core is not None and st.core.state == "open"
        assert st.core.occurrences == 1

    def test_refresh_accumulates_within_cooldown_silently(self):
        """[IN-08] repeats refresh occurrences; no popup inside cooldown."""
        st, _ = step_episode(CFG, EpisodeState(), (match(T),), T)
        st, effects = step_episode(CFG, st, (match(T + 60), match(T + 65)), T + 65)
        assert kinds(effects) == ["refresh"]  # recorded, not notified
        assert st.core.occurrences == 3
        assert st.last_seen_ts == T + 65

    def test_cooldown_renotify_carries_count(self):
        """[IN-08] renotify after cooldown; body says how many, which is the
        whole point of episodes (one popup for a 12x burst, not 12)."""
        st, _ = step_episode(CFG, EpisodeState(), (match(T),), T)
        st, effects = step_episode(CFG, st, (match(T + 301),), T + 301)
        notes = [e for e in effects if isinstance(e, NotifyEffect)]
        assert len(notes) == 1 and notes[0].notification.kind == "renotify"
        assert "2x since open" in notes[0].notification.body

    def test_quiet_period_clears_silently(self):
        """[IN-08] clear_after with no matches -> quiet_period clear and NO
        recovery popup (notify_recovery defaults False for event rules)."""
        st, _ = step_episode(CFG, EpisodeState(), (match(T),), T)
        st, effects = step_episode(CFG, st, (), T + 1799)
        assert effects == ()  # not yet
        st, effects = step_episode(CFG, st, (), T + 1801)
        assert kinds(effects) == ["clear"]  # no notify
        assert st.core.state == "cleared"
        clear = next(e for e in effects if isinstance(e, RecordEffect))
        assert clear.detail["reason"] == "quiet_period"

    def test_notify_recovery_true_sends_quiet_notice(self):
        cfg = replace(CFG, notify_recovery=True)
        st, _ = step_episode(cfg, EpisodeState(), (match(T),), T)
        st, effects = step_episode(cfg, st, (), T + 1801)
        assert kinds(effects) == ["clear", "notify:recover"]

    def test_reopen_after_clear_is_new_episode(self):
        """[IN-08] a match after quiet-clear opens fresh (occurrences reset)."""
        st, _ = step_episode(CFG, EpisodeState(), (match(T),), T)
        st, _ = step_episode(CFG, st, (), T + 1801)
        st, effects = step_episode(CFG, st, (match(T + 2000),), T + 2000)
        assert kinds(effects) == ["open", "notify:open"]
        assert st.core.occurrences == 1

    def test_ack_suppresses_renotify_but_still_clears(self):
        """[IN-08] acked episodes accumulate silently and quiet-clear."""
        st, _ = step_episode(CFG, EpisodeState(), (match(T),), T)
        st = replace(st, core=replace(st.core, state="acked"))
        st, effects = step_episode(CFG, st, (match(T + 400),), T + 400)
        assert kinds(effects) == ["refresh"]  # no renotify while acked
        st, effects = step_episode(CFG, st, (), T + 400 + 1801)
        assert kinds(effects) == ["clear"]

    def test_no_events_no_state_stays_empty(self):
        st, effects = step_episode(CFG, EpisodeState(), (), T)
        assert st == EpisodeState() and effects == ()


class TestConfirmBurst:
    CFG3 = replace(CFG, confirm_count=3, confirm_window_s=120.0)

    def test_needs_count_within_window(self):
        """[IN-08] confirm_count=3/120s: two matches stay pending; the third
        inside the window opens with occurrences=3."""
        st, effects = step_episode(self.CFG3, EpisodeState(), (match(T),), T)
        assert effects == () and st.core is None and len(st.pending_ts) == 1
        st, effects = step_episode(self.CFG3, st, (match(T + 30),), T + 30)
        assert effects == () and len(st.pending_ts) == 2
        st, effects = step_episode(self.CFG3, st, (match(T + 60),), T + 60)
        assert kinds(effects) == ["open", "notify:open"]
        assert st.core.occurrences == 3

    def test_stale_pending_expires(self):
        """[IN-08] an old lone event cannot combine with a much later one."""
        st, _ = step_episode(self.CFG3, EpisodeState(), (match(T),), T)
        st, _ = step_episode(self.CFG3, st, (), T + 200)  # window passed
        assert st.pending_ts == ()
        st, effects = step_episode(
            self.CFG3, st, (match(T + 300), match(T + 301)), T + 301)
        assert effects == ()  # only 2 fresh ones: still pending


class TestFlap:
    def test_three_quick_clears_mark_flapping_and_stretch_cooldown(self):
        """[IN-05] 3 clears within 10m -> next open is (flapping) with 4x
        cooldown, the episode version of jumping to max backoff."""
        st = EpisodeState()
        t = T
        for _ in range(3):
            st, _ = step_episode(CFG, st, (match(t),), t)
            # force quick clears with a tiny clear_after variant
            fast = replace(CFG, clear_after_s=10.0)
            st, _ = step_episode(fast, st, (), t + 11)
            t += 20
        st, effects = step_episode(CFG, st, (match(t),), t)
        note = next(e for e in effects if isinstance(e, NotifyEffect))
        assert note.notification.body.startswith("(flapping) ")
        assert st.cooldown_s == CFG.cooldown_s * 4
