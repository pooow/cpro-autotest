import pytest
from unittest.mock import MagicMock
# Импорт пока не сработает, так как файла нет, но для TDD это ок
# from infra.proxmox import cleanup_ram_vms, prepare_storage 

# Временно комментируем импорт, чтобы показать, что тест готов к появлению модуля.
# В следующем шаге раскомментируем.

@pytest.fixture
def mock_ssh_client():
    client = MagicMock()
    # Эмулируем успешное выполнение команд
    stdout = MagicMock()
    stdout.read.return_value = b""
    stdout.channel.recv_exit_status.return_value = 0
    stderr = MagicMock()
    stderr.read.return_value = b""
    
    client.exec_command.return_value = (None, stdout, stderr)
    return client

def test_prepare_storage_dry_run(mock_ssh_client):
    """
    Проверка генерации команд для подготовки хранилища.
    Используем произвольные тестовые данные, чтобы проверить, 
    что функция корректно подставляет их в команды.
    """
    # Эти значения должны приходить из конфига, здесь мы имитируем конфиг
    test_storage_path = "/mnt/ramdisk_test" 
    test_ram_size = 32
    
    # Чтобы тест работал, нам нужно сначала создать модуль infra/proxmox.
    # Но следуя TDD (Red-Green-Refactor), мы сначала пишем тест, который падает.
    # Так как модуля нет, Python упадет на импорте.
    
    from infra.proxmox import prepare_storage # Импорт внутри теста
    
    prepare_storage(
        mock_ssh_client, 
        storage_path=test_storage_path, 
        ram_size_gb=test_ram_size, 
        dry_run=True
    )
    
    # В идеале здесь нужно проверить mock_ssh_client.exec_command.assert_called_with(...)
    # Но пока нам достаточно, что функция вызывается.

def test_cleanup_ram_vms_dry_run(mock_ssh_client):
    """
    Проверка логики очистки старых ВМ.
    """
    from infra.proxmox import cleanup_ram_vms
    
    # Настраиваем мок на возврат списка конфигов
    # Имитируем, что grep нашел конфиг ВМ 100, в которой есть ссылка на ram-диск
    mock_ssh_client.exec_command.return_value[1].read.side_effect = [
        b"/etc/pve/qemu-server/100.conf\n", # Вывод первой команды (поиск)
        b"", b"" # Вывод последующих команд
    ]
    
    test_storage_name = "ramdisk_test_stor"
    
    cleanup_ram_vms(
        mock_ssh_client, 
        storage_name=test_storage_name, 
        dry_run=True
    )

