"""A small POSIX PTY adapter; Windows continues to use pywinpty."""
import codecs
import errno
import os
import signal
import subprocess


class PosixTerminal:
    def __init__(self, command, directory):
        import pty
        self.fd, slave = pty.openpty()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.eof = False
        try:
            self.process = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                stdout=slave, stderr=slave, start_new_session=True)
        except BaseException:
            os.close(self.fd)
            raise
        finally:
            os.close(slave)
        self.pid = self.process.pid

    def read(self, size):
        if self.eof:
            raise EOFError
        try:
            data = os.read(self.fd, size)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            data = b""
        if not data:
            self.eof = True
            tail = self.decoder.decode(b"", final=True)
            if tail:
                return tail
            raise EOFError
        return self.decoder.decode(data)

    def isalive(self):
        return self.process.poll() is None

    @property
    def exitstatus(self):
        try:
            # PTY EOF can precede waitpid readiness by a few milliseconds.
            return self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return None

    def close(self, force=True):
        if self.isalive():
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=3)
        else:
            self.process.wait()
        os.close(self.fd)
