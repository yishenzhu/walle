import logging
import logging.handlers
from ..conf import PROJ_ROOT, auto_path, LogConfig


class TraceInjectingFilter(logging.Filter):
    def filter(self, record):
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.trace_id else "0" * 32
        record.span_id = format(ctx.span_id, "016x") if ctx.span_id else "0" * 16
        return True


def setup_logger(conf: LogConfig):

    root = logging.getLogger()
    root.setLevel(logging.ERROR)

    root.handlers.clear()

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=auto_path(conf.path),
        when="midnight",
        interval=1,
        backupCount=conf.backup_count,
        encoding="utf-8",
        delay=False,
    )

    formatter = logging.Formatter(
        "%(asctime)s|%(levelname)-5s|trace_id=%(trace_id)s span_id=%(span_id)s|%(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(TraceInjectingFilter())
    root.addHandler(handler)

    logging.getLogger(PROJ_ROOT.name).setLevel(conf.level_int)
