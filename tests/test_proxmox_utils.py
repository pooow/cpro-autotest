import pytest
from unittest.mock import MagicMock, patch
from infra.proxmox import check_vm_safety, prepare_storage, cleanup_ram_vms

@pytest.fixture
def mock_ssh_client():
    client = MagicMock()
    stdout = MagicMock()
    stdout.read.return_value = b""
    stdout.channel.recv_exit_status.return_value = 0
    stderr = MagicMock()
    stderr.read.return_value = b""
    client.exec_command.return_value = (None, stdout, stderr)
    return client

# Тесты безопасности (Unit tests)

def test_check_vm_safety_safe_vm():
    """Тест безопасной ВМ (все диски в RAM и правильного формата)"""
    config = """
scsi0: ram:100/vm-100-disk-0.qcow2,size=32G,ssd=1
efidisk0: ram:100/vm-100-disk-1.qcow2,size=128K
ide2: local:iso/image.iso,media=cdrom
    """
    assert check_vm_safety("100", config, "ram") is True

def test_check_vm_safety_unsafe_mixed_storage():
    """Тест опасной ВМ (один диск на local-lvm)"""
    config = """
scsi0: ram:100/vm-100-disk-0.qcow2,size=32G
scsi1: local-lvm:vm-100-disk-1.raw,size=10G
    """
    assert check_vm_safety("100", config, "ram") is False

def test_check_vm_safety_unsafe_pattern():
    """Тест опасной ВМ (диск на RAM, но не matches паттерн disk+size+qcow2)"""
    config = """
scsi0: ram:100/vm-100-root.raw,size=32G
    """
    # qcow2 нет -> unsafe
    assert check_vm_safety("100", config, "ram") is False
    
    config2 = """
scsi0: ram:100/vm-100-disk-0.qcow2
    """
    # size нет -> unsafe (строгая проверка)
    assert check_vm_safety("100", config2, "ram") is False

def test_check_vm_safety_no_disks():
    """ВМ без дисков (только CDROM) не должна удаляться"""
    config = """
ide2: local:iso/image.iso,media=cdrom
memory: 1024
    """
    assert check_vm_safety("100", config, "ram") is False


# Интеграционные тесты (с патчем execute_ssh_command)

@patch("infra.proxmox.execute_ssh_command")
def test_prepare_storage_dry_run(mock_execute, mock_ssh_client):
    """
    Проверка вызова prepare_storage (dry-run).
    В dry-run проверка (grep) пропускается, сразу идут команды монтирования.
    """
    mock_execute.return_value = "" 

    prepare_storage(
        mock_ssh_client, 
        storage_path="/mnt/ram_test", 
        ram_size_gb=16, 
        dry_run=True
    )
    
    # Проверяем, что execute_ssh_command вызывался
    assert mock_execute.called
    
    # Проверяем конкретные команды (в dry-run мы сразу монтируем)
    calls = [args[0][1] for args in mock_execute.call_args_list]
    
    # mkdir для точки монтирования
    assert "mkdir -p /mnt/ram_test" in calls
    # Само монтирование
    assert "mount -t tmpfs -o size=16G tmpfs /mnt/ram_test" in calls
    # Создание структуры папок
    assert any("mkdir -p /mnt/ram_test/{images" in c for c in calls)

@patch("infra.proxmox.execute_ssh_command")
def test_cleanup_ram_vms_dry_run(mock_execute, mock_ssh_client):
    """
    Проверка логики очистки.
    """
    # Настраиваем side_effect для имитации ответов команд
    mock_execute.side_effect = [
        "/etc/pve/qemu-server/100.conf\n", # 1. ls
        "scsi0: ram:100/vm-100-disk-0.qcow2,size=10G\n", # 2. cat 100.conf (SAFE)
        "", # 3. stop (успех)
        "", # 4. destroy (успех)
    ]
    
    cleanup_ram_vms(mock_ssh_client, storage_name="ram", dry_run=True)
    
    # Проверяем, что были вызовы stop и destroy
    calls = [args[0][1] for args in mock_execute.call_args_list]
    assert "qm stop 100 --skiplock" in calls
    assert "qm destroy 100 --skiplock --purge" in calls

