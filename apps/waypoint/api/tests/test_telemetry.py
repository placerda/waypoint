"""Hermetic telemetry wiring tests; no exporter can contact a remote endpoint."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import StringIO
from unittest.mock import Mock, patch

import fastapi
import opentelemetry._logs as otel_logs
import opentelemetry.exporter.otlp.proto.grpc._log_exporter as log_exporter
import opentelemetry.exporter.otlp.proto.grpc.metric_exporter as metric_exporter
import opentelemetry.exporter.otlp.proto.grpc.trace_exporter as trace_exporter
import opentelemetry.metrics as otel_metrics
import opentelemetry.trace as otel_trace
import pytest
from azure.monitor.opentelemetry.exporter import ApplicationInsightsSampler
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry
from app.common import tracer


@dataclass
class TelemetryPipeline:
    spans: InMemorySpanExporter = field(default_factory=InMemorySpanExporter)
    logs: InMemoryLogRecordExporter = field(default_factory=InMemoryLogRecordExporter)
    metrics_output: StringIO = field(default_factory=StringIO)
    providers: list[TracerProvider | MeterProvider | LoggerProvider] = field(default_factory=list)
    factories: dict[str, Mock] = field(default_factory=dict)


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[TelemetryPipeline]:
    result = TelemetryPipeline()
    metrics = ConsoleMetricExporter(out=result.metrics_output)
    for module, name, exporter in (
        (telemetry, "AzureMonitorTraceExporter", result.spans),
        (telemetry, "AzureMonitorMetricExporter", metrics),
        (telemetry, "AzureMonitorLogExporter", result.logs),
        (trace_exporter, "OTLPSpanExporter", result.spans),
        (metric_exporter, "OTLPMetricExporter", metrics),
        (log_exporter, "OTLPLogExporter", result.logs),
    ):
        factory = Mock(return_value=exporter)
        monkeypatch.setattr(module, name, factory)
        result.factories[name] = factory

    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(telemetry, "_configured", False)
    monkeypatch.setattr(otel_trace, "set_tracer_provider", result.providers.append)
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: result.providers[0])
    monkeypatch.setattr(otel_metrics, "set_meter_provider", result.providers.append)
    monkeypatch.setattr(otel_logs, "set_logger_provider", result.providers.append)
    monkeypatch.setattr(otel_logs, "get_logger_provider", lambda: result.providers[2])

    app_logger = logging.getLogger("app")
    handlers = app_logger.handlers.copy()
    level = app_logger.level
    app_logger.setLevel(logging.INFO)
    try:
        yield result
    finally:
        for handler in app_logger.handlers:
            if handler not in handlers:
                handler.close()
        app_logger.handlers[:] = handlers
        app_logger.setLevel(level)
        for provider in reversed(result.providers):
            provider.shutdown()


@pytest.mark.parametrize("azure_bound", [True, False])
def test_exporter_selection_and_all_three_signals(
    pipeline: TelemetryPipeline, monkeypatch: pytest.MonkeyPatch, azure_bound: bool
) -> None:
    if azure_bound:
        monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "test-binding")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "waypoint-api-test")

    telemetry.configure_opentelemetry()

    selected = (
        ("AzureMonitorTraceExporter", "AzureMonitorMetricExporter", "AzureMonitorLogExporter")
        if azure_bound
        else ("OTLPSpanExporter", "OTLPMetricExporter", "OTLPLogExporter")
    )
    for name, factory in pipeline.factories.items():
        if name in selected:
            factory.assert_called_once_with(
                **({"connection_string": "test-binding"} if azure_bound else {})
            )
        else:
            factory.assert_not_called()

    trace_provider, meter_provider, log_provider = pipeline.providers
    assert isinstance(trace_provider, TracerProvider)
    assert isinstance(meter_provider, MeterProvider)
    assert isinstance(log_provider, LoggerProvider)
    assert trace_provider.resource.attributes["service.name"] == "waypoint-api-test"
    if azure_bound:
        assert isinstance(trace_provider.sampler, ApplicationInsightsSampler)

    with trace_provider.get_tracer("test").start_as_current_span("test-request"):
        logging.getLogger("app.telemetry_test").info("application-event")
    meter_provider.get_meter("test").create_counter("test.requests").add(1)
    assert trace_provider.force_flush()
    assert log_provider.force_flush()
    assert meter_provider.force_flush()
    assert [span.name for span in pipeline.spans.get_finished_spans()] == ["test-request"]
    assert "application-event" in [
        record.log_record.body for record in pipeline.logs.get_finished_logs()
    ]
    assert "test.requests" in pipeline.metrics_output.getvalue()


@pytest.mark.parametrize("binding", ["", "  "])
def test_empty_binding_keeps_local_otlp(
    pipeline: TelemetryPipeline, monkeypatch: pytest.MonkeyPatch, binding: str
) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", binding)
    telemetry.configure_opentelemetry()
    pipeline.factories["OTLPSpanExporter"].assert_called_once_with()
    pipeline.factories["AzureMonitorTraceExporter"].assert_not_called()


def test_only_application_logs_are_exported(pipeline: TelemetryPipeline) -> None:
    root = logging.getLogger()
    handlers = root.handlers.copy()
    telemetry.configure_opentelemetry()
    assert root.handlers == handlers
    assert len(logging.getLogger("app").handlers) == 1

    logging.getLogger("app.modules.telemetry_test").warning("application-only")
    logging.getLogger("azure.monitor.opentelemetry.exporter").warning("exporter-not-captured")
    logging.getLogger("opentelemetry.exporter").warning("sdk-not-captured")
    root.warning("root-not-captured")
    log_provider = pipeline.providers[2]
    assert isinstance(log_provider, LoggerProvider)
    assert log_provider.force_flush()
    bodies = [record.log_record.body for record in pipeline.logs.get_finished_logs()]
    assert "application-only" in bodies
    assert not any(
        body in bodies
        for body in ("exporter-not-captured", "sdk-not-captured", "root-not-captured")
    )


def test_repeated_setup_does_not_duplicate_handlers_or_fastapi(
    pipeline: TelemetryPipeline,
) -> None:
    app = fastapi.FastAPI()
    tracer_provider = TracerProvider(shutdown_on_exit=False)
    meter_provider = MeterProvider(shutdown_on_exit=False)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        exclude_spans=["send", "receive"],
    )
    middleware = app.user_middleware.copy()
    try:
        with (
            patch.object(FastAPIInstrumentor, "instrument") as instrument,
            patch.object(FastAPIInstrumentor, "instrument_app") as instrument_app,
        ):
            telemetry.configure_opentelemetry()
            telemetry.configure_opentelemetry()
        instrument.assert_not_called()
        instrument_app.assert_not_called()
        assert app.user_middleware == middleware
        assert len(pipeline.providers) == 3
        assert len(logging.getLogger("app").handlers) == 1
        pipeline.factories["OTLPSpanExporter"].assert_called_once()
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        tracer_provider.shutdown()
        meter_provider.shutdown()


def test_configuration_failure_does_not_log_binding(
    pipeline: TelemetryPipeline, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "private-test-value")
    pipeline.factories["AzureMonitorTraceExporter"].side_effect = ValueError("private-test-value")
    telemetry.configure_opentelemetry()
    assert "private-test-value" not in caplog.text
    assert "ValueError" in caplog.text
    assert not telemetry._configured


async def test_service_tracing_does_not_record_function_content(
    pipeline: TelemetryPipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry.configure_opentelemetry()
    trace_provider = pipeline.providers[0]
    assert isinstance(trace_provider, TracerProvider)
    monkeypatch.setattr(otel_trace, "get_tracer", trace_provider.get_tracer)

    @tracer.trace
    def sync_service(content: str) -> str:
        return content

    @tracer.trace
    async def async_service(content: str) -> str:
        return content

    assert sync_service("private-body") == "private-body"
    assert await async_service("private-prompt") == "private-prompt"
    assert trace_provider.force_flush()
    spans = pipeline.spans.get_finished_spans()
    assert len(spans) == 2
    assert all(not span.attributes for span in spans)
