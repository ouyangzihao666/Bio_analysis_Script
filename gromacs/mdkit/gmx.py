"""Safe subprocess execution: no shell, timeout, dry-run, capture."""

from __future__ import annotations

import os
import logging
import shlex
import signal
import subprocess
import threading
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
        tee_path: Optional[str] = None,
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
        tail: List[bytes] = []
        tail_bytes = 0
        timed_out = False
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
            if stdin_text is not None:
                proc.stdin.write(stdin_text.encode("utf-8"))
                proc.stdin.close()

            tee_fh = None
            if tee_path:
                os.makedirs(os.path.dirname(os.path.abspath(tee_path)), exist_ok=True)
                tee_fh = open(tee_path, "ab")

            def reader():
                nonlocal tail_bytes
                try:
                    # gmx mdrun -v 的进度行以 \r 原地覆盖、很少写 \n，
                    # readline()（二进制流只按 \n 切分）会把后续进度一直
                    # 滞留在缓冲区直到进程结束；这里按原始字节流实时落盘，
                    # progress.py 读取时 universal newlines 会按 \r 切行。
                    while True:
                        chunk = proc.stdout.read1(4096)
                        if not chunk:
                            break
                        tail.append(chunk)
                        tail_bytes += len(chunk)
                        while tail_bytes > 65536 and tail:
                            tail_bytes -= len(tail[0])
                            tail.pop(0)
                        if tee_fh is not None:
                            tee_fh.write(chunk)
                            tee_fh.flush()
                except Exception:
                    pass
                finally:
                    if tee_fh is not None:
                        try:
                            tee_fh.close()
                        except Exception:
                            pass

            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            try:
                proc.wait(timeout=to)
            except subprocess.TimeoutExpired:
                timed_out = True
                self.interrupt()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            thread.join(timeout=5)
        except OSError as exc:
            self._current_proc = None
            raise CommandError(
                "无法执行命令: %s（%s）" % (display, exc),
                argv=argv,
            )
        finally:
            self._current_proc = None
            if proc is not None:
                for stream in (getattr(proc, "stdin", None), getattr(proc, "stdout", None)):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
        output_tail = _tail(b"".join(tail))
        self.log.debug("CMD output tail:\n%s", output_tail)
        if timed_out:
            raise CommandError(
                "命令超时（%ss）: %s" % (to, display),
                argv=argv,
                exit_code=None,
                output_tail=output_tail,
                timed_out=True,
            )
        if proc.returncode != 0:
            raise CommandError(
                "命令失败（退出码 %s）: %s" % (proc.returncode, display),
                argv=argv,
                exit_code=proc.returncode,
                output_tail=output_tail,
            )
        return {"returncode": 0, "output_tail": output_tail, "command": display}

    def run_gmx(
        self,
        args: List[str],
        stdin_text: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        tee_path: Optional[str] = None,
    ):
        return self.run(
            ["gmx"] + args,
            stdin_text=stdin_text,
            cwd=cwd,
            timeout=timeout,
            tee_path=tee_path,
        )


def _tail(data: bytes, limit: int = 20000) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return "…[截断]…\n" + text[-limit:]
