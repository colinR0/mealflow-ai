"""Stable task contracts retained while assistant orchestration is rebuilt."""

RUN_TIMEOUT_SECONDS = 60
REBUILDING_CODE = "AGENT_REBUILDING"


class ExecutionStopped(RuntimeError):
    pass


class RunTimedOut(ExecutionStopped):
    pass
