import paramiko
import pytest

def test_ssh_echo(ssh_config):
    """Проверка, что мы можем подключиться и выполнить простую команду"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Попытка подключения
        client.connect(
            hostname=ssh_config['host'],
            username=ssh_config['user'],
            key_filename=ssh_config['sshkey'] 
        )
        
        # Выполнение команды
        stdin, stdout, stderr = client.exec_command('echo "Hello AutoTest"')
        output = stdout.read().decode().strip()
        
        assert output == "Hello AutoTest"
        
    finally:
        client.close()

