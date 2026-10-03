"""Safe errors for correcting tool arguments; never include submitted values."""


class ToolInputError(ValueError):
    code = "invalid_tool_input"
