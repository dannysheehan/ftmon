"""[CA-01][CA-02][CA-03][EX-03] Function library golden tests and eval safety."""

from hypothesis import given
from hypothesis import strategies as st

from ftmon.expr import NameEnv, compile_expr
from ftmon.expr import functions as fx
from tests.conftest import FakeCtx

ENV = NameEnv(
    metrics=frozenset({"m", "c"}),
    attrs=frozenset({"name"}),
    params=frozenset({"p"}),
)
T0 = 1_700_000_000.0


def ctx_with(points, **kw):
    return FakeCtx(series={"m": [(T0 - 600 + i * 60, v) for i, v in enumerate(points)]},
                   wall=T0, **kw)


def ev(text, ctx):
    return compile_expr(text, ENV).eval(ctx, counter=ctx.count)


def test_avg_min_max_delta():
    ctx = ctx_with([1, 2, 3, 4])
    assert ev('avg(m, "10m")', ctx) == 2.5
    assert ev('min(m, "10m")', ctx) == 1
    assert ev('max(m, "10m")', ctx) == 4
    assert ev('delta(m, "10m")', ctx) == 3
    # [CA-02] empty window -> None
    empty = FakeCtx(wall=T0)
    assert ev('avg(m, "10m")', empty) is None
    assert ev('delta(m, "10m")', empty) is None


def test_slope_golden():
    """[CA-01] slope: 1 unit per 60s = 1/60 per second; < 3 points -> None."""
    ctx = ctx_with([10, 11, 12, 13])
    assert abs(ev('slope(m, "10m")', ctx) - 1 / 60) < 1e-12
    two = ctx_with([1, 2])
    assert ev('slope(m, "10m")', two) is None


def test_monot_boundaries():
    """[CA-01] monot - the legacy Filling test."""
    assert ev('monot(m, "10m")', ctx_with([1, 2, 3, 4])) == 1.0
    assert ev('monot(m, "10m")', ctx_with([4, 3, 2, 1])) == 0.0
    assert ev('monot(m, "10m")', ctx_with([1, 2, 1, 2])) == 2 / 3
    assert ev('monot(m, "10m")', ctx_with([1])) is None


def test_coverage_golden():
    """[CA-01] coverage: fraction of the window actually observed, clamped to [0, 1]."""
    # 11 points 60s apart span exactly 600s = the full "10m" window (exact boundary -> 1.0)
    full = ctx_with(list(range(11)))
    assert ev('coverage(m, "10m")', full) == 1.0
    # 3 points span 120s of a 2700s ("45m") window -> 120/2700 == 2/45
    sparse = ctx_with([1, 2, 3])
    assert abs(ev('coverage(m, "45m")', sparse) - 2 / 45) < 1e-12
    # < 2 points -> None (CA-02), including no samples at all
    assert ev('coverage(m, "10m")', ctx_with([1])) is None
    assert ev('coverage(m, "10m")', FakeCtx(wall=T0)) is None


def test_coverage_clamps_overspan_and_rejects_nonpositive_window():
    """[CA-01] pathological span > w clamps to 1.0; w <= 0 -> None, mirroring slope's guard."""
    over = [(0.0, 1.0), (100.0, 2.0)]  # span 100s exceeds a 10s window
    assert fx.f_coverage(over, 10.0) == 1.0
    assert fx.f_coverage(over, 0.0) is None
    assert fx.f_coverage(over, -5.0) is None


def test_rate_counter_reset():
    """[CA-03] negative delta -> 0.0 and a counter_reset self-metric."""
    ctx = ctx_with([100, 200, 50])
    assert ev('rate(m, "10m")', ctx) == 0.0
    assert ctx.counters.get("counter_reset") == 1
    ok = ctx_with([100, 160, 220])
    assert abs(ev('rate(m, "10m")', ok) - 1.0) < 1e-12  # 120 over 120s


def test_age_uses_last_sample_ts():
    ctx = ctx_with([1, 2, 3])  # last sample at T0 - 600 + 120
    assert ev("age(m)", ctx) == 600 - 120
    assert ev("age(m)", FakeCtx(wall=T0)) is None


def test_baseline_none_until_learned():
    """[CA-05] baseline None while learning keeps rules silent via CA-02."""
    ctx = ctx_with([1000])
    assert ev("m > baseline(m) * 4", ctx) is None
    ctx.baselines["m"] = 100.0
    assert ev("m > baseline(m) * 4", ctx) is True


def test_helpers():
    ctx = ctx_with([50])
    assert ev("pct(m, 200)", ctx) == 25.0
    assert ev("clamp(m, 0, 10)", ctx) == 10
    assert ev("roundv(m / 3, 1)", ctx) == 16.7
    assert ev("abs(0 - m)", ctx) == 50
    assert ev("coalesce(baseline(m), 7)", ctx) == 7


def test_string_functions():
    ctx = FakeCtx(attrs={"name": "firefox-bin"}, wall=T0)
    env = NameEnv(attrs=frozenset({"name"}))
    assert compile_expr('matches(name, "^firefox")', env).eval(ctx) is True
    assert compile_expr('contains(name, "fox")', env).eval(ctx) is True
    assert compile_expr('contains(name, "chrome")', env).eval(ctx) is False
    missing = FakeCtx(wall=T0)
    assert compile_expr('matches(name, "^firefox")', env).eval(missing) is None


def test_during_and_dow():
    env = NameEnv()
    # T0 = 2023-11-14 22:13:20 UTC; FakeCtx.now is interpreted in local time,
    # so compute the expected answers from the same conversion the code uses.
    import datetime

    t = datetime.datetime.fromtimestamp(T0)
    inside = f"{t.hour:02d}:00-{(t.hour + 1) % 24:02d}:00"
    ctx = FakeCtx(wall=T0)
    assert compile_expr(f'during("{inside}")', env).eval(ctx) is True
    wrapped = f"{(t.hour + 2) % 24:02d}:00-{(t.hour + 1) % 24:02d}:00"  # wraps, contains t
    assert compile_expr(f'during("{wrapped}")', env).eval(ctx) is True
    dow = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[t.weekday()]
    assert compile_expr(f'dow() == "{dow}"', env).eval(ctx) is True


@given(
    st.text(
        alphabet="mcp0123456789.+-*/%()<>=! \"'andornotifelse,",
        min_size=1,
        max_size=60,
    )
)
def test_eval_never_raises_on_arbitrary_accepted_input(text):
    """[EX-03][EX-06] property: if it compiles, eval never raises."""
    ctx = ctx_with([1, 2, 3], params={"p": 1.0})
    try:
        e = compile_expr(text, ENV)
    except Exception:
        return  # rejection at compile time is fine; eval safety is the property
    e.eval(ctx)  # must not raise


def test_deadline_cooperative():
    """[EX-03] deadline_check aborts evaluation -> None + counter."""
    ctx = ctx_with([1] * 10)
    e = compile_expr("m + m + m + " * 40 + "m", ENV)
    hits = {"n": 0}

    def deadline():
        hits["n"] += 1
        return True

    assert e.eval(ctx, deadline_check=deadline, counter=ctx.count) is None
    assert ctx.counters.get("eval_deadline") == 1
