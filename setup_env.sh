#!/bin/bash

# 1. Создаем venv, если его нет
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

# 2. Активируем и обновляем pip
source .venv/bin/activate
echo "Upgrading pip..."
pip install --upgrade pip

# 3. Устанавливаем зависимости
if [ -f "requirements.txt" ]; then
    echo "Installing requirements..."
    pip install -r requirements.txt
else
    echo "requirements.txt not found!"
fi

echo "Setup complete. Activate with: source .venv/bin/activate"

