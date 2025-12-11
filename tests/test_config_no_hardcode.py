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
from unittest.mock import patch
from infra.config import load_config, get_node_params


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
        mock_config = {
            "nodes": {
                "test_node": {
                    "host": "10.0.0.1",
                    "user": "root",
                    "key_path": "~/.ssh/id_rsa",
                    "storage": "ram",
                    "storage_path": "/custom/path/stor"
                }
            }
        }

        with patch('infra.config.load_config', return_value=mock_config):
            params = get_node_params("test_node", mock_config)
            assert params["storage_path"] == "/custom/path/stor", \
                "storage_path должен читаться из конфига узла"

    def test_storage_path_missing_raises_error(self):
        """
        Проверяем, что отсутствие storage_path в конфиге узла вызывает ошибку.
        """
        mock_config = {
            "nodes": {
                "test_node": {
                    "host": "10.0.0.1",
                    "user": "root",
                    "key_path": "~/.ssh/id_rsa",
                    "storage": "ram"
                    # storage_path отсутствует!
                }
            }
        }

        with patch('infra.config.load_config', return_value=mock_config):
            with pytest.raises(KeyError, match="storage_path"):
                params = get_node_params("test_node", mock_config)
                # Принудительно обращаемся к ключу
                _ = params["storage_path"]

    def test_ram_disk_size_from_deploy_config(self):
        """
        Проверяем, что ram_disk_size_gb читается из секции deploy в config.yaml.
        """
        mock_config = {
            "deploy": {
                "ram_disk_size_gb": 50
            }
        }

        with patch('infra.config.load_config', return_value=mock_config):
            config = load_config()
            ram_size = config.get("deploy", {}).get("ram_disk_size_gb")
            assert ram_size == 50, \
                "ram_disk_size_gb должен читаться из секции deploy"

    def test_ram_disk_size_missing_returns_none(self):
        """
        Проверяем, что при отсутствии ram_disk_size_gb в конфиге возвращается None.
        Дефолт не должен быть захардкожен в коде!
        """
        mock_config = {
            "deploy": {}
        }

        with patch('infra.config.load_config', return_value=mock_config):
            config = load_config()
            ram_size = config.get("deploy", {}).get("ram_disk_size_gb")
            assert ram_size is None, \
                "При отсутствии ram_disk_size_gb должен возвращаться None (без хардкода)"

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
        
        Обязательные секции:
        - deploy.ram_disk_size_gb
        - nodes.<каждый узел>.storage_path
        """
        config = load_config()
        
        # Проверка deploy.ram_disk_size_gb
        assert "deploy" in config, "Отсутствует секция 'deploy' в config.yaml"
        assert "ram_disk_size_gb" in config["deploy"], \
            "Отсутствует параметр 'ram_disk_size_gb' в секции deploy"
        
        # Проверка storage_path для каждого узла
        assert "nodes" in config, "Отсутствует секция 'nodes' в config.yaml"
        
        for node_name, node_conf in config["nodes"].items():
            assert "storage_path" in node_conf, \
                f"Узел '{node_name}' не содержит обязательный параметр 'storage_path'"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

