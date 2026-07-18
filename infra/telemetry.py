from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
    ConsoleMetricExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from ..conf import TelemetryConfig


def setup_telemetry(conf: TelemetryConfig):
    if not conf.enabled:
        trace.set_tracer_provider(TracerProvider())
        metrics.set_meter_provider(MeterProvider())
        return

    resource = Resource.create({"service.name": conf.service_name})

    span_exporters = []
    if conf.otlp.endpoint:
        span_exporters.append(
            OTLPSpanExporter(
                endpoint=conf.otlp.endpoint,
                insecure=conf.otlp.insecure,
            )
        )
    if conf.console_export:
        span_exporters.append(ConsoleSpanExporter())

    tracer_provider = TracerProvider(resource=resource)
    for exporter in span_exporters:
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(tracer_provider)

    metric_readers = []
    if conf.otlp.endpoint:
        metric_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=conf.otlp.endpoint,
                    insecure=conf.otlp.insecure,
                )
            )
        )
    if conf.console_export:
        metric_readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))

    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    metrics.set_meter_provider(meter_provider)

    OpenAIInstrumentor().instrument()


tracer = trace.get_tracer("agent")
meter = metrics.get_meter("agent")
