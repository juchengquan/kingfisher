"""Model construction.

Always returns a **pre-built** `BaseChatModel` instance, never a
`"provider:model"` string. deepagents' `resolve_model` returns instances
unchanged, which is the only way past its built-in OpenAI provider profile —
that profile forces `use_responses_api=True`, sending requests to
`/v1/responses`, which virtually every OpenAI-compatible gateway does not
implement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.app.config import Config

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


def build_model(cfg: Config, role: str = "main") -> BaseChatModel:
    """Build the chat model for `role`, pointed at the configured endpoint."""
    model_id = cfg.model_for(role)

    if cfg.api_style == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_id,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout_s,
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_id,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout_s,
        # Without this, deepagents' provider profile would flip it on and the
        # request would go to /v1/responses. Verified against MiniMax.
        use_responses_api=False,
    )
