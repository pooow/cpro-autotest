"""
Модуль для работы с конфигурацией проекта.
Обеспечивает загрузку config.yaml и доступ к параметрам узлов.

Правило: Все настройки должны быть явно указаны в config.yaml.
Хардкод дефолтов запрещен согласно AI_WORKFLOW.md.
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
    
    Все параметры должны быть явно указаны в config.yaml!
    Отсутствие обязательных параметров приводит к ValueError.
    
    :param node_name: Имя узла из config.yaml (например, 'r' или 'pve9')
    :param config: Опциональный словарь конфигурации
    :return: Словарь с параметрами узла
    :raises ValueError: Если узел не найден или отсутствуют обязательные параметры
    """
    if config is None:
        config = load_config()

    nodes = config.get("nodes", {})
    node_conf = nodes.get(node_name)
    
    if not node_conf:
        raise ValueError(
            f"Node '{node_name}' not found in config.yaml. "
            f"Available nodes: {list(nodes.keys())}"
        )
    
    # Проверяем обязательные параметры
    required_params = ["host", "user", "key_path", "storage", "storage_path"]
    missing = [p for p in required_params if p not in node_conf]
    
    if missing:
        raise ValueError(
            f"Node '{node_name}' missing required parameters: {missing}. "
            f"Please add them to config.yaml"
        )
    
    return {
        "host": node_conf["host"],
        "user": node_conf["user"],
        "key": os.path.expanduser(node_conf["key_path"]),
        "storage": node_conf["storage"],
        "storage_path": node_conf["storage_path"],
        # ram_disk_size_gb теперь опциональный (специфичен для узла)
        # Если не указан - возвращаем None (tmpfs использует дефолт 50% RAM)
        "ram_disk_size_gb": node_conf.get("ram_disk_size_gb")
    }

