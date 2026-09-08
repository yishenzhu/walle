from .telemetry import meter

TOOL_DURATION = meter.create_histogram(
    name="tool.call.duration",
    unit="ms",
    description="Tool call duration",
)

TOOL_CALLS = meter.create_counter(
    name="tool.calls",
    unit="1",
    description="Tool call count",
)

TOOL_ERRORS = meter.create_counter(
    name="tool.errors",
    unit="1",
    description="Tool call error count",
)

AGENT_ITERATIONS = meter.create_histogram(
    name="agent.iterations",
    unit="1",
    description="Iterations per chat turn",
)

HANDOFF = meter.create_counter(
    name="agent.handoff",
    unit="1",
    description="Agent handoff count",
)
