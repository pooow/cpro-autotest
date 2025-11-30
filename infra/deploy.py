import paramiko
import time
import argparse
import sys
import json

# Настройки (лучше потом в конфиг/env)
PROXMOX_HOST = "10.33.33.15" 
PROXMOX_USER = "root"
SSH_KEY = "/home/bk/.ssh/id_ed25519_vm101" # Убедись, что путь верный

def execute_ssh_command(client, command, dry_run=False, print_output=True):
    """
    Выполняет SSH команду.
    dry_run: если True, только печатает команду.
    print_output: если True, печатает stdout команды в консоль.
    """
    if dry_run:
        print(f"[DRY-RUN] Would execute: {command}")
        return "MOCK_OUTPUT"

    print(f"Executing: {command}")
    stdin, stdout, stderr = client.exec_command(command)
    
    # Читаем вывод
    out_str = stdout.read().decode().strip()
    err_str = stderr.read().decode().strip()
    exit_status = stdout.channel.recv_exit_status()

    if print_output and out_str:
        print(f"--- Output ({command[:20]}...) ---\n{out_str}\n------------------------------")
    
    if exit_status != 0:
        # Можно добавить логику игнорирования ошибок для status команд
        raise Exception(f"Command failed ({exit_status}): {command}\nError: {err_str}")
    
    return out_str

def deploy_vm(template_id, snap_name, new_vm_id, target_node="r", memory=8192, dry_run=False, force=False):
    print(f"--- Starting Deploy: Tpl={template_id} Snap={snap_name} NewID={new_vm_id} Node={target_node} ---")
    
    client = None
    host_ip = PROXMOX_HOST 
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if not dry_run:
            print(f"Connecting to {host_ip}...")
            client.connect(host_ip, username=PROXMOX_USER, key_filename=SSH_KEY)
        else:
            print(f"[DRY-RUN] Mock connection to {host_ip}")

        # 0. Проверка: существует ли уже ВМ?
        vm_exists = False
        check_cmd = f"qm status {new_vm_id}"
        try:
            if not dry_run:
                execute_ssh_command(client, check_cmd, print_output=False)
                vm_exists = True
            else:
                vm_exists = True # Симулируем существование для теста
        except:
            vm_exists = False # qm status вернул ошибку -> ВМ нет

        if vm_exists:
            if force:
                print(f"⚠️  VM {new_vm_id} exists. Force flag set -> Destroying...")
                # Останавливаем и даем команду на уничтожение
                # purge_vm_disks позже почистит диски, но лучше явно остановить qm
                stop_cmd = f"qm stop {new_vm_id} --skiplock"
                try:
                    execute_ssh_command(client, stop_cmd, dry_run=dry_run, print_output=False)
                except:
                    pass # Если уже остановлена
            else:
                print(f"❌ VM {new_vm_id} already exists!")
                if not dry_run:
                    user_input = input("Destroy and reinstall? [y/N]: ")
                    if user_input.lower() != 'y':
                        print("Aborting.")
                        return
                print("Stopping (force reinstall approved)...")
                try:
                    execute_ssh_command(client, f"qm stop {new_vm_id} --skiplock", dry_run=dry_run, print_output=False)
                except:
                    pass

        # 1. Очистка и подготовка (purge_vm_disks + ramstor)
        # Важно: source .bashrc чтобы функции были доступны
        setup_cmd = (
            f"bash -ic 'source /root/.bashrc && "
            f"purge_vm_disks && "
            f"./ramstor.sh'"
        )
        execute_ssh_command(client, setup_cmd, dry_run, print_output=True)
        
        # 2. Клонирование
        clone_cmd = (
            f"qm clone {template_id} {new_vm_id} "
            f"--snapname {snap_name} --storage ram && "
            f"qm set {new_vm_id} --cpu host --agent 1 --memory {memory}"
        )
        execute_ssh_command(client, clone_cmd, dry_run)
        
        # 3. Старт
        execute_ssh_command(client, f"qm start {new_vm_id}", dry_run)
        
        # 4. Ожидание IP
        print("Waiting for IP (max 60s)...")
        start_time = time.time()
        vm_ip = None
        
        while time.time() - start_time < 60:
            if dry_run:
                print("[DRY-RUN] Checking IP via qm agent...")
                vm_ip = "10.33.33.DRY"
                break

            try:
                cmd = f"qm guest cmd {new_vm_id} network-get-interfaces"
                # Получаем JSON напрямую (без execute_ssh_command, т.к. нам нужно обработать exit code вручную)
                # Но execute_ssh_command выбрасывает исключение, если код != 0.
                # А qm guest cmd возвращает не 0, если агент не готов.
                # Поэтому используем try-except вокруг execute_ssh_command
                
                # ВАЖНО: execute_ssh_command вернет строку JSON
                json_out = execute_ssh_command(client, cmd, print_output=False)
                data = json.loads(json_out)
                
                for iface in data:
                    if iface.get('name') == 'lo': continue
                    for addr in iface.get('ip-addresses', []):
                        if addr['ip-address-type'] == 'ipv4':
                            ip = addr['ip-address']
                            if ip.startswith("10."):
                                vm_ip = ip
                                break
                    if vm_ip: break
            except Exception:
                # Либо SSH сбой, либо (чаще) агент еще не ответил (код возврата != 0)
                pass
            
            if vm_ip:
                print(f"✅ VM IP Found: {vm_ip}")
                break
            
            time.sleep(3)
            
        if not vm_ip:
             print("⚠️  Warning: IP not found (timeout). Check Guest Agent.")
             
        return {"id": new_vm_id, "ip": vm_ip}

    except Exception as e:
        print(f"❌ Deploy failed: {e}")
        sys.exit(1) # Выход с ошибкой для CI/CD
        
    finally:
        if client:
            client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy VM on Proxmox")
    parser.add_argument("--tmpl-id", required=True, type=int, help="Template VM ID")
    parser.add_argument("--snap", required=True, help="Snapshot name")
    parser.add_argument("--new-id", required=True, type=int, help="New VM ID")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without running commands")
    parser.add_argument("--force", action="store_true", help="Force destroy existing VM if conflict")

    args = parser.parse_args()
    
    deploy_vm(args.tmpl_id, args.snap, args.new_id, dry_run=args.dry_run, force=args.force)

