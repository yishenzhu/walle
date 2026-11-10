import logging
import logging.handlers
import os
import sys
from pathlib import Path

from ..conf import PROJECT_ROOT, LogConfig, resolve_path


class TraceInjectingFilter(logging.Filter):
    def filter(self, record):
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.trace_id else "0" * 32
        record.span_id = format(ctx.span_id, "016x") if ctx.span_id else "0" * 16
        return True


def setup_logger(conf: LogConfig):

    log_path = Path(resolve_path(conf.path))

    root = logging.getLogger()
    root.setLevel(logging.ERROR)

    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s|%(levelname)-5s|trace_id=%(trace_id)s span_id=%(span_id)s|%(message)s"
    )

    if os.getenv("WALLE_LOG_FILE", "1").lower() not in ("0", "false", "no", "off"):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_path),
            when="midnight",
            interval=1,
            backupCount=conf.backup_count,
            encoding="utf-8",
            delay=True,  # 懒创建：避免首启时空文件轮转报错
        )
        handler.setFormatter(formatter)
        handler.addFilter(TraceInjectingFilter())
        root.addHandler(handler)

    if os.getenv("WALLE_LOG_STDOUT", "").lower() in ("1", "true", "yes", "on"):
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        stream.addFilter(TraceInjectingFilter())
        root.addHandler(stream)

    logging.getLogger(PROJECT_ROOT.name).setLevel(conf.level_number)
