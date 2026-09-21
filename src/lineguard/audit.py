from __future__ import annotations

import asyncio
import functools
import inspect
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar, cast

from pydantic import BaseModel

from lineguard import repository

P = ParamSpec("P")
R = TypeVar("R")


def _safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "<nested>"
    if isinstance(value, BaseModel):
        return _safe_value(value.model_dump(mode="json"), depth + 1)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_value(item, depth + 1) for key, item in list(value.items())[:30]}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth + 1) for item in list(value)[:30]]
    if isinstance(value, str) and len(value) > 1000:
        return value[:1000] + "...<truncated>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)[:1000]


def _output_summary(value: Any) -> dict[str, Any]:
    safe_value = _safe_value(value)
    return safe_value if isinstance(safe_value, dict) else {"result": safe_value}


def audited_tool(
    name: str,
    max_retries: int = 0,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Record deterministic tool inputs, outputs, latency, and failures."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(func):
            async_func = cast(Callable[P, Awaitable[Any]], func)

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs):
                task_id = kwargs.get("task_id")
                if task_id is None and args and isinstance(args[0], str):
                    task_id = args[0]
                base_input_summary = {
                    "args": _safe_value(args[1:] if task_id and args else args),
                    "kwargs": _safe_value(
                        {key: value for key, value in kwargs.items() if key != "task_id"}
                    ),
                }
                for attempt in range(1, max_retries + 2):
                    started = time.perf_counter()
                    input_summary = {
                        **base_input_summary,
                        "attempt": attempt,
                        "max_retries": max_retries,
                    }
                    try:
                        result = await async_func(*args, **kwargs)
                        duration_ms = round((time.perf_counter() - started) * 1000, 3)
                        output_summary = _output_summary(result)
                        domain_status = getattr(result, "status", None)
                        domain_status = getattr(domain_status, "value", domain_status)
                        record_status = (
                            str(domain_status)
                            if domain_status in {"failed", "needs_review"}
                            else "success"
                        )
                        if task_id:
                            repository.add_tool_call(
                                task_id=str(task_id),
                                tool_name=name,
                                status=record_status,
                                input_summary=input_summary,
                                output_summary=output_summary,
                                duration_ms=duration_ms,
                            )
                            repository.add_trace(
                                task_id=str(task_id),
                                event_type="tool",
                                name=name,
                                status=record_status,
                                input_summary=input_summary,
                                output_summary=output_summary,
                                duration_ms=duration_ms,
                            )
                        return result
                    except Exception as exc:
                        duration_ms = round((time.perf_counter() - started) * 1000, 3)
                        retrying = attempt <= max_retries
                        if task_id:
                            repository.add_tool_call(
                                task_id=str(task_id),
                                tool_name=name,
                                status="retrying" if retrying else "failed",
                                input_summary=input_summary,
                                output_summary={},
                                duration_ms=duration_ms,
                                error=str(exc),
                            )
                            repository.add_trace(
                                task_id=str(task_id),
                                event_type="tool",
                                name=name,
                                status="retrying" if retrying else "failed",
                                input_summary=input_summary,
                                output_summary={},
                                duration_ms=duration_ms,
                                error=str(exc),
                            )
                        if not retrying:
                            raise
                        await asyncio.sleep(0.05)
                raise RuntimeError("unreachable")

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            task_id = kwargs.get("task_id")
            if task_id is None and args and isinstance(args[0], str):
                task_id = args[0]
            base_input_summary = {
                "args": _safe_value(args[1:] if task_id and args else args),
                "kwargs": _safe_value({k: v for k, v in kwargs.items() if k != "task_id"}),
            }
            for attempt in range(1, max_retries + 2):
                started = time.perf_counter()
                input_summary = {
                    **base_input_summary,
                    "attempt": attempt,
                    "max_retries": max_retries,
                }
                try:
                    result = func(*args, **kwargs)
                    duration_ms = round((time.perf_counter() - started) * 1000, 3)
                    output_summary = _output_summary(result)
                    domain_status = getattr(result, "status", None)
                    domain_status = getattr(domain_status, "value", domain_status)
                    record_status = (
                        str(domain_status)
                        if domain_status in {"failed", "needs_review"}
                        else "success"
                    )
                    if task_id:
                        repository.add_tool_call(
                            task_id=str(task_id),
                            tool_name=name,
                            status=record_status,
                            input_summary=input_summary,
                            output_summary=output_summary,
                            duration_ms=duration_ms,
                        )
                        repository.add_trace(
                            task_id=str(task_id),
                            event_type="tool",
                            name=name,
                            status=record_status,
                            input_summary=input_summary,
                            output_summary=output_summary,
                            duration_ms=duration_ms,
                        )
                    return result
                except Exception as exc:
                    duration_ms = round((time.perf_counter() - started) * 1000, 3)
                    retrying = attempt <= max_retries
                    if task_id:
                        repository.add_tool_call(
                            task_id=str(task_id),
                            tool_name=name,
                            status="retrying" if retrying else "failed",
                            input_summary=input_summary,
                            output_summary={},
                            duration_ms=duration_ms,
                            error=str(exc),
                        )
                        repository.add_trace(
                            task_id=str(task_id),
                            event_type="tool",
                            name=name,
                            status="retrying" if retrying else "failed",
                            input_summary=input_summary,
                            output_summary={},
                            duration_ms=duration_ms,
                            error=str(exc),
                        )
                    if not retrying:
                        raise
                    time.sleep(0.05)
            raise RuntimeError("unreachable")

        return wrapper

    return decorator
