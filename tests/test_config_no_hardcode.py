#!/usr/bin/env python3
"""
Тест для проверки отсутствия хардкода настроек в проекте.

Проверяет:
1. Чтение storage_path и ram_disk_size_gb из config.yaml
2. Отсутствие дефолтных значений во ВСЕХ Python-модулях проекта
3. Ошибку при отсутствии обязательных параметров

Принцип: Единственный источник правды (Single Source of Truth) - это config.yaml.
Легальных дефолтов в коде быть не должно.

Автор: pooow (с помощью AI)
Дата: Декабрь 2025
"""
import pytest
import os
import ast
from pathlib import Path
from unittest.mock import patch, mock_open


# Список конфигурационных ключей, для которых запрещены дефолты в коде
FORBIDDEN_CONFIG_KEYS_WITH_DEFAULTS = [
    "storage_path",
    "ram_disk_size_gb",
    "host",
    "user",
    "key_path",
    "storage",
    # TODO: Добавлять сюда новые ключи по мере развития проекта
]


class TestConfigNoHardcode:
    """
    Тесты для проверки правила "Хардкод запрещен" из AI_WORKFLOW.md
    """

    def test_storage_path_from_config(self):
        """
        Проверяем, что storage_path читается из config.yaml для узла.
        """
        from infra.config import get_node_params
        
        mock_config = {
            "nodes": {
                "test_node": {
                    "host": "10.0.0.1",
                    "user": "root",
                    "key_path": "~/.ssh/id_rsa",
                    "storage": "ram",
                    "storage_path": "/custom/path/stor",
                    "ram_disk_size_gb": 32
                }
            }
        }

        params = get_node_params("test_node", mock_config)
        assert params["storage_path"] == "/custom/path/stor", \
            "storage_path должен читаться из конфига узла"

    def test_storage_path_missing_raises_error(self):
        """
        Проверяем, что отсутствие storage_path в конфиге узла вызывает ValueError.
        """
        from infra.config import get_node_params
        
        mock_config = {
            "nodes": {
                "test_node": {
                    "host": "10.0.0.1",
                    "user": "root",
                    "key_path": "~/.ssh/id_rsa",
                    "storage": "ram",
                    "ram_disk_size_gb": 32
                    # storage_path отсутствует!
                }
            }
        }

        with pytest.raises(ValueError, match="storage_path"):
            get_node_params("test_node", mock_config)

    def test_ram_disk_size_from_node_config(self):
        """
        НОВЫЙ ТЕСТ: Проверяем, что ram_disk_size_gb читается из nodes.<node>.
        
        ram_disk_size_gb теперь специфичен для узла (разное количество RAM),
        а не глобальный параметр в deploy.
        """
        from infra.config import get_node_params
        
        mock_config = {
            "nodes": {
                "r": {
                    "host": "10.33.33.15",
                    "user": "root",
                    "key_path": "~/.ssh/id_rsa",
                    "storage": "ram",
                    "storage_path": "/mnt/ramdisk/stor",
                    "ram_disk_size_gb": 32  # 50% от 64 GB RAM
                },
                "pve9": {
                    "host": "10.33.33.2",
                    "user": "root",
                    "key_path": "~/.ssh/id_rsa",
                    "storage": "ramdisk_stor",
                    "storage_path": "/mnt/ramdisk_stor",
                    "ram_disk_size_gb": 64  # 50% от 128 GB RAM
                }
            }
        }

        # Проверяем узел r (64 GB RAM)
        params_r = get_node_params("r", mock_config)
        assert params_r["ram_disk_size_gb"] == 32, \
            "ram_disk_size_gb для узла 'r' должен быть 32 GB"

        # Проверяем узел pve9 (128 GB RAM)
        params_pve9 = get_node_params("pve9", mock_config)
        assert params_pve9["ram_disk_size_gb"] == 64, \
            "ram_disk_size_gb для узла 'pve9' должен быть 64 GB"

    def test_ram_disk_size_missing_returns_none(self):
        """
        Проверяем, что при отсутствии ram_disk_size_gb в конфиге узла возвращается None.
        
        None означает: tmpfs автоматически использует 50% RAM хоста.
        """
        from infra.config import get_node_params
        
        mock_config = {
            "nodes": {
                "test_node": {
                    "host": "10.0.0.1",
                    "user": "root",
                    "key_path": "~/.ssh/id_rsa",
                    "storage": "ram",
                    "storage_path": "/mnt/ramdisk/stor"
                    # ram_disk_size_gb отсутствует!
                }
            }
        }

        params = get_node_params("test_node", mock_config)
        ram_size = params.get("ram_disk_size_gb")
        assert ram_size is None, \
            "При отсутствии ram_disk_size_gb должен возвращаться None (tmpfs auto)"

    def test_deploy_ram_disk_size_not_used(self):
        """
        НОВЫЙ ТЕСТ: Проверяем, что deploy.ram_disk_size_gb больше НЕ используется.
        
        Старая структура (глобальный параметр) удалена.
        Теперь ram_disk_size_gb специфичен для узла.
        """
        import yaml
        
        # Загружаем реальный config.yaml
        from infra.config import CONFIG_PATH
        
        if not os.path.exists(CONFIG_PATH):
            pytest.skip("config.yaml не найден")
        
        with open(CONFIG_PATH, 'r') as f:
            config = yaml.safe_load(f)
        
        # Проверяем, что deploy.ram_disk_size_gb отсутствует
        deploy_section = config.get("deploy", {})
        assert "ram_disk_size_gb" not in deploy_section, \
            "deploy.ram_disk_size_gb должен быть удален! Теперь параметр специфичен для узла."

    def test_no_hardcoded_defaults_in_entire_project(self):
        """
        УНИВЕРСАЛЬНЫЙ ТЕСТ: Проверяем отсутствие хардкода дефолтов
        во ВСЕХ Python-файлах проекта (включая будущие).
        
        Сканирует все .py файлы в infra/, tests/, plugins/ и ищет:
        - .get("запрещенный_ключ", <любой_дефолт>)
        
        Исключения:
        - Файлы в tests/ (сами тесты могут содержать моки)
        - conftest.py (фикстуры pytest)
        
        Легальные дефолты должны быть ТОЛЬКО в config.yaml!
        """
        # Корневая директория проекта
        project_root = Path(__file__).parent.parent
        
        # Директории для сканирования (исключаем tests/)
        scan_dirs = [
            project_root / "infra",
            # project_root / "plugins",  # TODO: раскомментировать когда появится
        ]
        
        violations = []  # Список нарушений
        
        for scan_dir in scan_dirs:
            if not scan_dir.exists():
                continue
                
            # Рекурсивно обходим все .py файлы
            for py_file in scan_dir.rglob("*.py"):
                # Пропускаем __pycache__ и т.п.
                if "__pycache__" in str(py_file):
                    continue
                
                with open(py_file, 'r', encoding='utf-8') as f:
                    try:
                        source = f.read()
                        tree = ast.parse(source)
                    except SyntaxError:
                        # Пропускаем файлы с синтаксическими ошибками
                        continue
                
                # Ищем вызовы .get() с запрещенными ключами и дефолтами
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        # Проверяем, что это вызов метода .get()
                        if (hasattr(node.func, 'attr') and 
                            node.func.attr == 'get' and 
                            len(node.args) >= 1):
                            
                            # Извлекаем имя ключа (первый аргумент)
                            if isinstance(node.args[0], ast.Constant):
                                key_name = node.args[0].value
                                
                                # Проверяем, запрещен ли этот ключ для дефолтов
                                if key_name in FORBIDDEN_CONFIG_KEYS_WITH_DEFAULTS:
                                    # Если есть второй аргумент (дефолт) - нарушение!
                                    if len(node.args) >= 2:
                                        violations.append({
                                            "file": str(py_file.relative_to(project_root)),
                                            "line": node.lineno,
                                            "key": key_name,
                                            "default": ast.unparse(node.args[1])
                                        })
        
        # Формируем подробное сообщение об ошибках
        if violations:
            error_msg = [
                "\n❌ НАЙДЕНЫ ХАРДКОД ДЕФОЛТЫ В КОДЕ (нарушение AI_WORKFLOW.md):",
                "\nЛегальные дефолты должны быть ТОЛЬКО в config.yaml!\n"
            ]
            
            for v in violations:
                error_msg.append(
                    f"  📁 {v['file']}:{v['line']}\n"
                    f"     .get(\"{v['key']}\", {v['default']})  ← ЗАПРЕЩЕНО!\n"
                )
            
            error_msg.append(
                "\n💡 Как исправить:\n"
                "  1. Удалите второй аргумент из .get()\n"
                "  2. Добавьте значение в config.yaml\n"
                "  3. Обработайте случай отсутствия значения (raise ValueError)\n"
            )
            
            pytest.fail("".join(error_msg))

    def test_config_yaml_has_required_keys(self):
        """
        Проверяем, что config.yaml содержит все обязательные ключи.
        
        ОБНОВЛЕНО: ram_disk_size_gb теперь опциональный (для узлов).
        Обязательные параметры:
        - nodes.<каждый узел>.storage_path
        - nodes.<каждый узел>.host, user, key_path, storage
        """
        from infra.config import load_config
        
        config = load_config()
        
        # Проверка наличия секции nodes
        assert "nodes" in config, "Отсутствует секция 'nodes' в config.yaml"
        
        # Проверка обязательных параметров для каждого узла
        required_node_params = ["host", "user", "key_path", "storage", "storage_path"]
        
        for node_name, node_conf in config["nodes"].items():
            for param in required_node_params:
                assert param in node_conf, \
                    f"Узел '{node_name}' не содержит обязательный параметр '{param}'"
            
            # ram_disk_size_gb теперь опциональный (можно не указывать)
            # Если указан - должен быть числом
            if "ram_disk_size_gb" in node_conf:
                assert isinstance(node_conf["ram_disk_size_gb"], (int, float)), \
                    f"Узел '{node_name}': ram_disk_size_gb должен быть числом"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

