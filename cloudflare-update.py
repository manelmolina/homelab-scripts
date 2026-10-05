import sys
import requests
import ipaddress

IP_API_URL = "http://ip-api.com/csv?fields=query"
CF_API_BASE = "https://api.cloudflare.com/client/v4"

# ============================================================
# CONFIGURACIÓN
# ============================================================

CLOUDFLARE_API_TOKEN = "xxxxxxxxxx"

ZONE_NAME = "domain.tld"

RECORDS = [
    "subdomain.domain.tld",
    "home.domain.tld",
    "vpn.domain.tld",
    "server.domain.tld",
]

TTL = 120
PROXIED = False


# ============================================================
# OBTENER IP PÚBLICA
# ============================================================

def get_public_ip() -> str:
    r = requests.get(
        IP_API_URL,
        timeout=10
    )

    r.raise_for_status()

    ip = r.text.strip()

    try:
        parsed = ipaddress.ip_address(ip)

        if parsed.version != 4:
            raise ValueError("La IP obtenida no es IPv4")

    except ValueError:
        raise ValueError(
            f"IP pública inválida recibida: {ip!r}"
        )

    return ip


# ============================================================
# CLOUDFLARE
# ============================================================

def cf_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def cf_get_zone_id(
    token: str,
    zone_name: str
) -> str:

    r = requests.get(
        f"{CF_API_BASE}/zones",
        headers=cf_headers(token),
        params={
            "name": zone_name,
            "status": "active",
        },
        timeout=15,
    )

    r.raise_for_status()

    data = r.json()

    if not data.get("success") or not data.get("result"):
        raise RuntimeError(
            f"No se encontró la zona activa {zone_name!r}: {data}"
        )

    return data["result"][0]["id"]


def cf_get_a_record(
    token: str,
    zone_id: str,
    record_name: str
):

    r = requests.get(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records",
        headers=cf_headers(token),
        params={
            "type": "A",
            "name": record_name,
        },
        timeout=15,
    )

    r.raise_for_status()

    data = r.json()

    if not data.get("success"):
        raise RuntimeError(
            f"Error buscando registro DNS {record_name}: {data}"
        )

    results = data.get("result", [])

    return results[0] if results else None


def cf_update_record(
    token: str,
    zone_id: str,
    record_id: str,
    payload: dict
):

    r = requests.put(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
        headers=cf_headers(token),
        json=payload,
        timeout=15,
    )

    r.raise_for_status()

    data = r.json()

    if not data.get("success"):
        raise RuntimeError(
            f"Error actualizando registro: {data}"
        )

    return data["result"]


# ============================================================
# ACTUALIZAR REGISTRO
# ============================================================

def update_record(
    token: str,
    zone_id: str,
    record_name: str,
    public_ip: str
):

    existing = cf_get_a_record(
        token,
        zone_id,
        record_name
    )

    # El registro no existe: no hacemos nada
    if not existing:
        print(
            f"SKIPPED: {record_name} no existe como registro A."
        )
        return

    current_ip = existing.get("content")
    current_proxied = existing.get("proxied")
    current_ttl = existing.get("ttl")

    # Ya está correcto
    if (
        current_ip == public_ip
        and current_proxied == PROXIED
        and current_ttl == TTL
    ):
        print(
            f"OK:      {record_name} -> {public_ip} "
            "(sin cambios)"
        )
        return

    payload = {
        "type": "A",
        "name": record_name,
        "content": public_ip,
        "ttl": TTL,
        "proxied": PROXIED,
    }

    updated = cf_update_record(
        token,
        zone_id,
        existing["id"],
        payload
    )

    print(
        f"UPDATED: {updated['name']} -> "
        f"{updated['content']} "
        f"(proxied={updated.get('proxied')}, "
        f"ttl={updated.get('ttl')})"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    token = CLOUDFLARE_API_TOKEN

    if not token:
        print(
            "ERROR: No se ha configurado el token de Cloudflare.",
            file=sys.stderr
        )
        sys.exit(2)

    public_ip = get_public_ip()

    print(f"IP pública actual: {public_ip}")
    print()

    zone_id = cf_get_zone_id(
        token,
        ZONE_NAME
    )

    for record_name in RECORDS:

        try:
            update_record(
                token,
                zone_id,
                record_name,
                public_ip
            )

        except Exception as e:
            print(
                f"ERROR:   {record_name}: {e}",
                file=sys.stderr
            )


if __name__ == "__main__":
    main()
