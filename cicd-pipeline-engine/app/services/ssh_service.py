"""
SSH deployment service using Paramiko.
Connects to the remote server, pulls the new Docker image,
restarts the container, and runs a health check with rollback.
"""
import time
import logging
from app.core.config import settings

log = logging.getLogger(__name__)


class SSHDeployer:
    def __init__(
        self,
        host    : str,
        user    : str,
        key_path: str,
        port    : int = 22,
    ):
        self.host     = host
        self.user     = user
        self.key_path = key_path
        self.port     = port
        self._client  = None

    def connect(self):
        import paramiko
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname    = self.host,
            username    = self.user,
            key_filename= self.key_path,
            port        = self.port,
            timeout     = 15,
        )
        log.info("SSH connected to %s@%s:%s", self.user, self.host, self.port)

    def run(self, command: str, timeout: int = 300) -> tuple[int, str, str]:
        """
        Execute a command over SSH.
        Returns (exit_code, stdout, stderr).
        Timeout set to None for docker pull (large images take time).
        """
        if not self._client:
            raise RuntimeError("Not connected — call connect() first")

        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)

        # Set no timeout for the channel itself (command can run long)
        stdout.channel.settimeout(None)

        out  = stdout.read().decode("utf-8", errors="replace")
        err  = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err

    def deploy(self, image_tag: str, container_name: str, port_map: str = "8000:8000") -> list[str]:
        """
        Full deployment sequence:
          1. Pull new image
          2. Stop old container
          3. Start new container
          4. Health check
          5. Rollback on failure

        Returns list of log lines.
        """
        logs = []

        def log_step(msg: str):
            logs.append(msg)
            log.info(msg)

        log_step(f"[deploy] Pulling image: {image_tag}")
        code, out, err = self.run(f"docker pull {image_tag}", timeout=None)
        if code != 0:
            raise RuntimeError(f"docker pull failed: {err}")
        log_step(f"[deploy] Pull complete")

        # Capture current image for rollback
        _, current_image, _ = self.run(
            f"docker inspect --format='{{{{.Config.Image}}}}' {container_name} 2>/dev/null || echo ''"
        )
        current_image = current_image.strip()

        log_step(f"[deploy] Stopping old container: {container_name}")
        self.run(f"docker stop {container_name} 2>/dev/null || true")
        self.run(f"docker rm   {container_name} 2>/dev/null || true")

        log_step(f"[deploy] Starting new container")
        start_cmd = (
            f"docker run -d --name {container_name} "
            f"--restart unless-stopped "
            f"-p {port_map} {image_tag}"
        )
        code, out, err = self.run(start_cmd)
        if code != 0:
            raise RuntimeError(f"Container start failed: {err}")

        log_step(f"[deploy] Container started. Running health check...")
        healthy = self._health_check(container_name)

        if not healthy:
            log_step("[deploy] Health check FAILED — rolling back to previous image")
            if current_image:
                self.run(f"docker stop {container_name} 2>/dev/null || true")
                self.run(f"docker rm   {container_name} 2>/dev/null || true")
                rollback_cmd = (
                    f"docker run -d --name {container_name} "
                    f"--restart unless-stopped "
                    f"-p {port_map} {current_image}"
                )
                self.run(rollback_cmd)
                log_step(f"[deploy] Rolled back to: {current_image}")
            raise RuntimeError("Deployment failed health check — rollback complete")

        log_step("[deploy] ✅ Deployment successful and healthy")
        return logs

    def _health_check(self, container_name: str, retries: int = 6, delay: int = 5) -> bool:
        """Poll container status every 5s for up to 30s."""
        for attempt in range(1, retries + 1):
            time.sleep(delay)
            code, out, _ = self.run(
                f"docker inspect --format='{{{{.State.Status}}}}' {container_name}"
            )
            status = out.strip()
            log.info("Health check attempt %d/%d — status: %s", attempt, retries, status)
            if status == "running":
                return True
        return False

    def close(self):
        if self._client:
            self._client.close()
