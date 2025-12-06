"""
Модуль для управления Proxmox (Storage, VMs) через SSH.
Заменяет старые Bash-скрипты (ramstor.sh, purge_vm_disks).
"""

import logging
from infra.ssh_utils import execute_ssh_command

logger = logging.getLogger("proxmox")

def parse_vm_config(config_text: str) -> dict:
    """
    Парсит конфиг ВМ Proxmox в словарь.
    Возвращает словарь {ключ: значение}, где ключи - scsi0, ide2, memory и т.д.
    """
    config = {}
    for line in config_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            config[key.strip()] = value.strip()
    return config

def is_disk_key(key: str) -> bool:
    """Проверяет, является ли ключ диском (scsi, ide, sata, virtio, efidisk)."""
    return any(key.startswith(prefix) for prefix in ["scsi", "ide", "sata", "virtio", "efidisk"])

def is_cdrom(value: str) -> bool:
    """Проверяет, является ли значение CD-ROM/ISO."""
    return "media=cdrom" in value or ".iso" in value

def check_vm_safety(vm_id: str, config_text: str, target_storage: str) -> bool:
    """
    Проверяет, можно ли безопасно удалить ВМ.
    Критерий: 
    1. Найдена хотя бы один диск.
    2. ВСЕ жесткие диски (не ISO) должны быть на target_storage.
    3. Значение строки диска должно содержать 'disk', 'size' и 'qcow2' (строгий паттерн).
    """
    config = parse_vm_config(config_text)
    disk_found = False
    all_disks_safe = True
    
    for key, value in config.items():
        if is_disk_key(key):
            if is_cdrom(value):
                continue
            
            disk_found = True
            
            # 1. Проверка хранилища (ram:...)
            if not value.startswith(f"{target_storage}:"):
                logger.debug(f"VM {vm_id} skipped: Disk '{key}' is on another storage.")
                all_disks_safe = False
                break
            
            # 2. Строгая проверка паттерна (disk + size + qcow2)
            if not ("disk" in value and "size" in value and "qcow2" in value):
                 logger.warning(f"VM {vm_id} skipped: Disk '{key}' matches storage but not strict pattern (disk+size+qcow2).")
                 all_disks_safe = False
                 break
                 
    return disk_found and all_disks_safe


def cleanup_ram_vms(
    client, 
    storage_name: str = "ram", 
    dry_run: bool = False
) -> None:
    """
    Ищет и уничтожает ВМ, ВСЕ диски которых находятся в указанном RAM-хранилище
    и соответствуют строгому паттерну безопасности.
    """
    logger.warning(f"🔥 Scanning for VMs fully on storage '{storage_name}' to purge...")
    
    try:
        files_out = execute_ssh_command(
            client, "ls /etc/pve/qemu-server/*.conf", dry_run=dry_run, print_output=False, ignore_errors=True
        )
    except Exception:
        files_out = ""

    if not files_out:
        logger.info("No VM configs found.")
        return

    config_files = files_out.strip().splitlines()
    
    for conf_path in config_files:
        try:
            vm_id = conf_path.split("/")[-1].replace(".conf", "")
            if not vm_id.isdigit():
                continue
            
            config_text = execute_ssh_command(client, f"cat {conf_path}", dry_run=dry_run, print_output=False, log_command=False)
            
            if not config_text:
                continue

            if check_vm_safety(vm_id, config_text, storage_name):
                logger.warning(f"⚠️  VM {vm_id} is fully on {storage_name} and matches safety pattern. Destroying...")
                execute_ssh_command(client, f"qm stop {vm_id} --skiplock", dry_run=dry_run, ignore_errors=True, log_command=True)
                execute_ssh_command(client, f"qm destroy {vm_id} --skiplock --purge", dry_run=dry_run, log_command=True)
            
        except Exception as e:
            logger.error(f"Failed to analyze/purge VM {conf_path}: {e}")


def prepare_storage(
    client, 
    storage_path: str, 
    ram_size_gb: int = 32, 
    dry_run: bool = False,
    force_remount: bool = False
) -> None:
    """
    Подготавливает tmpfs хранилище.
    """
    logger.info(f"💾 Checking RAM storage at {storage_path}...")

    is_mounted = False
    if not dry_run:
        check_cmd = f"mount | grep ' {storage_path} ' || true"
        out = execute_ssh_command(client, check_cmd, print_output=False, log_command=False)
        if out.strip():
            is_mounted = True
    else:
        is_mounted = False

    if is_mounted and not force_remount:
        logger.info(f"✅ Storage {storage_path} is already mounted. Skipping remount (safe).")
        execute_ssh_command(client, f"mkdir -p {storage_path}/{{images,snippets,iso,dump}}", dry_run=dry_run, log_command=False)
        return

    if force_remount:
        logger.warning(f"♻️  Force remount requested! Data in {storage_path} will be lost.")
        execute_ssh_command(client, f"umount {storage_path} || true", dry_run=dry_run)

    logger.info(f"Mounting tmpfs ({ram_size_gb}GB) -> {storage_path}")
    execute_ssh_command(client, f"mkdir -p {storage_path}", dry_run=dry_run)
    execute_ssh_command(client, f"mount -t tmpfs -o size={ram_size_gb}G tmpfs {storage_path}", dry_run=dry_run)
    execute_ssh_command(client, f"mkdir -p {storage_path}/{{images,snippets,iso,dump}}", dry_run=dry_run)
    
    logger.info("✅ RAM storage ready.")

