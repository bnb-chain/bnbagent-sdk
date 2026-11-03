"""Trace adapters (L1): external agent-trace formats -> normalized :class:`Session`.

``load_trace(data)`` auto-detects the format among the registered adapters; pass ``format=``
to skip detection. Built-in formats (see each module's docstring): ``native``, ``otel_genai``,
``agentcore_adot``, ``langfuse_strands``, ``langfuse_langchain``, ``langfuse_langgraph``,
``langsmith_langgraph``, ``smolagents``, ``taubench``, ``cx0``, ``eb_habitat``.
"""

from __future__ import annotations

from .agentcore_adot import AgentCoreAdotAdapter
from .base import (
    REGISTRY,
    AdapterRegistry,
    TraceAdapter,
    adapter_names,
    detect_format,
    get_adapter,
    load_trace,
    read_trace_file,
    register_adapter,
)
from .cx0 import Cx0Adapter
from .eb_habitat import EbHabitatAdapter
from .langfuse import LangfuseLangchainAdapter, LangfuseLanggraphAdapter, LangfuseStrandsAdapter
from .langsmith import LangsmithAdapter
from .native import NativeAdapter, dump_session, load_session
from .otel_genai import OtelGenAIAdapter
from .render import available_tools, render_trajectory, session_messages, tools_text
from .smolagents import SmolagentsAdapter
from .taubench import TauBenchAdapter

BUILTIN_ADAPTERS: tuple[TraceAdapter, ...] = (
    NativeAdapter(),
    OtelGenAIAdapter(),
    AgentCoreAdotAdapter(),
    LangfuseStrandsAdapter(),
    LangfuseLangchainAdapter(),
    LangfuseLanggraphAdapter(),
    LangsmithAdapter(),
    SmolagentsAdapter(),
    TauBenchAdapter(),
    Cx0Adapter(),
    EbHabitatAdapter(),
)

for _adapter in BUILTIN_ADAPTERS:
    register_adapter(_adapter, replace=True)

__all__ = [
    "AdapterRegistry",
    "BUILTIN_ADAPTERS",
    "REGISTRY",
    "TraceAdapter",
    "adapter_names",
    "available_tools",
    "detect_format",
    "dump_session",
    "get_adapter",
    "load_session",
    "load_trace",
    "read_trace_file",
    "register_adapter",
    "render_trajectory",
    "session_messages",
    "tools_text",
]
