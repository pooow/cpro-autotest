import paramiko
import time
import argparse
import sys

# Настройки (лучше потом в конфиг/env)
PROXMOX_HOST = "10.33.33.15" 
PROXMOX_USER = "root"
SSH_KEY = "/home/bk/.ssh/id_ed25519_vm101" # Убедись, что путь верный

def execute_ssh_command(client, command, dry_run=False):
    """
    Если dry_run=True, просто печатает команду.
    Иначе - выполняет.
    """
    if dry_run:
        print(f"[DRY-RUN] Would execute: {command}")
        return "MOCK_OUTPUT" # Возвращаем заглушку, чтобы код шел дальше

    print(f"Executing: {command}")
    stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    
    out_str = stdout.read().decode().strip()
    err_str = stderr.read().decode().strip()
    
    if exit_status != 0:
        raise Exception(f"Command failed ({exit_status}): {command}\nError: {err_str}")
    
    return out_str

def deploy_vm(template_id, snap_name, new_vm_id, target_node="r", memory=8192, dry_run=False):
    print(f"--- Starting Deploy: Tpl={template_id} Snap={snap_name} NewID={new_vm_id} Node={target_node} ---")
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # Выбор IP хоста (пока простая логика)
    host_ip = PROXMOX_HOST 
    
    try:
        if not dry_run:
            print(f"Connecting to {host_ip}...")
            client.connect(host_ip, username=PROXMOX_USER, key_filename=SSH_KEY)
        else:
            print(f"[DRY-RUN] Mock connection to {host_ip}")

        # 1. Очистка и подготовка (используем bash с подгрузкой .bashrc)
        setup_cmd = (
            f"bash -ic 'source /root/.bashrc && "
            f"purge_vm_disks && "
            f"./ramstor.sh'"
        )
        execute_ssh_command(client, setup_cmd, dry_run)
        
        # 2. Клонирование
        clone_cmd = (
            f"qm clone {template_id} {new_vm_id} "
            f"--snapname {snap_name} --storage ram && "
            f"qm set {new_vm_id} --cpu host --agent 1 --memory {memory}"
        )
        execute_ssh_command(client, clone_cmd, dry_run)
        
        # 3. Старт
        execute_ssh_command(client, f"qm start {new_vm_id}", dry_run)
        
        # 4. Ожидание IP (упрощенно для старта)
        if dry_run:
            print("[DRY-RUN] Would wait for IP and parse JSON")
            return {"id": new_vm_id, "ip": "10.33.33.XXX (DRY_RUN)"}

        # (Тут будет реальный цикл ожидания IP, добавим позже)
        print("VM started (IP check skipped in this basic version)")
        return {"id": new_vm_id, "ip": "UNKNOWN"}

    finally:
        client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy VM on Proxmox")
    parser.add_argument("--tmpl-id", required=True, type=int, help="Template VM ID")
    parser.add_argument("--snap", required=True, help="Snapshot name")
    parser.add_argument("--new-id", required=True, type=int, help="New VM ID")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without running commands")

    args = parser.parse_args()
    
    deploy_vm(args.tmpl_id, args.snap, args.new_id, dry_run=args.dry_run)

