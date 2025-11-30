import paramiko
import time
import argparse
import sys
import json
import os
import yaml

# Путь к конфигу (по умолчанию в корне проекта)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

def load_config(config_path=CONFIG_PATH):
    """Загружает конфиг из YAML, если файл существует. Иначе возвращает пустой dict."""
    if not os.path.exists(config_path):
        print(f"⚠️  Config file not found at {config_path}. Using defaults/CLI only.")
        return {}
    
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        sys.exit(1)

def execute_ssh_command(client, command, dry_run=False, print_output=True):
    if dry_run:
        print(f"[DRY-RUN] Would execute: {command}")
        return "MOCK_OUTPUT"

    print(f"Executing: {command}")
    stdin, stdout, stderr = client.exec_command(command)
    
    out_str = stdout.read().decode().strip()
    err_str = stderr.read().decode().strip()
    exit_status = stdout.channel.recv_exit_status()

    if print_output and out_str:
        print(f"--- Output ({command[:20]}...) ---\n{out_str}\n------------------------------")
    
    if exit_status != 0:
        raise Exception(f"Command failed ({exit_status}): {command}\nError: {err_str}")
    
    return out_str

def get_node_params(node_name, config):
    """
    Возвращает параметры подключения (host, user, key) для ноды.
    Приоритет: Config > Defaults (хардкод на всякий случай)
    """
    nodes = config.get("nodes", {})
    node_conf = nodes.get(node_name)
    
    if not node_conf:
        raise ValueError(f"Unknown node '{node_name}'. Check config.yaml or --node argument.")
    
    return {
        "host": node_conf.get("host"),
        "user": node_conf.get("user", "root"),
        "key": os.path.expanduser(node_conf.get("key_path", "~/.ssh/id_rsa"))
    }

def deploy_vm(template_id, snap_name, new_vm_id, target_node=None, memory=None, dry_run=False, force=False):
    # 1. Загрузка конфига
    config = load_config()
    
    # 2. Определение Node (CLI > Config Default > "r")
    if not target_node:
        target_node = config.get("default_node", "r")
    
    print(f"--- Starting Deploy: Tpl={template_id} Snap={snap_name} NewID={new_vm_id} Node={target_node} ---")

    # 3. Параметры подключения
    try:
        conn_params = get_node_params(target_node, config)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    # 4. Параметры ВМ (CLI > Config > Default)
    if not memory:
        memory = config.get("deploy", {}).get("memory", 8192)

    client = None
    host_ip = conn_params["host"]
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if not dry_run:
            print(f"Connecting to {target_node} ({host_ip})...")
            client.connect(host_ip, username=conn_params["user"], key_filename=conn_params["key"])
        else:
            print(f"[DRY-RUN] Mock connection to {target_node} ({host_ip})")

        # --- Далее логика деплоя без изменений ---
        
        # 0. Проверка существования
        vm_exists = False
        try:
            if not dry_run:
                execute_ssh_command(client, f"qm status {new_vm_id}", print_output=False)
                vm_exists = True
            else:
                vm_exists = True
        except:
            vm_exists = False

        if vm_exists:
            if force:
                print(f"⚠️  VM {new_vm_id} exists. Force -> Destroying...")
                try:
                    execute_ssh_command(client, f"qm stop {new_vm_id} --skiplock", dry_run=dry_run, print_output=False)
                except: pass
            else:
                print(f"❌ VM {new_vm_id} exists!")
                if not dry_run:
                    if input("Destroy? [y/N]: ").lower() != 'y': return
                try:
                    execute_ssh_command(client, f"qm stop {new_vm_id} --skiplock", dry_run=dry_run, print_output=False)
                except: pass

        # 1. Очистка
        setup_cmd = "bash -ic 'source /root/.bashrc && purge_vm_disks && ./ramstor.sh'"
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
        print("Waiting for IP...")
        start_time = time.time()
        vm_ip = None
        while time.time() - start_time < 60:
            if dry_run:
                vm_ip = "10.DRY.RUN.IP"
                break
            try:
                json_out = execute_ssh_command(client, f"qm guest cmd {new_vm_id} network-get-interfaces", print_output=False)
                data = json.loads(json_out)
                for iface in data:
                    if iface.get('name') == 'lo': continue
                    for addr in iface.get('ip-addresses', []):
                        if addr['ip-address-type'] == 'ipv4' and addr['ip-address'].startswith("10."):
                            vm_ip = addr['ip-address']
            except: pass
            if vm_ip: break
            time.sleep(3)
            
        if vm_ip:
             print(f"✅ IP: {vm_ip}")
        else:
             print("⚠️  IP timeout")

        return {"id": new_vm_id, "ip": vm_ip}

    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)
    finally:
        if client: client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tmpl-id", required=True, type=int)
    parser.add_argument("--snap", required=True)
    parser.add_argument("--new-id", required=True, type=int)
    parser.add_argument("--node", help="Target node name (from config.yaml)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")

    args = parser.parse_args()
    
    deploy_vm(
        args.tmpl_id, args.snap, args.new_id, 
        target_node=args.node, 
        dry_run=args.dry_run, 
        force=args.force
    )

