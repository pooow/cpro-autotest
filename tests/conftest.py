import pytest
import os
import sys

# Добавляем корень проекта в sys.path, чтобы видеть infra
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.config import get_node_params

@pytest.fixture
def target_node():
    """
    Имя ноды, на которой запускаем тесты.
    Можно переопределить через env var: TEST_NODE=pve9 pytest ...
    По умолчанию берет 'r' (или то, что в конфиге дефолтное, но здесь пока явно 'r').
    """
    return os.getenv("TEST_NODE", "r")

@pytest.fixture
def ssh_config(target_node):
    """
    Возвращает параметры подключения (host, user, key) для выбранной ноды.
    Читает их из config.yaml через infra.config.
    """
    try:
        params = get_node_params(target_node)
        return {
            "host": params["host"],
            "user": params["user"],
            "sshkey": params["key"]
        }
    except ValueError as e:
        pytest.fail(f"Invalid configuration for node '{target_node}': {e}")


