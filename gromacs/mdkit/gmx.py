"""Safe subprocess execution: no shell, timeout, dry-run, capture."""

from __future__ import annotations

import os
import logging
import shlex
import signal
import subprocess
from typing import List, Optional

from mdkit.exceptions import CommandError


class CommandRunner:
    """Runs external commands as argument lists (never through a shell)."""

    def __init__(self, log, dry_run: bool = False, timeout: Optional[float] = None):
        self.log = log if log is not None else logging.getLogger("mdkit")
        self.dry_run = dry_run
        self.timeout = timeout
        self._current_proc: Optional[subprocess.Popen] = None

    def interrupt(self) -> None:
        if self._current_proc is not None and self._current_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._current_proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                try:
                    self._current_proc.terminate()
                except Exception:
                    pass

    @staticmethod
    def quote(argv: List[str]) -> str:
        return " ".join(shlex.quote(a) for a in argv)

    def run(
        self,
        argv: List[str],
        stdin_text: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        timeout: Optional[float] = None,
    ):
        """Run argv; returns dict with returncode/output_tail.

        Raises CommandError on non-zero exit or timeout.
        """
        display = self.quote(argv)
        if stdin_text is not None and self.dry_run:
            display += "   <<< %r" % stdin_text
        self.log.debug("CMD: %s", display)
        if self.dry_run:
            return {"returncode": 0, "output_tail": "", "command": display, "dry_run": True}

        env_full = dict(os.environ)
        if env:
            env_full.update(env)
        to = timeout if timeout is not None else self.timeout
        proc = None
        stdout = None
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=env_full,
                start_new_session=True,
            )
            self._current_proc = proc
            stdout, _ = proc.communicate(
                input=stdin_text.encode("utf-8") if stdin_text is not None else None,
                timeout=to,
            )
        except subprocess.TimeoutExpired:
            self.interrupt()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            self._current_proc = None
            tail = _tail(stdout) if stdout else ""
            raise CommandError(
                "命令超时（%ss）: %s" % (to, display),
                argv=argv,
                exit_code=None,
                output_tail=tail,
                timed_out=True,
            )
        except OSError as exc:
            self._current_proc = None
            raise CommandError(
                "无法执行命令: %s（%s）" % (display, exc),
                argv=argv,
            )
        finally:
            self._current_proc = None
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        tail = _tail(output.encode("utf-8", errors="replace"))
        self.log.debug("CMD output tail:\n%s", tail)
        if proc.returncode != 0:
            raise CommandError(
                "命令失败（退出码 %s）: %s" % (proc.returncode, display),
                argv=argv,
                exit_code=proc.returncode,
                output_tail=tail,
            )
        return {"returncode": 0, "output_tail": tail, "command": display}

    def run_gmx(
        self,
        args: List[str],
        stdin_text: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        return self.run(["gmx"] + args, stdin_text=stdin_text, cwd=cwd, timeout=timeout)


def _tail(data: bytes, limit: int = 20000) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return "…[截断]…\n" + text[-limit:]
