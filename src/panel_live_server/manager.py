"""Panel server subprocess management.

This module manages the Panel server as a subprocess, including
startup, health checks, and shutdown.
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

import requests  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class PanelServerManager:
    """Manages the Panel server subprocess."""

    def __init__(
        self,
        db_path: Path,
        port: int = 5077,
        host: str = "localhost",
        max_restarts: int = 3,
    ):
        """Initialize the Panel server manager.

        Parameters
        ----------
        db_path : Path
            Path to SQLite database
        port : int
            Port for Panel server
        host : str
            Host address for Panel server
        max_restarts : int
            Maximum number of restart attempts
        """
        self.db_path = db_path
        self.port = port
        self.host = host
        self.max_restarts = max_restarts
        self.process: subprocess.Popen | None = None
        self.restart_count = 0

    def _is_port_in_use(self) -> bool:
        """Check if the configured port is already in use."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((self.host, self.port))
                return False
            except OSError:
                return True

    def _try_recover_stale_server(self) -> bool:
        """Try to recover from a stale server occupying the port.

        If the port is in use, checks whether the existing server is healthy.
        - If healthy AND we own the subprocess (``self.process`` is set): adopt it.
        - If healthy but we do NOT own it (orphan from a previous session): kill it
          and return ``False`` so ``start()`` launches a fresh subprocess with the
          current code.
        - If unhealthy (zombie): find and kill the stale process.

        Returns
        -------
        bool
            True if a healthy server we own is available on the port, False otherwise.
        """
        # First check if the existing server responds to health checks
        try:
            response = requests.get(f"http://{self.host}:{self.port}/api/health", timeout=3)
            if response.status_code == 200:
                if self.process is not None and self.process.poll() is None:
                    # We own this process — it is still running from this session.
                    logger.info(f"Found healthy Panel server already running on port {self.port}")
                    return True
                # Healthy but unowned — orphan from a previous MCP session (e.g. the
                # MCP server was killed with SIGTERM/SIGKILL before atexit could run).
                # Kill it so we start fresh with the current code.
                logger.info(f"Found orphaned Panel server on port {self.port} (not owned by this session) — " "stopping it to ensure current code is loaded.")
                stale_pid = self._find_pid_on_port()
                if stale_pid:
                    import os
                    import signal as _signal

                    try:
                        os.kill(stale_pid, _signal.SIGTERM)
                        for _ in range(10):
                            time.sleep(0.5)
                            if not self._is_port_in_use():
                                logger.info(f"Orphaned Panel server (PID {stale_pid}) stopped.")
                                return False
                        os.kill(stale_pid, _signal.SIGKILL)
                        time.sleep(1)
                    except ProcessLookupError:
                        pass  # Already gone
                    except PermissionError:
                        logger.error(f"No permission to kill orphaned process (PID {stale_pid})")
                        return True  # Can't replace it; adopt as fallback
                return False
        except requests.RequestException:
            pass

        # Port is occupied but server is unresponsive — try to find and kill it
        logger.warning(f"Port {self.port} is occupied by an unresponsive process, attempting cleanup")
        stale_pid = self._find_pid_on_port()
        if stale_pid:
            import os
            import signal

            logger.info(f"Killing stale Panel server process (PID {stale_pid})")
            try:
                os.kill(stale_pid, signal.SIGTERM)
                for _ in range(10):
                    time.sleep(0.5)
                    if not self._is_port_in_use():
                        logger.info(f"Stale process (PID {stale_pid}) cleaned up successfully")
                        return False
                os.kill(stale_pid, signal.SIGKILL)
                time.sleep(1)
            except ProcessLookupError:
                pass  # Already dead
            except PermissionError:
                logger.error(f"No permission to kill stale process (PID {stale_pid})")
                return False

        if self._is_port_in_use():
            logger.error(f"Cannot free port {self.port} — another process is using it")
            return False

        return False

    def _find_pid_on_port(self) -> int | None:
        """Find the PID of a process listening on the configured port.

        Uses psutil for cross-platform compatibility.
        """
        try:
            import psutil

            for conn in psutil.net_connections(kind="tcp"):
                if conn.laddr.port == self.port and conn.status == psutil.CONN_LISTEN:
                    return conn.pid
        except (ImportError, psutil.AccessDenied):
            pass
        return None

    def start(self) -> bool:
        """Start the Panel server subprocess.

        Returns
        -------
        bool
            True if started successfully, False otherwise
        """
        if self.process and self.process.poll() is None:
            logger.info("Panel server is already running")
            return True

        # Check if port is already in use (stale process from previous session)
        if self._is_port_in_use():
            if self._try_recover_stale_server():
                # A healthy server is already running, adopt it
                return True
            # Stale server was cleaned up or port was freed, continue with startup
            if self._is_port_in_use():
                logger.error(f"Port {self.port} is still in use, cannot start Panel server")
                return False

        try:
            # Get path to app.py
            app_path = Path(__file__).parent / "app.py"

            # Set up environment — use PANEL_LIVE_SERVER_* vars to match get_config()
            import os

            env = os.environ.copy()
            env["PANEL_LIVE_SERVER_DB_PATH"] = str(self.db_path)
            env["PANEL_LIVE_SERVER_PORT"] = str(self.port)
            env["PANEL_LIVE_SERVER_HOST"] = self.host

            logger.info(f"Using database at: {env['PANEL_LIVE_SERVER_DB_PATH']}")

            # Start subprocess
            logger.info(f"Starting Panel server on {self.host}:{self.port}")
            self.process = subprocess.Popen(
                [sys.executable, str(app_path)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Wait for server to be ready
            if self._wait_for_health():
                logger.info("Panel server started successfully")
                self.restart_count = 0
                return True
            else:
                logger.error("Panel server failed to start (health check timed out)")
                self.stop()
                return False

        except Exception as e:
            logger.exception(f"Error starting Panel server: {e}")
            return False

    def _wait_for_health(self, timeout: int = 30, interval: float = 1.0) -> bool:
        """Wait for Panel server to be healthy.

        Parameters
        ----------
        timeout : int
            Maximum time to wait in seconds
        interval : float
            Time between checks in seconds

        Returns
        -------
        bool
            True if server is healthy, False if timeout
        """
        start_time = time.time()
        base_url = f"http://{self.host}:{self.port}"

        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"{base_url}/api/health", timeout=2)
                if response.status_code == 200:
                    return True
            except requests.RequestException:
                pass  # Server not ready yet

            # Check if process died
            if self.process and self.process.poll() is not None:
                logger.error("Panel server process died during startup")
                return False

            time.sleep(interval)

        return False

    def is_healthy(self) -> bool:
        """Check if Panel server is healthy.

        Returns
        -------
        bool
            True if server is healthy, False otherwise
        """
        if not self.process or self.process.poll() is not None:
            return False

        try:
            base_url = f"http://{self.host}:{self.port}"
            response = requests.get(f"{base_url}/api/health", timeout=2)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def stop(self, timeout: int = 5) -> None:
        """Stop the Panel server subprocess.

        Parameters
        ----------
        timeout : int
            Maximum time to wait for graceful shutdown
        """
        if not self.process:
            return

        try:
            logger.info("Stopping Panel server")
            self.process.terminate()

            try:
                self.process.wait(timeout=timeout)
                logger.info("Panel server stopped gracefully")
            except subprocess.TimeoutExpired:
                logger.warning("Panel server did not stop gracefully, killing")
                self.process.kill()
                self.process.wait()

        except Exception as e:
            logger.exception(f"Error stopping Panel server: {e}")
        finally:
            self.process = None

    def restart(self) -> bool:
        """Restart the Panel server.

        Returns
        -------
        bool
            True if restarted successfully, False otherwise
        """
        if self.restart_count >= self.max_restarts:
            logger.error(f"Maximum restart attempts ({self.max_restarts}) reached")
            return False

        self.restart_count += 1
        logger.info(f"Restarting Panel server (attempt {self.restart_count}/{self.max_restarts})")
        self.stop()
        return self.start()

    def get_base_url(self) -> str:
        """Get the base URL for the Panel server.

        Returns
        -------
        str
            Base URL
        """
        return f"http://{self.host}:{self.port}"
