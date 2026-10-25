"""Stop the entire native server process tree, including Windows child app.exe.

'gungnir run' may launch a child process. Stopping only the CLI leaves that
server bound to port 8000 and leaks in-memory login rate limits between tests.
"""
import os
import signal
import subprocess

def stop(process):
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False, timeout=15)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
