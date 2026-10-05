#!/usr/bin/env python3

import subprocess
import ipaddress
import urllib.request
import urllib.error
import json
import sqlite3
import argparse
import smtplib
import os

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "dispositivos.db")


# ------------------------------------------------------------
# SMTP
# ------------------------------------------------------------

SMTP_HOST = "smtp.dondominio.com"
SMTP_PORT = 587

SMTP_USER = "manel@molinaig.es"
SMTP_PASSWORD = "Agora709-"

SMTP_FROM = "manel@molinaig.es"
SMTP_TO = "manel@molinaig.es"

# True para STARTTLS (normalmente puerto 587)
SMTP_TLS = True

# True para SMTP SSL directo (normalmente puerto 465)
SMTP_SSL = False

EMAIL_SUBJECT = "Nuevo dispositiov en la red"


# ============================================================
# BASE DE DATOS
# ============================================================

def conectar_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS dispositivos (
            mac TEXT PRIMARY KEY,
            ip TEXT,
            fabricante TEXT,
            descripcion TEXT DEFAULT '',
            fecha_hora TEXT
        )
    """)

    conn.commit()

    return conn


def buscar_dispositivo(conn, mac):
    cursor = conn.execute(
        """
        SELECT mac, ip, fabricante, descripcion, fecha_hora
        FROM dispositivos
        WHERE mac = ?
        """,
        (mac.lower(),)
    )

    return cursor.fetchone()


def insertar_dispositivo(conn, ip, mac, fabricante):
    fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        """
        INSERT INTO dispositivos (
            mac,
            ip,
            fabricante,
            descripcion,
            fecha_hora
        )
        VALUES (?, ?, ?, '', ?)
        """,
        (
            mac.lower(),
            ip,
            fabricante,
            fecha_hora
        )
    )

    conn.commit()


def actualizar_dispositivo(conn, ip, mac, fabricante):
    fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        """
        UPDATE dispositivos
        SET
            ip = ?,
            fabricante = ?,
            fecha_hora = ?
        WHERE mac = ?
        """,
        (
            ip,
            fabricante,
            fecha_hora,
            mac.lower()
        )
    )

    conn.commit()


def actualizar_descripcion(conn, mac, descripcion):

    mac = normalizar_mac(mac)

    cursor = conn.execute(
        """
        UPDATE dispositivos
        SET descripcion = ?
        WHERE mac = ?
        """,
        (
            descripcion,
            mac
        )
    )

    conn.commit()

    if cursor.rowcount == 0:
        print(f"No existe ningún dispositivo con la MAC {mac}")
        return False

    print(f"Descripción actualizada:")
    print(f"MAC:         {mac}")
    print(f"Descripción: {descripcion}")

    return True


# ============================================================
# MAC
# ============================================================

def normalizar_mac(mac):
    mac = mac.strip().lower()

    # Admitimos:
    # aa:bb:cc:dd:ee:ff
    # aa-bb-cc-dd-ee-ff
    # aabbccddeeff

    limpia = mac.replace(":", "").replace("-", "")

    if len(limpia) != 12:
        raise ValueError("MAC no válida")

    try:
        int(limpia, 16)
    except ValueError:
        raise ValueError("MAC no válida")

    return ":".join(
        limpia[i:i + 2]
        for i in range(0, 12, 2)
    )


# ============================================================
# RED
# ============================================================

def obtener_red():

    resultado = subprocess.check_output(
        [
            "ip",
            "-4",
            "-o",
            "addr",
            "show",
            "scope",
            "global"
        ],
        text=True
    )

    for linea in resultado.splitlines():

        partes = linea.split()

        for parte in partes:

            if "/" not in parte or "." not in parte:
                continue

            try:
                interfaz = ipaddress.ip_interface(parte)

                if interfaz.version == 4:
                    return interfaz.network

            except ValueError:
                pass

    raise RuntimeError(
        "No se pudo detectar una red IPv4 local"
    )


def ping(ip):

    subprocess.run(
        [
            "ping",
            "-4",
            "-c",
            "1",
            "-W",
            "1",
            str(ip)
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


def escanear_red(red):

    print(f"Escaneando red IPv4 {red}...")

    with ThreadPoolExecutor(max_workers=50) as executor:
        list(
            executor.map(
                ping,
                red.hosts()
            )
        )


# ============================================================
# TABLA ARP
# ============================================================

def obtener_dispositivos():

    resultado = subprocess.check_output(
        [
            "ip",
            "-4",
            "neigh"
        ],
        text=True
    )

    dispositivos = []

    macs_vistas = set()

    for linea in resultado.splitlines():

        partes = linea.split()

        if "lladdr" not in partes:
            continue

        try:

            ip = partes[0]

            direccion = ipaddress.ip_address(ip)

            if direccion.version != 4:
                continue

            posicion = partes.index("lladdr")

            mac = normalizar_mac(
                partes[posicion + 1]
            )

            # Evitamos MAC duplicadas
            if mac in macs_vistas:
                continue

            macs_vistas.add(mac)

            dispositivos.append(
                {
                    "ip": ip,
                    "mac": mac
                }
            )

        except (
            ValueError,
            IndexError
        ):
            pass

    return dispositivos


# ============================================================
# FABRICANTE
# ============================================================

def fabricante_macvendors(mac):

    mac_limpia = (
        mac
        .replace(":", "")
        .replace("-", "")
    )

    url = (
        "https://macvendors.com/query/"
        + mac_limpia
    )

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=5
        ) as response:

            fabricante = (
                response
                .read()
                .decode("utf-8")
                .strip()
            )

            if fabricante:
                return fabricante

    except Exception:
        pass

    return None


def fabricante_maclookup(mac):

    url = (
        "https://api.maclookup.app/v2/macs/"
        + mac
    )

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=5
        ) as response:

            datos = json.loads(
                response
                .read()
                .decode("utf-8")
            )

            if datos.get("found"):

                fabricante = datos.get(
                    "company"
                )

                if fabricante:
                    return fabricante.strip()

    except Exception:
        pass

    return None


def fabricante_macadress(mac):

    url = (
        "https://api.macvendors.com/"
        + mac
    )

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=5
        ) as response:

            fabricante = (
                response
                .read()
                .decode("utf-8")
                .strip()
            )

            if fabricante:
                return fabricante

    except Exception:
        pass

    return None


def obtener_fabricante(mac):

    fabricante = fabricante_macvendors(mac)

    if fabricante:
        return fabricante

    fabricante = fabricante_maclookup(mac)

    if fabricante:
        return fabricante

    fabricante = fabricante_macadress(mac)

    if fabricante:
        return fabricante

    return "Desconocido"


# ============================================================
# CORREO
# ============================================================

def enviar_correo_nuevo_dispositivo(
    ip,
    mac,
    fabricante
):

    if not SMTP_HOST or not SMTP_TO:
        print(
            "SMTP no configurado. "
            "No se enviará correo."
        )

        return

    mensaje = EmailMessage()

    mensaje["From"] = SMTP_FROM
    mensaje["To"] = SMTP_TO
    mensaje["Subject"] = EMAIL_SUBJECT

    mensaje.set_content(
        f"""
Se ha detectado un nuevo dispositivo en la red.

IP: {ip}
MAC: {mac}
Fabricante: {fabricante}

Fecha/Hora: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
""".strip()
    )

    try:

        if SMTP_SSL:

            servidor = smtplib.SMTP_SSL(
                SMTP_HOST,
                SMTP_PORT,
                timeout=10
            )

        else:

            servidor = smtplib.SMTP(
                SMTP_HOST,
                SMTP_PORT,
                timeout=10
            )

        with servidor:

            servidor.ehlo()

            if SMTP_TLS and not SMTP_SSL:
                servidor.starttls()
                servidor.ehlo()

            if SMTP_USER:
                servidor.login(
                    SMTP_USER,
                    SMTP_PASSWORD
                )

            servidor.send_message(
                mensaje
            )

        print(
            f"Correo enviado por nuevo dispositivo: "
            f"{mac}"
        )

    except Exception as e:

        print(
            f"Error enviando correo: {e}"
        )


# ============================================================
# ESCANEO
# ============================================================

def procesar_dispositivos(conn):

    red = obtener_red()

    escanear_red(red)

    dispositivos = obtener_dispositivos()

    resultados = []

    for dispositivo in dispositivos:

        ip = dispositivo["ip"]
        mac = dispositivo["mac"]

        existente = buscar_dispositivo(
            conn,
            mac
        )

        fabricante = obtener_fabricante(
            mac
        )

        nuevo = existente is None

        if nuevo:

            insertar_dispositivo(
                conn,
                ip,
                mac,
                fabricante
            )

            descripcion = ""

            enviar_correo_nuevo_dispositivo(
                ip,
                mac,
                fabricante
            )

        else:

            actualizar_dispositivo(
                conn,
                ip,
                mac,
                fabricante
            )

            descripcion = existente[3]

        resultados.append(
            {
                "ip": ip,
                "mac": mac,
                "fabricante": fabricante,
                "descripcion": descripcion,
                "nuevo": nuevo
            }
        )

    return resultados


# ============================================================
# MOSTRAR RESULTADOS
# ============================================================

def mostrar_resultados(resultados):

    print()

    print(
        f"{'IP':<16} "
        f"{'MAC':<18} "
        f"{'FABRICANTE':<35} "
        f"{'DESCRIPCIÓN':<30} "
        f"{'FECHA_HORA':<20}"
    )

    print("-" * 125)

    fecha_hora = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    for dispositivo in resultados:

        nuevo = ""

        if dispositivo["nuevo"]:
            nuevo = " [NUEVO]"

        print(
            f"{dispositivo['ip']:<16} "
            f"{dispositivo['mac']:<18} "
            f"{dispositivo['fabricante'][:34]:<35} "
            f"{dispositivo['descripcion'][:29]:<30} "
            f"{fecha_hora:<20}"
            f"{nuevo}"
        )


# ============================================================
# ARGUMENTOS
# ============================================================

def obtener_argumentos():

    parser = argparse.ArgumentParser(
        description=(
            "Detecta dispositivos de la red "
            "y los almacena en SQLite"
        )
    )

    parser.add_argument(
        "-mac",
        "--mac",
        help="MAC del dispositivo"
    )

    parser.add_argument(
        "-description",
        "--description",
        help="Descripción del dispositivo"
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main():

    args = obtener_argumentos()

    conn = conectar_db()

    try:

        # --------------------------------------------
        # Actualización manual de descripción
        # --------------------------------------------

        if args.mac or args.description:

            if not args.mac or args.description is None:

                print(
                    "Debes especificar ambos parámetros:"
                )

                print(
                    '-mac MAC '
                    '-description "Descripción"'
                )

                return

            actualizar_descripcion(
                conn,
                args.mac,
                args.description
            )

            return

        # --------------------------------------------
        # Escaneo normal
        # --------------------------------------------

        resultados = procesar_dispositivos(
            conn
        )

        mostrar_resultados(
            resultados
        )

    finally:

        conn.close()


if __name__ == "__main__":
    main()
