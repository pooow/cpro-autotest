# CryptoPro5 Automation

Автоматизация тестирования КриптоПро CSP 5.0 по методике (CryptoPro5.pdf).
Цель: полная автоматизация CLI и GUI сценариев в среде Proxmox VM (ALT Linux).

## Структура
- `cli_tests/` — скрипты для проверки консольных утилит (csptest, certmgr).
- `gui_tests/` — сценарии для GUI (dogtail, xdotool).
- `infra/` — скрипты управления Proxmox (создание ВМ, снапшоты).
- `docs/` — документация и заметки.

## Требования
- Python 3.9+
- Proxmox VE (доступ по SSH/API)
- ALT Linux (Sisyphus/p11) в качестве целевой ВМ

