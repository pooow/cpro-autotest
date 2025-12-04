"""
Модуль с утилитами для работы по SSH.
Содержит функции для выполнения команд, фильтрации вывода и ожидания сетевых событий.
Используется во всех частях фреймворка (deploy, tests, plugins) для унификации работы с paramiko.
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

def execute_ssh_command(
    client: paramiko.SSHClient,
    command: str,
    dry_run: bool = False,
    print_output: bool = True,
    ignore_errors: bool = False,
    log_command: bool = True,
) -> str:
    """
    Выполняет SSH команду на удаленном сервере.

    :param client: Активный SSH клиент paramiko.
    :param command: Строка команды (bash).
    :param dry_run: Если True, команда не выполняется, только логируется.
    :param print_output: Если True, вывод команды (stdout) пишется в лог INFO.
    :param ignore_errors: Если True, ошибки (exit code != 0) не выбрасывают
                          исключение (но логируются на уровне DEBUG).
    :param log_command: Если True, сама команда пишется в лог перед выполнением.
                        Стоит выключать для частых опросов (polling), чтобы не засорять лог.
    :return: Строка stdout (обрезанная от пробелов).
    """
    if dry_run:
        if log_command:
            logger.warning(f"[DRY-RUN] Would execute: {command}")
        return "MOCK_OUTPUT_JSON"

    if log_command:
        logger.info(f"Executing: {command}")

    # Выполняем команду
    stdin, stdout, stderr = client.exec_command(command)

    # Читаем и декодируем вывод (stdout и stderr)
    out_str = stdout.read().decode().strip()
    err_str = stderr.read().decode().strip()
    
    # Получаем код возврата (блокирует выполнение до завершения команды)
    exit_status = stdout.channel.recv_exit_status()

    # --- Фильтрация "мусора" из stdout ---
    # Некоторые утилиты (например, qemu-img при клонировании) пишут прогресс-бар
    # в stdout, создавая сотни строк вида "transferred ...".
    # Это засоряет лог-файлы и консоль. Убираем их.
    if out_str:
        filtered_lines = []
        for line in out_str.splitlines():
            # Если строка содержит признаки прогресс-бара (проценты + слово transferred), пропускаем
            if "transferred" in line and "%" in line:
                continue
            filtered_lines.append(line)
        out_str = "\n".join(filtered_lines).strip()

    # Вывод результата в лог, если требуется
    if print_output and out_str:
        logger.info(f"--- STDOUT ---\n{out_str}\n--------------")

    # Обработка ошибок
    if exit_status != 0:
        if ignore_errors:
            # Если ошибка ожидаема (например, проверка существования файла),
            # пишем в DEBUG, чтобы не пугать пользователя красным цветом.
            logger.debug(
                f"Command failed (expected/ignored). Exit: {exit_status}. Error: {err_str}"
            )
        else:
            # Критическая ошибка выполнения команды
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
    Повторяет запрос каждые 3 секунды до истечения таймаута.
    
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
            # Запрашиваем список интерфейсов у агента
            # log_command=False, чтобы не спамить в лог "Executing: ..." каждые 3 секунды
            json_out = execute_ssh_command(
                client,
                f"qm guest cmd {vm_id} network-get-interfaces",
                print_output=False,
                ignore_errors=True,
                log_command=False, 
            )

            # Если агент еще не ответил или вернул пустоту
            if not json_out:
                time.sleep(3)
                continue

            # Парсим JSON ответ от QEMU Guest Agent
            data = json.loads(json_out)

            for iface in data:
                # Игнорируем loopback интерфейс
                if iface.get("name") == "lo":
                    continue
                
                # Ищем первый попавшийся IPv4 адрес
                for addr in iface.get("ip-addresses", []):
                    if addr["ip-address-type"] == "ipv4":
                        ip = addr["ip-address"]
                        # Фильтр для локальной сети (чтобы не взять какой-нибудь Docker IP)
                        # Можно вынести префикс в конфиг, если нужно
                        if ip.startswith("10."):
                            logger.info(f"✅ IP FOUND: {ip}")
                            return ip
        except Exception:
            # Любая ошибка (SSH отвалился, JSON битый) — просто пробуем снова
            pass

        time.sleep(3)

    logger.warning("⚠️  Timeout waiting for IP. Guest Agent might not be running.")
    return None

