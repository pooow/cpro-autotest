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
Дата: Ноябрь 2025
"""

import paramiko
import time
import argparse
import sys
import json
import os
import yaml
import logging

# Пытаемся импортировать coloredlogs для красивого вывода
try:
    import coloredlogs
except ImportError:
    coloredlogs = None

# -----------------------------------------------------------------------------
# Настройки и Константы
# -----------------------------------------------------------------------------

# Путь к корневой директории проекта (на уровень выше infra/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Путь к файлу конфигурации
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

# Инициализация логгера для этого модуля
logger = logging.getLogger("deploy")

# -----------------------------------------------------------------------------
# Вспомогательные функции
# -----------------------------------------------------------------------------

def setup_logging(level="INFO"):
    """
    Настраивает формат и уровень логирования.
    Если доступен coloredlogs, использует его для цветного вывода.
    """
    log_fmt = '%(asctime)s - %(levelname)s - %(message)s'
    if coloredlogs:
        coloredlogs.install(level=level, fmt=log_fmt, logger=logger)
    else:
        logging.basicConfig(level=level, format=log_fmt)
        logger.warning("Совет: Установите 'coloredlogs' для цветного вывода (pip install coloredlogs)")

def load_config(config_path=CONFIG_PATH):
    """
    Загружает настройки из YAML файла.
    При ошибке завершает выполнение скрипта.
    
    :param config_path: Путь к файлу config.yaml
    :return: Словарь с конфигурацией
    """
    if not os.path.exists(config_path):
        logger.error(f"Файл конфигурации не найден: {config_path}")
        sys.exit(1)
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}
            logger.debug(f"Конфигурация успешно загружена из {config_path}")
            return config
    except Exception as e:
        logger.critical(f"Ошибка при чтении конфига: {e}")
        sys.exit(1)

def get_node_params(node_name, config):
    """
    Возвращает параметры подключения для конкретного узла Proxmox.
    
    :param node_name: Имя узла (например, 'r' или 'pve9')
    :param config: Загруженный объект конфигурации
    :return: Словарь {host, user, key, storage}
    """
    nodes = config.get("nodes", {})
    node_conf = nodes.get(node_name)
    
    if not node_conf:
        logger.error(f"Узел '{node_name}' не найден в config.yaml. Доступные: {list(nodes.keys())}")
        raise ValueError(f"Node '{node_name}' not configured")
    
    return {
        "host": node_conf.get("host"),
        "user": node_conf.get("user", "root"),
        "key": os.path.expanduser(node_conf.get("key_path", "~/.ssh/id_rsa")),
        # Если storage не указан, используем дефолт 'ram'
        "storage": node_conf.get("storage", "ram")
    }

def execute_ssh_command(client, command, dry_run=False, print_output=True, ignore_errors=False):
    """
    Выполняет SSH команду на удаленном сервере.
    
    :param client: Объект paramiko.SSHClient
    :param command: Строка команды для выполнения
    :param dry_run: Если True, команда не выполняется, только логируется
    :param print_output: Если True, вывод (stdout) пишется в лог уровня INFO
    :param ignore_errors: Если True, ошибки (exit code != 0) не выбрасывают исключение (но логируются в DEBUG)
    :return: Строка stdout (strip)
    """
    if dry_run:
        logger.warning(f"[DRY-RUN] Would execute: {command}")
        return "MOCK_OUTPUT_JSON"

    logger.info(f"Executing: {command}")
    stdin, stdout, stderr = client.exec_command(command)
    
    # Читаем вывод и ошибки
    out_str = stdout.read().decode().strip()
    err_str = stderr.read().decode().strip()
    exit_status = stdout.channel.recv_exit_status()

    # Логируем stdout, если просили и если он не пустой
    if print_output and out_str:
        logger.info(f"--- STDOUT ---\n{out_str}\n--------------")
    
    if exit_status != 0:
        if ignore_errors:
            # Ожидаемая ошибка (например, "ВМ не найдена" при проверке статуса)
            logger.debug(f"Command failed (expected/ignored). Exit: {exit_status}. Error: {err_str}")
        else:
            # Реальная ошибка
            logger.error(f"Command failed (Exit: {exit_status}): {command}")
            if err_str:
                logger.error(f"--- STDERR ---\n{err_str}\n--------------")
            raise Exception(f"SSH Command failed: {err_str}")
    
    return out_str

def wait_for_ip(client, vm_id, dry_run=False, timeout=60):
    """
    Ожидает появления IP-адреса у ВМ через QEMU Guest Agent.
    
    :param client: SSH клиент
    :param vm_id: ID виртуальной машины
    :param timeout: Максимальное время ожидания в секундах
    :return: IP адрес (str) или None, если не найден
    """
    logger.info(f"⏳ Waiting for IP address (Max {timeout}s)...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if dry_run:
            return "10.DRY.RUN.IP"

        try:
            # Запрашиваем интерфейсы. ignore_errors=True, чтобы не спамить в лог, 
            # если агент еще не запустился (qm вернет ошибку)
            json_out = execute_ssh_command(
                client, 
                f"qm guest cmd {vm_id} network-get-interfaces", 
                print_output=False, 
                ignore_errors=True
            )
            
            # Если команда вернула пустую строку (ошибка), json.loads упадет, поэтому проверяем
            if not json_out:
                time.sleep(3)
                continue

            data = json.loads(json_out)
            
            # Ищем первый подходящий IPv4 (кроме loopback)
            for iface in data:
                if iface.get('name') == 'lo': continue
                for addr in iface.get('ip-addresses', []):
                    if addr['ip-address-type'] == 'ipv4':
                        ip = addr['ip-address']
                        # Фильтр: берем только адреса из локальной сети 10.x
                        if ip.startswith("10."):
                            logger.info(f"✅ IP FOUND: {ip}")
                            return ip
        except Exception:
            pass # Любая ошибка парсинга или SSH - просто пробуем снова
        
        time.sleep(3)
    
    logger.warning("⚠️  Timeout waiting for IP. Guest Agent might not be running.")
    return None

# -----------------------------------------------------------------------------
# Основная логика
# -----------------------------------------------------------------------------

def deploy_vm(template_id, snap_name, new_vm_id, target_node=None, memory=None, dry_run=False, force=False):
    """
    Основная функция оркестрации развертывания.
    """
    # 1. Инициализация
    config = load_config()
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    
    # Определение узла (CLI аргумент > Config default > "r")
    if not target_node:
        target_node = config.get("default_node", "r")
    
    logger.info(f"🚀 STARTING DEPLOYMENT: Tpl={template_id} Snap={snap_name} NewID={new_vm_id} Node={target_node}")

    # Получаем параметры для выбранного узла
    try:
        node_params = get_node_params(target_node, config)
    except ValueError:
        sys.exit(1)

    # Распаковка параметров
    target_storage = node_params["storage"]
    host_ip = node_params["host"]
    ssh_user = node_params["user"]
    ssh_key = node_params["key"]
    
    # Параметры ВМ (CLI memory > Config > Default)
    if not memory:
        memory = config.get("deploy", {}).get("memory", 8192)

    client = None
    
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
                # Используем ignore_errors=True, т.к. ошибка "ВМ не найдена" - это норма
                execute_ssh_command(client, f"qm status {new_vm_id}", print_output=False, ignore_errors=True)
                # Если мы здесь и execute_ssh_command не выкинул Exception (а он не выкинет при ignore_errors),
                # нам надо проверить возвращаемое значение или exit code.
                # НО: execute_ssh_command с ignore_errors=True все равно вернет stdout или пустую строку.
                # qm status пишет в stdout "status: stopped" если ок, или в stderr если не ок.
                # Упростим: просто выполним команду без ignore_errors внутри try/except для надежности проверки "существования"
                execute_ssh_command(client, f"qm status {new_vm_id}", print_output=False)
                vm_exists = True
            else:
                vm_exists = True # В dry-run считаем что конфликт возможен
        except:
            vm_exists = False # Команда упала -> ВМ нет

        if vm_exists:
            if force:
                logger.warning(f"⚠️  VM {new_vm_id} exists. FORCE flag set -> Destroying...")
                try:
                    execute_ssh_command(client, f"qm stop {new_vm_id} --skiplock", dry_run=dry_run, print_output=False, ignore_errors=True)
                except: pass
            else:
                logger.error(f"❌ VM {new_vm_id} already exists!")
                if not dry_run:
                    choice = input(f"❓ Destroy VM {new_vm_id} and continue? [y/N]: ")
                    if choice.lower() != 'y':
                        logger.info("🛑 Operation aborted by user.")
                        return
                logger.warning("Stopping VM (User approved)...")
                try:
                    execute_ssh_command(client, f"qm stop {new_vm_id} --skiplock", dry_run=dry_run, print_output=False, ignore_errors=True)
                except: pass

        # ---------------------------------------------------------
        # 3. Очистка и подготовка дисков (Purge & Prepare)
        # ---------------------------------------------------------
        # Подгружаем .bashrc чтобы были доступны функции purge_vm_disks
        setup_cmd = "bash -ic 'source /root/.bashrc && purge_vm_disks && ./ramstor.sh'"
        logger.info("🧹 Cleaning up old disks and preparing RAM storage...")
        execute_ssh_command(client, setup_cmd, dry_run, print_output=True)
        
        # ---------------------------------------------------------
        # 4. Клонирование ВМ (Clone)
        # ---------------------------------------------------------
        logger.info(f"📦 Cloning Tpl:{template_id} -> VM:{new_vm_id} to Storage:'{target_storage}'")
        
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proxmox VM Automated Deployer")
    parser.add_argument("--tmpl-id", required=True, type=int, help="Template VM ID")
    parser.add_argument("--snap", required=True, help="Snapshot name")
    parser.add_argument("--new-id", required=True, type=int, help="New VM ID")
    parser.add_argument("--node", help="Target node name (defined in config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate actions without execution")
    parser.add_argument("--force", action="store_true", help="Force destroy existing VM without prompt")

    args = parser.parse_args()
    
    deploy_vm(
        args.tmpl_id, args.snap, args.new_id, 
        target_node=args.node, 
        dry_run=args.dry_run, 
        force=args.force
    )

