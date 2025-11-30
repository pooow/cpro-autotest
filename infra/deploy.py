#!/usr/bin/env python3
"""
Скрипт автоматического развертывания тестовых виртуальных машин в Proxmox.
Поддерживает:
- Работу с несколькими узлами (через config.yaml).
- Клонирование из шаблона + снапшота.
- Развертывание в RAM-диск (tmpfs) для скорости.
- Dry-run режим (безопасный прогон).
- Цветной вывод логов.
"""

import paramiko
import time
import argparse
import sys
import json
import os
import yaml
import logging

# Попытка импорта coloredlogs для красоты
try:
    import coloredlogs
except ImportError:
    coloredlogs = None

# Настройка путей
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

# Инициализация логгера
logger = logging.getLogger("deploy")

def setup_logging(level="INFO"):
    """Настраивает цветное логирование."""
    log_format = '%(asctime)s %(hostname)s %(name)s[%(process)d] %(levelname)s %(message)s'
    if coloredlogs:
        coloredlogs.install(level=level, fmt='%(asctime)s - %(levelname)s - %(message)s', logger=logger)
    else:
        logging.basicConfig(level=level, format='%(asctime)s - %(levelname)s - %(message)s')
        logger.warning("Install 'coloredlogs' for better visual output: pip install coloredlogs")

def load_config(config_path=CONFIG_PATH):
    """
    Загружает конфигурацию из YAML файла.
    :param config_path: Путь к файлу config.yaml
    :return: Словарь с конфигурацией или exit(1) при ошибке.
    """
    if not os.path.exists(config_path):
        logger.error(f"Config file not found at {config_path}")
        sys.exit(1)
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}
            logger.debug(f"Config loaded successfully from {config_path}")
            return config
    except Exception as e:
        logger.critical(f"Error loading config: {e}")
        sys.exit(1)

def get_node_params(node_name, config):
    """
    Извлекает параметры конкретного узла из конфига.
    """
    nodes = config.get("nodes", {})
    node_conf = nodes.get(node_name)
    
    if not node_conf:
        valid_nodes = list(nodes.keys())
        logger.error(f"Unknown node '{node_name}'. Available nodes: {valid_nodes}")
        raise ValueError(f"Node '{node_name}' not defined in config.yaml")
    
    return {
        "host": node_conf.get("host"),
        "user": node_conf.get("user", "root"),
        "key": os.path.expanduser(node_conf.get("key_path", "~/.ssh/id_rsa")),
        "storage": node_conf.get("storage", "ram") # Значение по умолчанию, если не задано
    }

def execute_ssh_command(client, command, dry_run=False, print_output=True):
    """
    Выполняет SSH команду на удаленном сервере.
    
    :param client: Активный SSH клиент paramiko.
    :param command: Строка команды (bash).
    :param dry_run: Если True, команда не выполняется, только логируется.
    :param print_output: Если True, вывод команды (stdout) пишется в лог INFO.
    :return: Строка stdout (обрезанная от пробелов).
    """
    if dry_run:
        logger.warning(f"[DRY-RUN] Would execute: {command}")
        return "MOCK_OUTPUT_JSON"

    logger.info(f"Executing: {command}")
    stdin, stdout, stderr = client.exec_command(command)
    
    # Чтение потоков
    out_str = stdout.read().decode().strip()
    err_str = stderr.read().decode().strip()
    exit_status = stdout.channel.recv_exit_status()

    # Логирование вывода
    if print_output and out_str:
        logger.info(f"--- STDOUT ---\n{out_str}\n--------------")
    
    if exit_status != 0:
        # Логируем ошибку как ERROR, но выбрасываем исключение для обработки выше
        logger.error(f"Command failed (Exit: {exit_status}): {command}")
        if err_str:
            logger.error(f"--- STDERR ---\n{err_str}\n--------------")
        raise Exception(f"SSH Command failed: {err_str}")
    
    return out_str

def deploy_vm(template_id, snap_name, new_vm_id, target_node=None, memory=None, dry_run=False, force=False):
    """
    Основная функция развертывания ВМ.
    """
    # 1. Загрузка конфигурации
    config = load_config()
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    
    # Определение целевого узла
    if not target_node:
        target_node = config.get("default_node", "r")
    
    logger.info(f"🚀 Starting Deploy: Tpl={template_id} Snap={snap_name} NewID={new_vm_id} Node={target_node}")

    # Получение параметров подключения
    try:
        node_params = get_node_params(target_node, config)
    except ValueError as e:
        sys.exit(1)

    # Извлечение параметров из конфига
    target_storage = node_params["storage"]
    host_ip = node_params["host"]
    ssh_user = node_params["user"]
    ssh_key = node_params["key"]
    
    # Параметры ВМ (дефолты)
    if not memory:
        memory = config.get("deploy", {}).get("memory", 8192)

    client = None
    
    try:
        # ==================================================================
        # ЭТАП 1: Подключение SSH
        # ==================================================================
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if not dry_run:
            logger.info(f"Connecting to {target_node} ({host_ip})...")
            client.connect(host_ip, username=ssh_user, key_filename=ssh_key)
            logger.info("✅ Connected successfully")
        else:
            logger.warning(f"[DRY-RUN] Mock connection to {target_node} ({host_ip})")

        # ==================================================================
        # ЭТАП 2: Проверка существующей ВМ (Idempotency Check)
        # ==================================================================
        vm_exists = False
        try:
            if not dry_run:
                # qm status возвращает "status: running/stopped" или падает, если ВМ нет
                execute_ssh_command(client, f"qm status {new_vm_id}", print_output=False)
                vm_exists = True
            else:
                vm_exists = True # В режиме dry-run всегда считаем, что риск есть
        except:
            vm_exists = False

        if vm_exists:
            if force:
                logger.warning(f"⚠️  VM {new_vm_id} exists. FORCE flag is set -> Destroying...")
                try:
                    execute_ssh_command(client, f"qm stop {new_vm_id} --skiplock", dry_run=dry_run, print_output=False)
                except: pass # Игнорируем ошибку остановки
            else:
                logger.error(f"❌ VM {new_vm_id} already exists on node {target_node}!")
                if not dry_run:
                    # Интерактивный вопрос
                    choice = input(f"❓ Do you want to DESTROY VM {new_vm_id} and reinstall? [y/N]: ")
                    if choice.lower() != 'y':
                        logger.info("Aborting deployment by user request.")
                        return
                logger.warning("Stopping VM (Force reinstall approved)...")
                try:
                    execute_ssh_command(client, f"qm stop {new_vm_id} --skiplock", dry_run=dry_run, print_output=False)
                except: pass

        # ==================================================================
        # ЭТАП 3: Подготовка окружения (purge & ramdisk)
        # ==================================================================
        # Важно: подгружаем .bashrc, чтобы работали функции purge_vm_disks
        setup_cmd = "bash -ic 'source /root/.bashrc && purge_vm_disks && ./ramstor.sh'"
        logger.info("Cleaning up old disks and mounting RAM storage...")
        execute_ssh_command(client, setup_cmd, dry_run, print_output=True)
        
        # ==================================================================
        # ЭТАП 4: Клонирование ВМ
        # ==================================================================
        logger.info(f"Cloning template {template_id} -> {new_vm_id} to storage '{target_storage}'")
        
        clone_cmd = (
            f"qm clone {template_id} {new_vm_id} "
            f"--snapname {snap_name} --storage {target_storage} && " # <-- Storage из конфига
            f"qm set {new_vm_id} --cpu host --agent 1 --memory {memory}"
        )
        execute_ssh_command(client, clone_cmd, dry_run)
        logger.info("✅ Clone complete")
        
        # ==================================================================
        # ЭТАП 5: Запуск и ожидание сети
        # ==================================================================
        logger.info(f"Starting VM {new_vm_id}...")
        execute_ssh_command(client, f"qm start {new_vm_id}", dry_run)
        
        logger.info("Waiting for IP address (Guest Agent)...")
        start_time = time.time()
        vm_ip = None
        
        while time.time() - start_time < 60:
            if dry_run:
                vm_ip = "10.DRY.RUN.IP"
                break
            try:
                # qm guest cmd возвращает JSON
                json_out = execute_ssh_command(client, f"qm guest cmd {new_vm_id} network-get-interfaces", print_output=False)
                data = json.loads(json_out)
                
                # Парсинг JSON для поиска IPv4
                for iface in data:
                    if iface.get('name') == 'lo': continue # Пропускаем loopback
                    for addr in iface.get('ip-addresses', []):
                        if addr['ip-address-type'] == 'ipv4':
                            ip = addr['ip-address']
                            if ip.startswith("10."): # Фильтр по локальной сети
                                vm_ip = ip
                                break
                    if vm_ip: break
            except Exception:
                # Агент еще не загрузился или ошибка SSH
                pass
            
            if vm_ip:
                logger.info(f"✅ FOUND IP: {vm_ip}")
                break
            
            time.sleep(3) # Ждем перед повтором
            
        if not vm_ip:
             logger.warning("⚠️  Timeout waiting for IP. Guest Agent might not be running.")
             
        return {"id": new_vm_id, "ip": vm_ip}

    except Exception as e:
        logger.critical(f"❌ Deployment FAILED: {e}")
        sys.exit(1)
    finally:
        if client:
            client.close()
            logger.debug("SSH connection closed")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proxmox VM Deployer")
    parser.add_argument("--tmpl-id", required=True, type=int, help="Template VM ID")
    parser.add_argument("--snap", required=True, help="Snapshot name")
    parser.add_argument("--new-id", required=True, type=int, help="New VM ID")
    parser.add_argument("--node", help="Target node name (see config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution")
    parser.add_argument("--force", action="store_true", help="Force destroy existing VM")

    args = parser.parse_args()
    
    # Запуск
    deploy_vm(
        args.tmpl_id, args.snap, args.new_id, 
        target_node=args.node, 
        dry_run=args.dry_run, 
        force=args.force
    )

