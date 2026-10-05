#!/usr/bin/env python3

import json
import os
import shutil
import smtplib
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.message import EmailMessage


# ============================================================
# CONFIGURACIÓN
# ============================================================

# Si dejas este valor:
#
#   IPERF_SERVER = "iperf3serverlist.net"
#
# el script buscará automáticamente un servidor público.
#
# Si pones otro hostname o IP, utilizará ese servidor.
IPERF_SERVER = "iperf3serverlist.net"

# Solo se usa si configuras un servidor personalizado.
IPERF_PORT = 5201

# Duración de cada prueba iperf3.
IPERF_DURATION = 10

# Avisar si la velocidad cae más de este porcentaje.
ALERT_THRESHOLD_PERCENT = 10

# Número mínimo de mediciones anteriores necesarias
# para empezar a enviar alertas.
MIN_PREVIOUS_MEASUREMENTS = 3

# Base de datos SQLite.
DB_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "speedtest.db"
)

# Guardar mediciones durante un año.
RETENTION_DAYS = 365


# ============================================================
# CONFIGURACIÓN DE SERVIDORES PÚBLICOS
# ============================================================

SERVER_LIST_URL = (
    "https://export.iperf3serverlist.net/"
    "listed_iperf3_servers.json"
)

# Timeout de conexión TCP para medir latencia.
SERVER_LATENCY_TIMEOUT = 1.5

# Número máximo de comprobaciones simultáneas.
SERVER_CHECK_WORKERS = 30

# Número máximo de servidores candidatos.
AUTO_SERVER_CANDIDATES = 10

# Espera entre prueba de subida y bajada.
DELAY_BETWEEN_UPLOAD_DOWNLOAD = 3

# Número de intentos de bajada.
DOWNLOAD_RETRIES = 3

# Espera entre reintentos de bajada.
DOWNLOAD_RETRY_DELAY = 5


# ============================================================
# CONFIGURACIÓN SMTP
# ============================================================

SMTP_HOST = "smtp.example.com"
SMTP_PORT = 587

SMTP_USER = "usuario@example.com"
SMTP_PASSWORD = "PASSWORD"

# True  = STARTTLS, normalmente puerto 587
# False = SSL directo, normalmente puerto 465
SMTP_USE_TLS = True

EMAIL_FROM = "usuario@example.com"
EMAIL_TO = "destino@example.com"

EMAIL_SUBJECT = "Alerta de velocidad de Internet"


# ============================================================
# BASE DE DATOS
# ============================================================

def init_database():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            server_host TEXT NOT NULL,
            server_port INTEGER NOT NULL,
            upload_mbps REAL NOT NULL,
            download_mbps REAL NOT NULL
        )
    """)

    conn.commit()

    return conn


def cleanup_old_measurements(conn):
    """
    Elimina todas las mediciones anteriores a RETENTION_DAYS.
    Devuelve el número de registros eliminados.
    """

    limit = datetime.now() - timedelta(
        days=RETENTION_DAYS
    )

    cursor = conn.execute(
        """
        DELETE FROM measurements
        WHERE timestamp < ?
        """,
        (limit.isoformat(),)
    )

    deleted = cursor.rowcount

    conn.commit()

    return deleted


def get_historical_average(
    conn,
    server_host,
    server_port
):
    cursor = conn.execute(
        """
        SELECT
            COUNT(*),
            AVG(upload_mbps),
            AVG(download_mbps)
        FROM measurements
        WHERE server_host = ?
          AND server_port = ?
        """,
        (
            server_host,
            server_port
        )
    )

    return cursor.fetchone()


def save_measurement(
    conn,
    server_host,
    server_port,
    upload,
    download
):
    conn.execute(
        """
        INSERT INTO measurements (
            timestamp,
            server_host,
            server_port,
            upload_mbps,
            download_mbps
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            datetime.now().isoformat(),
            server_host,
            server_port,
            upload,
            download
        )
    )

    conn.commit()


# ============================================================
# ESTADÍSTICAS
# ============================================================

def show_stats(conn):
    """
    Muestra todas las mediciones almacenadas,
    la media global y las medias agrupadas por servidor.
    """

    cursor = conn.execute(
        """
        SELECT
            id,
            timestamp,
            server_host,
            server_port,
            upload_mbps,
            download_mbps
        FROM measurements
        ORDER BY timestamp ASC
        """
    )

    rows = cursor.fetchall()

    print()
    print("=" * 100)
    print("HISTÓRICO DE MEDICIONES")
    print("=" * 100)
    print()

    if not rows:
        print("No hay mediciones guardadas.")
        print()
        print(f"Base de datos: {DB_FILE}")
        return

    print(
        f"{'ID':<6} "
        f"{'FECHA/HORA':<20} "
        f"{'SERVIDOR':<30} "
        f"{'SUBIDA':>12} "
        f"{'BAJADA':>12}"
    )

    print("-" * 100)

    for (
        measurement_id,
        timestamp,
        server_host,
        server_port,
        upload,
        download
    ) in rows:

        try:
            dt = datetime.fromisoformat(timestamp)

            formatted_timestamp = dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        except ValueError:
            formatted_timestamp = timestamp[:19]

        server = f"{server_host}:{server_port}"

        print(
            f"{measurement_id:<6} "
            f"{formatted_timestamp:<20} "
            f"{server:<30} "
            f"{upload:>9.2f} Mbps "
            f"{download:>9.2f} Mbps"
        )

    print()
    print("=" * 100)
    print("MEDIA GLOBAL")
    print("=" * 100)

    cursor = conn.execute(
        """
        SELECT
            COUNT(*),
            AVG(upload_mbps),
            AVG(download_mbps),
            MIN(upload_mbps),
            MAX(upload_mbps),
            MIN(download_mbps),
            MAX(download_mbps)
        FROM measurements
        """
    )

    (
        count,
        avg_upload,
        avg_download,
        min_upload,
        max_upload,
        min_download,
        max_download
    ) = cursor.fetchone()

    print()
    print(f"Mediciones:       {count}")
    print()
    print(f"Subida media:     {avg_upload:.2f} Mbps")
    print(f"Subida mínima:    {min_upload:.2f} Mbps")
    print(f"Subida máxima:    {max_upload:.2f} Mbps")
    print()
    print(f"Bajada media:     {avg_download:.2f} Mbps")
    print(f"Bajada mínima:    {min_download:.2f} Mbps")
    print(f"Bajada máxima:    {max_download:.2f} Mbps")

    print()
    print("=" * 100)
    print("MEDIAS POR SERVIDOR")
    print("=" * 100)
    print()

    cursor = conn.execute(
        """
        SELECT
            server_host,
            server_port,
            COUNT(*),
            AVG(upload_mbps),
            AVG(download_mbps),
            MIN(upload_mbps),
            MAX(upload_mbps),
            MIN(download_mbps),
            MAX(download_mbps)
        FROM measurements
        GROUP BY
            server_host,
            server_port
        ORDER BY
            server_host,
            server_port
        """
    )

    server_rows = cursor.fetchall()

    for (
        server_host,
        server_port,
        server_count,
        server_avg_upload,
        server_avg_download,
        server_min_upload,
        server_max_upload,
        server_min_download,
        server_max_download
    ) in server_rows:

        print(f"Servidor: {server_host}:{server_port}")
        print(f"  Mediciones:     {server_count}")
        print(f"  Subida media:   {server_avg_upload:.2f} Mbps")
        print(f"  Subida mínima:  {server_min_upload:.2f} Mbps")
        print(f"  Subida máxima:  {server_max_upload:.2f} Mbps")
        print(f"  Bajada media:   {server_avg_download:.2f} Mbps")
        print(f"  Bajada mínima:  {server_min_download:.2f} Mbps")
        print(f"  Bajada máxima:  {server_max_download:.2f} Mbps")
        print()

    print("=" * 100)
    print(f"Base de datos: {DB_FILE}")
    print(f"Retención:     {RETENTION_DAYS} días")
    print("=" * 100)


# ============================================================
# SERVIDORES PÚBLICOS
# ============================================================

def download_server_list():
    print(
        "Descargando lista de servidores públicos..."
    )

    request = urllib.request.Request(
        SERVER_LIST_URL,
        headers={
            "User-Agent":
                "iperf3-speed-monitor/1.0"
        }
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:

            raw_data = (
                response
                .read()
                .decode("utf-8")
            )

    except Exception as e:
        raise RuntimeError(
            f"No se pudo descargar la lista "
            f"de servidores: {e}"
        )

    try:
        return json.loads(raw_data)

    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"La lista de servidores no contiene "
            f"JSON válido: {e}"
        )


def parse_first_port(port_value):
    """
    Devuelve únicamente el primer puerto disponible.

    Ejemplos:

    5201
        -> 5201

    30001-30010
        -> 30001
    """

    if not port_value:
        return None

    port_value = str(port_value).strip()

    try:
        if "-" in port_value:
            start, _ = port_value.split(
                "-",
                1
            )

            return int(start)

        return int(port_value)

    except (ValueError, TypeError):
        return None


def prepare_public_servers(raw_servers):
    servers = []

    for item in raw_servers:

        host = str(
            item.get(
                "IP/HOST",
                ""
            )
        ).strip()

        options = str(
            item.get(
                "OPTIONS",
                ""
            )
        )

        if not host:
            continue

        # Necesitamos soporte de -R
        # para poder medir descarga.
        if "-R" not in options:
            continue

        port = parse_first_port(
            item.get("PORT")
        )

        if port is None:
            continue

        servers.append({
            "host": host,
            "port": port,
            "country": str(
                item.get(
                    "COUNTRY",
                    ""
                )
            ),
            "site": str(
                item.get(
                    "SITE",
                    ""
                )
            ),
            "provider": str(
                item.get(
                    "PROVIDER",
                    ""
                )
            )
        })

    return servers


# ============================================================
# LATENCIA TCP
# ============================================================

def tcp_latency(server):
    host = server["host"]
    port = server["port"]

    try:
        start = time.perf_counter()

        sock = socket.create_connection(
            (
                host,
                port
            ),
            timeout=SERVER_LATENCY_TIMEOUT
        )

        elapsed_ms = (
            time.perf_counter()
            - start
        ) * 1000

        sock.close()

        return (
            server,
            elapsed_ms
        )

    except Exception:
        return (
            server,
            None
        )


def find_nearest_servers():
    raw_servers = download_server_list()

    servers = prepare_public_servers(
        raw_servers
    )

    if not servers:
        raise RuntimeError(
            "No se encontraron servidores "
            "públicos compatibles con -R."
        )

    print(
        f"Comprobando latencia contra "
        f"{len(servers)} servidores..."
    )

    results = []

    with ThreadPoolExecutor(
        max_workers=SERVER_CHECK_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                tcp_latency,
                server
            )
            for server in servers
        ]

        for future in as_completed(
            futures
        ):

            server, latency = (
                future.result()
            )

            if latency is None:
                continue

            result = server.copy()

            result["latency"] = (
                latency
            )

            results.append(
                result
            )

    if not results:
        raise RuntimeError(
            "No se pudo conectar con ningún "
            "servidor público."
        )

    results.sort(
        key=lambda item:
            item["latency"]
    )

    print()
    print(
        "Servidores con menor latencia:"
    )

    for server in results[:5]:

        print(
            f"  "
            f"{server['host']}:"
            f"{server['port']} "
            f"- "
            f"{server['latency']:.1f} ms "
            f"- "
            f"{server['site']} "
            f"- "
            f"{server['country']} "
            f"- "
            f"{server['provider']}"
        )

    print()

    return results[
        :AUTO_SERVER_CANDIDATES
    ]


# ============================================================
# IPERF3
# ============================================================

def run_iperf(
    server_host,
    server_port,
    reverse=False
):

    command = [
        "iperf3",
        "-c",
        server_host,
        "-p",
        str(server_port),
        "-t",
        str(IPERF_DURATION),
        "-J"
    ]

    if reverse:
        command.append("-R")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=IPERF_DURATION + 20
        )

    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "iperf3 ha excedido "
            "el tiempo máximo."
        )

    if result.returncode != 0:

        error = result.stderr.strip()

        if result.stdout:
            try:
                data = json.loads(
                    result.stdout
                )

                if data.get("error"):
                    error = data["error"]

            except Exception:
                pass

        raise RuntimeError(
            error
            or
            "Error desconocido ejecutando iperf3."
        )

    try:
        data = json.loads(
            result.stdout
        )

    except json.JSONDecodeError:
        raise RuntimeError(
            "No se pudo interpretar "
            "la salida JSON de iperf3."
        )

    if data.get("error"):
        raise RuntimeError(
            data["error"]
        )

    try:
        bits_per_second = (
            data["end"]
            ["sum_received"]
            ["bits_per_second"]
        )

    except KeyError:
        raise RuntimeError(
            "No se encontró la velocidad "
            "en la respuesta de iperf3."
        )

    return (
        bits_per_second
        / 1_000_000
    )


# ============================================================
# MEDICIÓN
# ============================================================

def measure_server(
    server_host,
    server_port
):

    print(
        f"Servidor: "
        f"{server_host}:"
        f"{server_port}"
    )

    # --------------------------------------------------------
    # SUBIDA
    # --------------------------------------------------------

    print(
        "Midiendo subida..."
    )

    upload = run_iperf(
        server_host,
        server_port,
        reverse=False
    )

    print(
        f"Subida:   "
        f"{upload:.2f} Mbps"
    )

    # --------------------------------------------------------
    # ESPERA
    # --------------------------------------------------------

    print(
        f"Esperando "
        f"{DELAY_BETWEEN_UPLOAD_DOWNLOAD} "
        f"segundos antes de medir bajada..."
    )

    time.sleep(
        DELAY_BETWEEN_UPLOAD_DOWNLOAD
    )

    # --------------------------------------------------------
    # BAJADA
    # --------------------------------------------------------

    download = None
    last_error = None

    for attempt in range(
        1,
        DOWNLOAD_RETRIES + 1
    ):

        try:
            print(
                f"Midiendo bajada "
                f"(intento "
                f"{attempt}/"
                f"{DOWNLOAD_RETRIES})..."
            )

            download = run_iperf(
                server_host,
                server_port,
                reverse=True
            )

            break

        except Exception as e:

            last_error = e

            print(
                f"Bajada fallida: {e}"
            )

            if (
                attempt
                < DOWNLOAD_RETRIES
            ):

                print(
                    f"Esperando "
                    f"{DOWNLOAD_RETRY_DELAY} "
                    f"segundos antes de "
                    f"reintentar..."
                )

                time.sleep(
                    DOWNLOAD_RETRY_DELAY
                )

    if download is None:
        raise RuntimeError(
            f"No se pudo medir bajada "
            f"después de "
            f"{DOWNLOAD_RETRIES} intentos: "
            f"{last_error}"
        )

    print(
        f"Bajada:   "
        f"{download:.2f} Mbps"
    )

    return (
        upload,
        download
    )


def measure_automatic():
    candidates = (
        find_nearest_servers()
    )

    for server in candidates:

        host = server["host"]
        port = server["port"]

        print(
            f"Probando "
            f"{host}:{port}..."
        )

        try:
            upload, download = (
                measure_server(
                    host,
                    port
                )
            )

            return (
                host,
                port,
                upload,
                download,
                server
            )

        except Exception as e:

            print(
                f"No disponible: {e}"
            )

            print(
                "Probando siguiente servidor..."
            )

            print()

    raise RuntimeError(
        "No se pudo realizar la prueba "
        "con ningún servidor público."
    )


# ============================================================
# EMAIL
# ============================================================

def send_email(body):
    msg = EmailMessage()

    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = EMAIL_SUBJECT

    msg.set_content(body)

    try:
        if SMTP_USE_TLS:

            smtp = smtplib.SMTP(
                SMTP_HOST,
                SMTP_PORT,
                timeout=30
            )

            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()

        else:

            smtp = smtplib.SMTP_SSL(
                SMTP_HOST,
                SMTP_PORT,
                timeout=30
            )

        if SMTP_USER:

            smtp.login(
                SMTP_USER,
                SMTP_PASSWORD
            )

        smtp.send_message(
            msg
        )

        smtp.quit()

        print(
            "Correo de alerta enviado."
        )

    except Exception as e:

        print(
            f"Error enviando correo: {e}",
            file=sys.stderr
        )


# ============================================================
# ALERTAS
# ============================================================

def calculate_reduction(
    current,
    average
):

    if (
        average is None
        or
        average <= 0
    ):
        return 0

    return (
        (
            average - current
        )
        /
        average
    ) * 100


def check_speed(
    server_host,
    server_port,
    upload,
    download,
    count,
    avg_upload,
    avg_download
):

    if (
        count
        < MIN_PREVIOUS_MEASUREMENTS
    ):

        print()

        print(
            f"Hay {count} mediciones "
            f"anteriores para "
            f"{server_host}:"
            f"{server_port}."
        )

        print(
            f"Se necesitan "
            f"{MIN_PREVIOUS_MEASUREMENTS} "
            f"para generar alertas."
        )

        return

    upload_reduction = (
        calculate_reduction(
            upload,
            avg_upload
        )
    )

    download_reduction = (
        calculate_reduction(
            download,
            avg_download
        )
    )

    print()

    print(
        f"Media histórica subida: "
        f"{avg_upload:.2f} Mbps"
    )

    print(
        f"Media histórica bajada: "
        f"{avg_download:.2f} Mbps"
    )

    print(
        f"Reducción subida: "
        f"{upload_reduction:.2f}%"
    )

    print(
        f"Reducción bajada: "
        f"{download_reduction:.2f}%"
    )

    alerts = []

    if (
        upload_reduction
        > ALERT_THRESHOLD_PERCENT
    ):

        alerts.append(
            f"""
SUBIDA

Actual: {upload:.2f} Mbps
Media histórica: {avg_upload:.2f} Mbps
Reducción: {upload_reduction:.2f} %
"""
        )

    if (
        download_reduction
        > ALERT_THRESHOLD_PERCENT
    ):

        alerts.append(
            f"""
BAJADA

Actual: {download:.2f} Mbps
Media histórica: {avg_download:.2f} Mbps
Reducción: {download_reduction:.2f} %
"""
        )

    if alerts:

        body = f"""
Se ha detectado una reducción significativa de velocidad.

Servidor:
{server_host}:{server_port}

Umbral configurado:
{ALERT_THRESHOLD_PERCENT} %

Mediciones históricas utilizadas:
{count}

{"".join(alerts)}

Fecha:
{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

        send_email(
            body
        )

    else:

        print(
            f"No se supera el umbral "
            f"del "
            f"{ALERT_THRESHOLD_PERCENT}%."
        )


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # PARÁMETROS
    # --------------------------------------------------------

    stats_mode = "-stats" in sys.argv[1:]

    unknown_arguments = [
        argument
        for argument in sys.argv[1:]
        if argument != "-stats"
    ]

    if unknown_arguments:
        print(
            f"ERROR: Parámetro desconocido: "
            f"{unknown_arguments[0]}",
            file=sys.stderr
        )

        print(
            f"Uso:",
            file=sys.stderr
        )

        print(
            f"  {sys.argv[0]}",
            file=sys.stderr
        )

        print(
            f"  {sys.argv[0]} -stats",
            file=sys.stderr
        )

        sys.exit(1)

    # --------------------------------------------------------
    # BASE DE DATOS
    # --------------------------------------------------------

    conn = init_database()

    try:

        # ----------------------------------------------------
        # LIMPIAR REGISTROS ANTIGUOS
        # ----------------------------------------------------

        deleted = cleanup_old_measurements(
            conn
        )

        if deleted > 0:
            print(
                f"Se han eliminado "
                f"{deleted} mediciones con más de "
                f"{RETENTION_DAYS} días."
            )

        # ----------------------------------------------------
        # MODO ESTADÍSTICAS
        # ----------------------------------------------------

        if stats_mode:

            show_stats(
                conn
            )

            return

        # ----------------------------------------------------
        # COMPROBAR IPERF3
        # ----------------------------------------------------

        if shutil.which(
            "iperf3"
        ) is None:

            print(
                "ERROR: No se encuentra iperf3. "
                "Instálalo con: "
                "sudo apt install iperf3",
                file=sys.stderr
            )

            sys.exit(1)

        # ----------------------------------------------------
        # SERVIDOR AUTOMÁTICO
        # ----------------------------------------------------

        if (
            IPERF_SERVER
            .strip()
            .lower()
            ==
            "iperf3serverlist.net"
        ):

            print(
                "Modo automático "
                "iperf3serverlist.net"
            )

            (
                server_host,
                server_port,
                upload,
                download,
                server_info
            ) = measure_automatic()

            print()

            print(
                "Servidor seleccionado:"
            )

            print(
                f"  Host:      "
                f"{server_host}"
            )

            print(
                f"  Puerto:    "
                f"{server_port}"
            )

            print(
                f"  Ciudad:    "
                f"{server_info['site']}"
            )

            print(
                f"  País:      "
                f"{server_info['country']}"
            )

            print(
                f"  Proveedor: "
                f"{server_info['provider']}"
            )

            print(
                f"  Latencia:  "
                f"{server_info['latency']:.1f} ms"
            )

        # ----------------------------------------------------
        # SERVIDOR PERSONALIZADO
        # ----------------------------------------------------

        else:

            server_host = (
                IPERF_SERVER
            )

            server_port = (
                IPERF_PORT
            )

            print(
                "Modo servidor personalizado."
            )

            upload, download = (
                measure_server(
                    server_host,
                    server_port
                )
            )

        # ----------------------------------------------------
        # HISTÓRICO
        # ----------------------------------------------------

        (
            count,
            avg_upload,
            avg_download
        ) = get_historical_average(
            conn,
            server_host,
            server_port
        )

        # ----------------------------------------------------
        # ALERTAS
        # ----------------------------------------------------

        check_speed(
            server_host,
            server_port,
            upload,
            download,
            count,
            avg_upload,
            avg_download
        )

        # ----------------------------------------------------
        # GUARDAR MEDICIÓN
        # ----------------------------------------------------

        save_measurement(
            conn,
            server_host,
            server_port,
            upload,
            download
        )

        print()

        print(
            "Medición guardada correctamente."
        )

        print(
            f"Base de datos: "
            f"{DB_FILE}"
        )

    except Exception as e:

        print(
            f"ERROR: {e}",
            file=sys.stderr
        )

        sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
