"""
Модуль для работы с конфигурацией проекта.
Обеспечивает загрузку config.yaml и доступ к параметрам узлов.
"""
import os
import yaml
import sys
import logging

# Настройка логгера (можно будет расширить)
logger = logging.getLogger("config")

# Пути
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

def load_config(config_path=CONFIG_PATH):
    """
    Загружает настройки из YAML файла.
    :return: Словарь с конфигурацией.
    """
    if not os.path.exists(config_path):
        logger.warning(f"Config file not found at {config_path}. Using empty config.")
        return {}
    
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return {}

def get_node_params(node_name, config=None):
    """
    Возвращает параметры подключения для узла.
    Если config не передан, загружает его заново.
    """
    if config is None:
        config = load_config()

    nodes = config.get("nodes", {})
    node_conf = nodes.get(node_name)
    
    if not node_conf:
        raise ValueError(f"Node '{node_name}' not found in config")
    
    return {
        "host": node_conf.get("host"),
        "user": node_conf.get("user", "root"),
        "key": os.path.expanduser(node_conf.get("key_path", "~/.ssh/id_rsa")),
        "storage": node_conf.get("storage", "ram"),
        # Добавлено для поддержки prepare_storage
        "storage_path": node_conf.get("storage_path", "/mnt/ramdisk_stor") 
    }

