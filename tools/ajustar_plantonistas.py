COMPETENCIAS = ["team1", "team2"]  # Substitua pelos nomes reais dos times


import os
import argparse

API_BASE = "https://api.victorops.com/api-public/v1"

def get_env_vars():
    from dotenv import load_dotenv
    load_dotenv()
    api_id = os.getenv("VICTOROPS_API_ID")
    api_key = os.getenv("VICTOROPS_API_KEY")
    org_id = os.getenv("VICTOROPS_ORG_ID")
    if not all([api_id, api_key, org_id]):
        raise Exception("Faltam variáveis de ambiente VICTOROPS_API_ID, VICTOROPS_API_KEY ou VICTOROPS_ORG_ID")
    return api_id, api_key, org_id

def get_headers(api_id, api_key, org_id):
    return {
        "X-VO-Api-Id": api_id,
        "X-VO-Api-Key": api_key,
        "X-VO-Org-Id": org_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# Lista todos os usuários da organização
def get_users(api_id, api_key, org_id):
    url = f"{API_BASE}/user"
    import requests
    resp = requests.get(url, headers=get_headers(api_id, api_key, org_id), verify=False)
    resp.raise_for_status()
    return resp.json().get("users", [])

# Busca escala de plantão de um time
def get_team_oncall_schedule(api_id, api_key, org_id, team):
    url = f"{API_BASE}/team/{team}/oncall/schedule"
    import requests
    resp = requests.get(url, headers=get_headers(api_id, api_key, org_id), verify=False)
    resp.raise_for_status()
    return resp.json()



def main():
    parser = argparse.ArgumentParser(description="Ajusta e exibe plantonistas VictorOps por competência.")
    parser.add_argument("--ajustar", action="store_true", help="Ajusta automaticamente o VictorOps para refletir a planilha")
    parser.add_argument("--dry-run", action="store_true", help="Simula o ajuste sem alterar o VictorOps (Splunk)")
    args = parser.parse_args()

    api_id, api_key, org_id = get_env_vars()

    # spreadsheet_id, credentials_path = get_planilha_env()

    # Exemplo: listar usuários
    print("Usuários VictorOps:")
    try:
        users = get_users(api_id, api_key, org_id)
        for user in users:
            # Se user for dict, imprime username, senão imprime o valor direto
            if isinstance(user, dict):
                print(f"- {user.get('username', user)}")
            else:
                print(f"- {user}")
    except Exception as e:
        print(f"[ERRO] Não foi possível listar usuários: {e}")

    # Exemplo: buscar escala de plantão de cada time
    for team in COMPETENCIAS:
        print(f"\n=== {team} ===")
        try:
            schedule = get_team_oncall_schedule(api_id, api_key, org_id, team)
            print(schedule)
        except Exception as e:
            print(f"  [VictorOps] Falha ao buscar escala do time '{team}': {e}")


if __name__ == "__main__":
    main()

API_BASE = "https://api.victorops.com/api-public/v1"


def get_env_vars():
    load_dotenv()
    api_id = os.getenv("VICTOROPS_API_ID")
    api_key = os.getenv("VICTOROPS_API_KEY")
    org_id = os.getenv("VICTOROPS_ORG_ID")
    if not all([api_id, api_key, org_id]):
        raise Exception("Faltam variáveis de ambiente VICTOROPS_API_ID, VICTOROPS_API_KEY ou VICTOROPS_ORG_ID")
    return api_id, api_key, org_id


def get_headers(api_id, api_key, org_id):
    return {
        "X-VO-Api-Id": api_id,
        "X-VO-Api-Key": api_key,
        "X-VO-Org-Id": org_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_schedules(api_id, api_key, org_id):
    url = f"{API_BASE}/org/{org_id}/schedules"
    resp = requests.get(url, headers=get_headers(api_id, api_key, org_id))
    resp.raise_for_status()
    return resp.json().get("schedules", [])


def get_schedule_details(api_id, api_key, org_id, schedule_id):
    url = f"{API_BASE}/org/{org_id}/schedules/{schedule_id}"
    resp = requests.get(url, headers=get_headers(api_id, api_key, org_id))
    resp.raise_for_status()
    return resp.json()




    parser = argparse.ArgumentParser(description="Ajusta e exibe plantonistas VictorOps por competência.")
    parser.add_argument("--ajustar", nargs=2, metavar=("COMPETENCIA", "NOME"), help="Ajusta o plantonista atual da competência")
    args = parser.parse_args()

    api_id, api_key, org_id = get_env_vars()
    schedules = get_schedules(api_id, api_key, org_id)
    spreadsheet_id, credentials_path = get_planilha_env()

    for comp in COMPETENCIAS:
        schedule = next((s for s in schedules if s["name"] == comp), None)
        if not schedule:
            print(f"[ERRO] Schedule não encontrado para: {comp}")
            continue
        details = get_schedule_details(api_id, api_key, org_id, schedule["id"])
        print(f"\n=== {comp} ===")
        # Buscar plantonista da semana atual na planilha
        planilha_info = COMPETENCIA_PLANILHA_MAP[comp]
        try:
            plantonista_planilha = get_plantonista_atual(planilha_info["sheet"], planilha_info["col"], credentials_path, spreadsheet_id)
            print(f"  [Planilha] Atual: {plantonista_planilha}")
        except Exception as e:
            print(f"  [Planilha] ERRO ao buscar: {e}")
        # Exibir VictorOps
        try:
            rotations = details.get("rotations", [])
            for rot in rotations:
                participants = rot.get("participants", [])
                if not participants:
                    print("  [VictorOps] Nenhum participante encontrado.")
                    continue
                print(f"  [VictorOps] Rota: {rot.get('name', 'Sem nome')}")
                print(f"    Atual: {participants[0].get('name', participants[0])}")
                print("    Próximos:")
                for i, p in enumerate(participants[1:5], 1):
                    print(f"      {i}. {p.get('name', p)}")
        except Exception as e:
            print(f"  [VictorOps] Falha ao processar rota: {e}")

    # TODO: Implementar ajuste de plantonista via PUT se --ajustar for usado
    if args.ajustar:
        print("\n[INFO] Ajuste de plantonista ainda não implementado neste esqueleto.")

if __name__ == "__main__":
    main()
