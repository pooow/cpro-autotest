import pytest
from unittest.mock import MagicMock, patch
from infra.deploy import deploy_vm

# Патчим input, чтобы тест не вис на вопросе "Destroy VM?"
@patch("builtins.input", return_value="y")
@patch("infra.deploy.load_config")
@patch("infra.deploy.get_node_params")
@patch("infra.deploy.paramiko.SSHClient")
@patch("infra.deploy.execute_ssh_command")
@patch("infra.deploy.wait_for_ip")
# Патчим prepare_storage, чтобы проверить факт его вызова
@patch("infra.deploy.prepare_storage") 
def test_deploy_vm_calls_prepare_storage(
    mock_prepare, mock_wait, mock_exec, mock_ssh_cls, mock_get_node, mock_load, mock_input
):
    """
    Проверяем, что deploy_vm вызывает функцию подготовки хранилища (prepare_storage).
    Это гарантирует, что мы избавились от bash-скриптов.
    """
    # 1. Настройка моков
    mock_load.return_value = {"deploy": {"memory": 2048, "ram_disk_size_gb": 42}, "logging": {"level": "DEBUG"}}
    
    mock_get_node.return_value = {
        "host": "1.2.3.4", 
        "user": "root", 
        "key": "key", 
        "storage": "ram",
        "storage_path": "/mnt/ram_test"
    }
    
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client
    
    # Имитируем успешное получение IP
    mock_wait.return_value = "10.0.0.1"

    # 2. Запуск функции
    # dry_run=False нужен, чтобы дойти до реальной логики вызовов
    res = deploy_vm(
        template_id=100,
        snap_name="snap1",
        new_vm_id=200,
        target_node="test_node",
        dry_run=False,
        force=False # Вызовет input, который мы замокали
    )

    # 3. Проверки (Assertions)
    
    # Проверка 1: prepare_storage вызвана 1 раз
    mock_prepare.assert_called_once()
    
    # Проверка 2: переданы правильные аргументы (из конфига)
    _, kwargs = mock_prepare.call_args
    assert kwargs['storage_path'] == "/mnt/ram_test"
    assert kwargs['ram_size_gb'] == 42 # из конфига (mock_load)

    # Проверка 3: клонирование запущено
    clone_found = False
    for call in mock_exec.call_args_list:
        cmd = call[0][1]
        if "qm clone 100 200" in cmd:
            clone_found = True
            break
    assert clone_found, "Команда qm clone не была вызвана!"

    # Проверка 4: результат функции
    assert res["id"] == 200
    assert res["ip"] == "10.0.0.1"

