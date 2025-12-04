#!/usr/bin/env python3
"""
Скрипт автоматического развертывания тестовых виртуальных машин в Proxmox.

Основные возможности:
- Поддержка нескольких узлов (Nodes) через config.yaml.
- Клонирование из шаблона (Template) + снапшота.
- Автоматическое удаление старой ВМ (Idempotency) с интерактивным подтверждением или флагом --force.
- Развертывание в RAM-диск (tmpfs) для максимальной скорости тестов.
- Ожидание получения IP-адреса через QEMU Guest Agent.
- Цветной вывод логов (coloredlogs).

Автор: pooow (с помощью AI)
Дата: Декабрь 2025
"""

import paramiko
import time
import argparse
import sys
import json
import logging

# Пытаемся импортировать coloredlogs для красивого вывода
try:
    import coloredlogs
except ImportError:
    coloredlogs = None

# Общий модуль конфигурации (DRY)
from infra.config import load_config, get_node_params


# -----------------------------------------------------------------------------
# Настройки логгера
# -----------------------------------------------------------------------------

logger = logging.getLogger("deploy")


def setup_logging(level: str = "INFO") -> None:
    """
    Настраивает формат и уровень логирования.
    Если доступен coloredlogs, использует его для цветного вывода.
    """
    log_fmt = "%(asctime)s - %(levelname)s - %(message)s"
    if coloredlogs:
        coloredlogs.install(level=level, fmt=log_fmt, logger=logger)
    else:
        logging.basicConfig(level=level, format=log_fmt)
        logger.warning(
            "Совет: установите 'coloredlogs' для цветного вывода (pip install coloredlogs)"
        )


# -----------------------------------------------------------------------------
# Вспомогательные функции
# -----------------------------------------------------------------------------

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
    stdin, stdout, stderr = client.exec_command(command)

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

    :param client: SSH клиент.
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
                if iface.get("name") == "lo":
                    continue
                for addr in iface.get("ip-addresses", []):
                    if addr["ip-address-type"] == "ipv4":
                        ip = addr["ip-address"]
                        if ip.startswith("10."):
                            logger.info(f"✅ IP FOUND: {ip}")
                            return ip
        except Exception:
            # Любая ошибка парсинга или SSH — просто пробуем снова
            pass

        time.sleep(3)

    logger.warning("⚠️  Timeout waiting for IP. Guest Agent might not be running.")
    return None


# -----------------------------------------------------------------------------
# Основная логика
# -----------------------------------------------------------------------------

def deploy_vm(
    template_id: int,
    snap_name: str,
    new_vm_id: int,
    target_node: str | None = None,
    memory: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """
    Основная функция оркестрации развертывания ВМ.
    """
    # 1. Инициализация: конфиг + логирование
    config = load_config()
    setup_logging(config.get("logging", {}).get("level", "INFO"))

    # Определяем целевой узел: CLI > config.default_node > "r"
    if not target_node:
        target_node = config.get("default_node", "r")

    logger.info(
        f"🚀 STARTING DEPLOYMENT: Tpl={template_id} Snap={snap_name} "
        f"NewID={new_vm_id} Node={target_node}"
    )

    # Получаем параметры для выбранного узла из общего модуля конфигурации
    try:
        node_params = get_node_params(target_node, config)
    except ValueError as e:
        logger.critical(str(e))
        sys.exit(1)

    target_storage = node_params["storage"]
    host_ip = node_params["host"]
    ssh_user = node_params["user"]
    ssh_key = node_params["key"]

    # Параметры ВМ (CLI > config.deploy > default)
    if memory is None:
        memory = config.get("deploy", {}).get("memory", 8192)

    client: paramiko.SSHClient | None = None

    try:
        # ---------------------------------------------------------
        # 1. Установка SSH соединения
        # ---------------------------------------------------------
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if not dry_run:
            logger.info(f"Connecting to {target_node} ({host_ip})...")
            client.connect(host_ip, username=ssh_user, key_filename=ssh_key)
            logger.debug("SSH connection established")
        else:
            logger.warning(f"[DRY-RUN] Mock connection to {target_node} ({host_ip})")

        # ---------------------------------------------------------
        # 2. Проверка конфликтов (Idempotency)
        # ---------------------------------------------------------
        vm_exists = False
        try:
            if not dry_run:
                # Пробуем получить статус ВМ. Если команды не существует — ВМ нет.
                execute_ssh_command(
                    client,
                    f"qm status {new_vm_id}",
                    print_output=False,
                )
                vm_exists = True
            else:
                vm_exists = True
        except Exception:
            vm_exists = False

        if vm_exists:
            if force:
                logger.warning(
                    f"⚠️  VM {new_vm_id} exists. FORCE flag set -> Destroying..."
                )
                try:
                    execute_ssh_command(
                        client,
                        f"qm stop {new_vm_id} --skiplock",
                        dry_run=dry_run,
                        print_output=False,
                        ignore_errors=True,
                    )
                except Exception:
                    pass
            else:
                logger.error(f"❌ VM {new_vm_id} already exists!")
                if not dry_run:
                    choice = input(
                        f"❓ Destroy VM {new_vm_id} and continue? [y/N]: "
                    )
                    if choice.lower() != "y":
                        logger.info("🛑 Operation aborted by user.")
                        return {"id": new_vm_id, "ip": None}
                logger.warning("Stopping VM (User approved)...")
                try:
                    execute_ssh_command(
                        client,
                        f"qm stop {new_vm_id} --skiplock",
                        dry_run=dry_run,
                        print_output=False,
                        ignore_errors=True,
                    )
                except Exception:
                    pass

        # ---------------------------------------------------------
        # 3. Очистка и подготовка дисков (Purge & Prepare)
        # ---------------------------------------------------------
        setup_cmd = (
            "bash -ic 'source /root/.bashrc && "
            "purge_vm_disks && ./ramstor.sh'"
        )
        logger.info("🧹 Cleaning up old disks and preparing RAM storage...")
        execute_ssh_command(client, setup_cmd, dry_run, print_output=True)

        # ---------------------------------------------------------
        # 4. Клонирование ВМ (Clone)
        # ---------------------------------------------------------
        logger.info(
            f"📦 Cloning Tpl:{template_id} -> VM:{new_vm_id} "
            f"to Storage:'{target_storage}'"
        )

        clone_cmd = (
            f"qm clone {template_id} {new_vm_id} "
            f"--snapname {snap_name} --storage {target_storage} && "
            f"qm set {new_vm_id} --cpu host --agent 1 --memory {memory}"
        )
        execute_ssh_command(client, clone_cmd, dry_run)

        # ---------------------------------------------------------
        # 5. Запуск и проверка сети (Start & Network)
        # ---------------------------------------------------------
        logger.info(f"▶️  Starting VM {new_vm_id}...")
        execute_ssh_command(client, f"qm start {new_vm_id}", dry_run)

        ip = wait_for_ip(client, new_vm_id, dry_run=dry_run)

        return {"id": new_vm_id, "ip": ip}

    except Exception as e:
        logger.critical(f"❌ DEPLOYMENT FAILED: {e}")
        sys.exit(1)
    finally:
        if client:
            client.close()
            logger.debug("SSH connection closed")


# -----------------------------------------------------------------------------
# CLI входная точка
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proxmox VM Automated Deployer")
    parser.add_argument("--tmpl-id", required=True, type=int, help="Template VM ID")
    parser.add_argument("--snap", required=True, help="Snapshot name")
    parser.add_argument("--new-id", required=True, type=int, help="New VM ID")
    parser.add_argument("--node", help="Target node name (defined in config.yaml)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate actions without execution"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force destroy existing VM without prompt",
    )

    args = parser.parse_args()

    deploy_vm(
        args.tmpl_id,
        args.snap,
        args.new_id,
        target_node=args.node,
        dry_run=args.dry_run,
        force=args.force,
    )

