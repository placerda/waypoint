"""OpenTelemetry configuration for distributed tracing, metrics, and logging."""

import logging
import os

import opentelemetry._logs as otel_logs
import opentelemetry.exporter.otlp.proto.grpc._log_exporter as log_exporter
import opentelemetry.exporter.otlp.proto.grpc.metric_exporter as metric_exporter
import opentelemetry.exporter.otlp.proto.grpc.trace_exporter as trace_exporter
import opentelemetry.metrics as otel_metrics
import opentelemetry.sdk._logs as otel_sdk_logs
import opentelemetry.sdk._logs.export as otel_logs_export
import opentelemetry.sdk.metrics as otel_sdk_metrics
import opentelemetry.sdk.metrics.export as otel_metrics_export
import opentelemetry.sdk.trace as otel_sdk_trace
import opentelemetry.sdk.trace.export as otel_trace_export
import opentelemetry.trace as otel_trace
from azure.monitor.opentelemetry.exporter import (
    ApplicationInsightsSampler,
    AzureMonitorLogExporter,
    AzureMonitorMetricExporter,
    AzureMonitorTraceExporter,
)

logger = logging.getLogger(__name__)
_configured = False


def configure_opentelemetry() -> None:
    """Export to Azure Monitor when bound, otherwise to the local OTLP/Aspire endpoint.

    Only application logs are exported. FastAPI instrumentation remains owned by
    main.py; exporters do not enable body, prompt, or completion recording.
    """
    global _configured
    if _configured:
        return

    try:
        connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
        span_exporter: otel_trace_export.SpanExporter
        metrics_exporter: otel_metrics_export.MetricExporter
        logs_exporter: otel_logs_export.LogRecordExporter
        if connection_string:
            span_exporter = AzureMonitorTraceExporter(connection_string=connection_string)
            metrics_exporter = AzureMonitorMetricExporter(connection_string=connection_string)
            logs_exporter = AzureMonitorLogExporter(connection_string=connection_string)
        else:
            span_exporter = trace_exporter.OTLPSpanExporter()
            metrics_exporter = metric_exporter.OTLPMetricExporter()
            logs_exporter = log_exporter.OTLPLogExporter()

        # Configure Traces
        otel_trace.set_tracer_provider(
            otel_sdk_trace.TracerProvider(
                sampler=ApplicationInsightsSampler(1.0) if connection_string else None
            )
        )
        span_processor = otel_trace_export.BatchSpanProcessor(span_exporter)
        trace_provider = otel_trace.get_tracer_provider()
        if isinstance(trace_provider, otel_sdk_trace.TracerProvider):
            trace_provider.add_span_processor(span_processor)

        # Configure Metrics
        metric_reader = otel_metrics_export.PeriodicExportingMetricReader(
            metrics_exporter, export_interval_millis=5000
        )
        otel_metrics.set_meter_provider(
            otel_sdk_metrics.MeterProvider(metric_readers=[metric_reader])
        )

        # Configure Logs
        otel_logs.set_logger_provider(otel_sdk_logs.LoggerProvider())
        log_processor = otel_logs_export.BatchLogRecordProcessor(logs_exporter)
        logs_provider = otel_logs.get_logger_provider()
        if isinstance(logs_provider, otel_sdk_logs.LoggerProvider):
            logs_provider.add_log_record_processor(log_processor)

        # Add OpenTelemetry handler to Python logging
        handler = otel_sdk_logs.LoggingHandler(level=logging.INFO, logger_provider=logs_provider)
        # SDK/exporter logs must not enter their own export pipeline.
        logging.getLogger("app").addHandler(handler)

        _configured = True
        logger.info("OpenTelemetry configured successfully")

    except Exception as e:
        logger.warning("Failed to configure OpenTelemetry (%s)", type(e).__name__)
