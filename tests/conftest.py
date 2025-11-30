import pytest
import os

# Фикстура для данных подключения (заглушка, потом заменим на .env)
@pytest.fixture
def ssh_config():
    return {
        "host": os.getenv("TEST_HOST", "10.33.33.15"), # Замени на IP своей ВМ/Proxmox
        "user": os.getenv("TEST_USER", "root"),          # Замени на юзера
        "sshkey": os.path.expanduser(
            os.getenv("TEST_SSHKEY", "~/.ssh/id_ed25519_vm101")
        )
    }

