import pytest
from unittest.mock import MagicMock
# В тесте мы уже можем импортировать реальную функцию, 
# так как infra/proxmox.py будет создан до запуска теста
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
    # size нет -> unsafe (хотя странный конфиг, но проверка строгая)
    assert check_vm_safety("100", config2, "ram") is False

def test_check_vm_safety_no_disks():
    """ВМ без дисков (только CDROM) не должна удаляться"""
    config = """
ide2: local:iso/image.iso,media=cdrom
memory: 1024
    """
    assert check_vm_safety("100", config, "ram") is False

def test_prepare_storage_dry_run(mock_ssh_client):
    """Проверка вызова prepare_storage (dry-run)"""
    prepare_storage(
        mock_ssh_client, 
        storage_path="/mnt/ram_test", 
        ram_size_gb=16, 
        dry_run=True
    )
    # Проверяем, что мок вызывался (достаточно для интеграционного теста)
    assert mock_ssh_client.exec_command.called

def test_cleanup_ram_vms_dry_run(mock_ssh_client):
    """Проверка вызова cleanup_ram_vms (dry-run)"""
    # Эмулируем, что ls нашел конфиг
    mock_ssh_client.exec_command.return_value[1].read.side_effect = [
        b"/etc/pve/qemu-server/100.conf\n", # ls
        # cat конфига (безопасный)
        b"scsi0: ram:100/vm-100-disk-0.qcow2,size=10G\n", 
        b"", b"" # для stop/destroy
    ]
    
    cleanup_ram_vms(mock_ssh_client, storage_name="ram", dry_run=True)
    
    # Проверяем, что были попытки destroy (так как конфиг безопасный)
    # Можно проверить аргументы вызова, но для начала хватит факта вызова
    assert mock_ssh_client.exec_command.call_count >= 2

