"""The run log, written and read by the same module.

The driver used to parse these records itself. That duplicated the event name
and every field name across a boundary with nothing keeping them in step --
the same shape of bug the layering rules exist to prevent, one level out.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from kingfisher.adapters.runlog import JsonlRunLogger, read_usage


def _logger(tmp_path):
    return JsonlRunLogger(
        tmp_path / "session.jsonl", model="m", api_style="anthropic", session_id="s"
    )


def _reply(**usage):
    message = AIMessage(content="ok", usage_metadata=usage)
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_usage_survives_the_round_trip(tmp_path):
    """Written and read by one module, so the format is pinned to itself rather
    than to a second copy of the field names somewhere else."""
    logger = _logger(tmp_path)
    for sent, got, cached in ((100, 10, 80), (200, 20, 100)):
        logger.on_llm_end(
            _reply(
                input_tokens=sent,
                output_tokens=got,
                total_tokens=sent + got,
                input_token_details={"cache_read": cached},
            )
        )

    usage = read_usage(tmp_path / "session.jsonl")
    assert usage.calls == 2
    assert usage.input_tokens == 300
    assert usage.output_tokens == 30
    assert usage.cache_read == 180
    assert usage.cached_share == 0.6


def test_a_log_with_no_model_calls_totals_zero(tmp_path):
    logger = _logger(tmp_path)
    logger.run_start("a task", "/runs/t001")

    usage = read_usage(tmp_path / "session.jsonl")
    assert usage.calls == 0
    assert usage.cached_share is None  # not a division by zero


def test_a_missing_log_is_not_an_error(tmp_path):
    """A run that died before writing should not take the summary with it."""
    assert read_usage(tmp_path / "never-written.jsonl").calls == 0


def test_a_torn_final_line_does_not_lose_the_rest(tmp_path):
    """The log is appended to as a run proceeds, so the last line can be
    partial if the process died mid-write. The earlier records still count."""
    logger = _logger(tmp_path)
    logger.on_llm_end(
        _reply(
            input_tokens=50,
            output_tokens=5,
            total_tokens=55,
            input_token_details={"cache_read": 25},
        )
    )
    path = tmp_path / "session.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "model_call", "input_tok')

    usage = read_usage(path)
    assert usage.calls == 1
    assert usage.input_tokens == 50
