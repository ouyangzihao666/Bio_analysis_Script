"""Shared exception types for mdkit."""


class MdkitError(Exception):
    """Base class for all mdkit errors."""


class ConfigError(MdkitError):
    """Invalid workflow / systems configuration."""


class StepError(MdkitError):
    """A step failed while executing."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class CommandError(StepError):
    """An external command failed."""

    def __init__(self, message, argv=None, exit_code=None, output_tail=None, timed_out=False):
        super().__init__(message)
        self.argv = argv
        self.exit_code = exit_code
        self.output_tail = output_tail
        self.timed_out = timed_out


class InputError(StepError):
    """A required input file is missing or unreadable."""


class RunError(MdkitError):
    """A run failed at the orchestration level."""
