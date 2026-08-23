"""The Eval preview tier: what goes on the socket, and what is never allowed to.

Two halves, and the second is the one with the expensive failure in it.

`preview_code` is pure and its output is the whole contract with Hyprland's Lua parser:
ADR-0005's headline case is a gradient, which *must* reach `hl.config` as a table because
`LuaConfigGradient.cpp` will not read the `"ff444444 0deg"` display text back. A preview
that spelled it the other way would be a preview of a config error.

`EvalPreview` is the sequencing, and the rule that matters is the one it shares with the
Apply transaction: `eval` clears `configerrors`, so a tick landing while a transaction is
confirming erases the errors that transaction is about to read -- and a rejected config then
reports as a clean apply. Every test below that names a transaction is testing that.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from _fake_hyprland import EVAL_UNSUPPORTED, OK, FakeHyprland
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.apply import EvalPreview, preview_code
from hyprtweaker.engine.ipc import CommandClient
from hyprtweaker.engine.model import ConfigModel, Gradient
from hyprtweaker.engine.schema import load_schema

T = TypeVar("T")

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)

GAPS_IN = "general:gaps_in"
ACTIVE_BORDER = "general:col.active_border"


def model_with(**values: object) -> ConfigModel:
    model = ConfigModel(SCHEMA)
    for key, value in values.items():
        model.set(key.replace("__", ":"), value)
    return model


def request_for(model: ConfigModel, name: str) -> str:
    """The wire request one tick of `name` should produce, built from the model itself.

    Derived rather than spelled out: a test that hand-typed the expected Lua could pass by
    agreeing with a typo, and the point of the assertion is that the *writer's* literal is
    what goes over the socket.
    """
    return f"eval {preview_code(model.option(name), model.get(name))}"


def with_preview(
    model: ConfigModel,
    scenario: Callable[[EvalPreview, FakeHyprland], Awaitable[T]],
    *,
    conversation: dict[str, str] | None = None,
    blocked: Callable[[], bool] = lambda: False,
    command_socket: bool = True,
) -> T:
    """Run `scenario` against a started `EvalPreview` over a scripted compositor.

    `command_socket=False` binds no command socket at all -- the compositor is gone, which
    is the failure the tier has to swallow rather than raise through a drag.
    """
    fake = FakeHyprland(conversation if conversation is not None else {})

    async def run() -> T:
        await fake.start(command_socket=command_socket, event_socket=False)
        preview = EvalPreview(
            model=model,
            client=CommandClient(fake.instance, timeout=0.5),
            is_blocked=blocked,
        )
        preview.start()
        try:
            return await scenario(preview, fake)
        finally:
            await preview.aclose()
            await fake.stop()

    return asyncio.run(run())


def evals(fake: FakeHyprland) -> list[str]:
    return [request for request in fake.requests if request.startswith("eval ")]


# --- what a tick puts on the wire -------------------------------------------------------------


def test_a_gradient_previews_as_a_lua_table_not_as_its_display_text() -> None:
    """ADR-0005's headline case. `LuaConfigGradient` cannot read `"ff444444 0deg"` back, so
    a preview spelling it that way would preview a config error."""
    option = SCHEMA[ACTIVE_BORDER]
    gradient = Gradient.parse("rgba(33ccffee) rgba(00ff99ee) 45deg")

    code = preview_code(option, gradient)

    assert code.startswith("hl.config{general={col={active_border={")
    assert "colors" in code and "angle" in code
    assert str(gradient) not in code, "the display text is not a value hl.config accepts"


def test_a_preview_is_one_line_and_sets_exactly_one_key() -> None:
    """`hl.config` merges per leaf, so a one-key call changes that key and nothing else."""
    model = model_with(general__gaps_in=6)

    code = preview_code(model.option(GAPS_IN), model.get(GAPS_IN))

    assert "\n" not in code
    assert code == "hl.config{general={gaps_in={ top = 6, right = 6, bottom = 6, left = 6 }}}"


def test_a_tick_reaches_the_socket_as_that_code() -> None:
    model = model_with(general__gaps_in=6)

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        preview.preview(GAPS_IN)
        await preview.flush()
        assert evals(fake) == [request_for(model, GAPS_IN)]

    with_preview(model, scenario, conversation={request_for(model, GAPS_IN): OK})


def test_an_unset_option_previews_nothing() -> None:
    """`eval` can set a value but has no way to say "stop setting this" -- only a reload
    re-reads the config from scratch, which is the Apply transaction's job."""
    model = ConfigModel(SCHEMA)

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        preview.preview(GAPS_IN)
        await preview.flush()
        assert evals(fake) == []

    with_preview(model, scenario)


# --- the sequencing ---------------------------------------------------------------------------


def test_only_the_newest_tick_of_a_burst_is_sent() -> None:
    """Latest wins. A drag emits ticks faster than round trips, and replaying the ones the
    user has already moved past would put the preview further behind with every tick."""
    model = model_with(general__gaps_in=4)
    # The request the burst *should* end on, taken before the drag rewinds the model.
    final = request_for(model, GAPS_IN)
    model.set(GAPS_IN, 1)

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        for gap in (2, 3, 4):
            model.set(GAPS_IN, gap)
            preview.preview(GAPS_IN)
        await preview.flush()

        assert evals(fake) == [final], "one request, and it carries the value it ended on"

    with_preview(model, scenario, conversation={final: OK})


def test_a_tick_is_dropped_while_an_apply_transaction_is_running() -> None:
    """The rule the whole tier hangs off: `eval` clears `configerrors`, so a preview landing
    between a reload and its error read makes a rejected config report as a clean apply."""
    model = model_with(general__gaps_in=6)

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        preview.preview(GAPS_IN)
        await preview.flush()
        assert evals(fake) == []

    with_preview(model, scenario, blocked=lambda: True)


def test_previews_resume_once_the_transaction_is_done() -> None:
    model = model_with(general__gaps_in=6)
    busy = True

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        nonlocal busy
        preview.preview(GAPS_IN)
        await preview.flush()
        assert evals(fake) == []

        busy = False
        preview.preview(GAPS_IN)
        await preview.flush()
        assert evals(fake) == [request_for(model, GAPS_IN)]

    with_preview(
        model,
        scenario,
        blocked=lambda: busy,
        conversation={request_for(model, GAPS_IN): OK},
    )


def test_forget_drops_a_tick_that_has_not_been_sent_yet() -> None:
    """What a reload leaves behind. The eval state is gone and the model is about to be
    re-read, so sending the tick would contradict the re-read a moment later."""
    model = model_with(general__gaps_in=6)

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        preview.preview(GAPS_IN)
        preview.forget()
        await preview.flush()
        assert evals(fake) == []

    with_preview(model, scenario, conversation={request_for(model, GAPS_IN): OK})


# --- failure is not the user's problem --------------------------------------------------------


def test_a_session_that_refuses_eval_is_never_asked_twice() -> None:
    """A hyprlang session refuses `eval` outright. Nothing the app sends will work, and the
    Options still apply through the file write -- so the tier goes quiet instead of spending
    a round trip per drag tick on a refusal."""
    model = model_with(general__gaps_in=6)

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        preview.preview(GAPS_IN)
        await preview.flush()
        assert preview.supported is False

        model.set(GAPS_IN, 7)
        preview.preview(GAPS_IN)
        await preview.flush()
        assert len(evals(fake)) == 1

    with_preview(model, scenario, conversation={request_for(model, GAPS_IN): EVAL_UNSUPPORTED})


def test_a_socket_that_is_not_there_drops_the_tick_rather_than_raising() -> None:
    """Best-effort by design: the value the user chose still lands through the Apply
    transaction on release, and that is the result they hear about."""
    model = model_with(general__gaps_in=6)

    async def scenario(preview: EvalPreview, _fake: FakeHyprland) -> None:
        preview.preview(GAPS_IN)
        await preview.flush()
        assert preview.last_code is None

    with_preview(model, scenario, command_socket=False)


def test_a_rejected_value_leaves_the_tier_working() -> None:
    """Hyprland answering with a parse error is a fact about one value, not about the
    session -- unlike the hyprlang refusal, the next tick is still worth sending."""
    model = model_with(general__gaps_in=6)

    async def scenario(preview: EvalPreview, fake: FakeHyprland) -> None:
        preview.preview(GAPS_IN)
        await preview.flush()

        assert preview.supported is True
        assert evals(fake) == [request_for(model, GAPS_IN)]

    with_preview(
        model,
        scenario,
        conversation={request_for(model, GAPS_IN): "error setting 'general.gaps_in'"},
    )
