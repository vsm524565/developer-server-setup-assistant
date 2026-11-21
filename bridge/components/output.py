"""Machine-readable output for ``main.py --json`` (Milestone 4.40).

In JSON mode stdout carries JSON Lines and nothing else:

    {"schema_version": "1.0", "type": "event", "event": NAME, "data": {...}}   zero or more
    {"schema_version": "1.0", "type": "result", "command": ..., "ok": ...,
     "exit_code": ..., "data": {...}, "error": null | {"message", "issues"}}   exactly one, last

Text meant for a person (plans, prompts, the server's live terminal) goes
to stderr. A program reads stdout line by line; the last line is always
the result, also after an error. Design: docs/design/json-output.md.
"""

import json
import sys

SCHEMA_VERSION = "1.0"


class Output:
    """Collects events and the result of one command."""

    def __init__(self, json_mode=False, stream=None):
        self.json = json_mode
        self.stream = stream if stream is not None else sys.stdout
        self.data = {}
        self.error = None

    def _write(self, document):
        self.stream.write(json.dumps(document, sort_keys=True) + "\n")
        self.stream.flush()

    def event(self, name, **data):
        if self.json:
            self._write({"schema_version": SCHEMA_VERSION, "type": "event",
                         "event": name, "data": data})

    def set(self, **data):
        self.data.update(data)

    def fail(self, message, issues=()):
        self.error = {"message": str(message),
                      "issues": [str(i) for i in issues if str(i) != str(message)]}

    def result(self, command, exit_code):
        if self.json:
            self._write({"schema_version": SCHEMA_VERSION, "type": "result",
                         "command": command, "ok": exit_code == 0,
                         "exit_code": exit_code, "data": self.data, "error": self.error})


class LogTee:
    """Byte sink for a run's terminal stream in JSON mode.

    The bytes go to stderr unchanged (the operator still sees the sudo
    prompt and the live log), and each complete line also becomes a
    ``log`` event on stdout.
    """

    def __init__(self, output, terminal=None):
        self.output = output
        self.terminal = terminal if terminal is not None else sys.stderr.buffer
        self.pending = b""

    def write(self, chunk):
        self.terminal.write(chunk)
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            self.output.event("log", line=line.decode(errors="replace").rstrip("\r"))
        return len(chunk)

    def flush(self):
        self.terminal.flush()

    def close(self):
        if self.pending:
            self.output.event("log", line=self.pending.decode(errors="replace").rstrip("\r"))
            self.pending = b""
