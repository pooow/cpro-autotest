"""
Модуль с утилитами для работы по SSH.
Содержит функции для выполнения команд и ожидания сети.
Используется во всех частях фреймворка (deploy, tests, plugins).
"""

import paramiko
import time
import json
import logging

# Пытаемся импортировать coloredlogs для красивого вывода (опционально)
try:
    import coloredlogs
except ImportError:
    coloredlogs = None

# Настройка логгера для этого модуля
logger = logging.getLogger("ssh_utils")

# Если coloredlogs доступен, настроим формат сразу при импорте,
# но лучше, чтобы вызывающий код настраивал root logger. 
# Здесь мы просто используем logger.

def execute_ssh_command(
    client: paramiko.SSHClient,
    command: str,
    dry_run: bool = False,
    print_output: bool = True,
    ignore_errors: bool = False,
) -> str:
    """
    Выполняет SSH команду на удаленном сервере.

    :param client: Активный SSH клиент paramiko.
    :param command: Строка команды (bash).
    :param dry_run: Если True, команда не выполняется, только логируется.
    :param print_output: Если True, вывод команды (stdout) пишется в лог INFO.
    :param ignore_errors: Если True, ошибки (exit code != 0) не выбрасывают
                          исключение (но логируются на уровне DEBUG).
    :return: Строка stdout (обрезанная от пробелов).
    """
    if dry_run:
        logger.warning(f"[DRY-RUN] Would execute: {command}")
        return "MOCK_OUTPUT_JSON"

    logger.info(f"Executing: {command}")
    # Используем exec_command
    stdin, stdout, stderr = client.exec_command(command)

    # Читаем потоки
    out_str = stdout.read().decode().strip()
    err_str = stderr.read().decode().strip()
    exit_status = stdout.channel.recv_exit_status()

    if print_output and out_str:
        logger.info(f"--- STDOUT ---\n{out_str}\n--------------")

    if exit_status != 0:
        if ignore_errors:
            logger.debug(
                f"Command failed (expected/ignored). Exit: {exit_status}. Error: {err_str}"
            )
        else:
            logger.error(f"Command failed (Exit: {exit_status}): {command}")
            if err_str:
                logger.error(f"--- STDERR ---\n{err_str}\n--------------")
            raise Exception(f"SSH Command failed: {err_str}")

    return out_str


def wait_for_ip(
    client: paramiko.SSHClient,
    vm_id: int,
    dry_run: bool = False,
    timeout: int = 60,
) -> str | None:
    """
    Ожидает появления IP-адреса у ВМ через QEMU Guest Agent.
    
    :param client: SSH клиент (подключенный к гипервизору Proxmox).
    :param vm_id: ID виртуальной машины.
    :param timeout: Максимальное время ожидания в секундах.
    :return: IP адрес (str) или None, если не найден.
    """
    logger.info(f"⏳ Waiting for IP address (Max {timeout}s)...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        if dry_run:
            return "10.DRY.RUN.IP"

        try:
            # Используем execute_ssh_command из этого же модуля
            json_out = execute_ssh_command(
                client,
                f"qm guest cmd {vm_id} network-get-interfaces",
                print_output=False,
                ignore_errors=True,
            )

            if not json_out:
                time.sleep(3)
                continue

            data = json.loads(json_out)

            for iface in data:
                # Игнорируем loopback
                if iface.get("name") == "lo":
                    continue
                # Ищем IPv4
                for addr in iface.get("ip-addresses", []):
                    if addr["ip-address-type"] == "ipv4":
                        ip = addr["ip-address"]
                        # Фильтр для локальной сети (опционально, можно вынести в конфиг)
                        if ip.startswith("10."):
                            logger.info(f"✅ IP FOUND: {ip}")
                            return ip
        except Exception:
            pass

        time.sleep(3)

    logger.warning("⚠️  Timeout waiting for IP. Guest Agent might not be running.")
    return None

