# homelab-scripts
Homelab scripts

cloudflare-update.py: este script actualiza la IP de las entradas DNS del dominio especificado en Cloudflare, la IP que se utilizará será la IP pública obtenida desde el equipo en el que se use el script.

network-scan.py: este script obtiene la tabla ARP y identifica los dispositivos habituales que hay en la red, en los siguientes escaneos identifica nuevos dispositivos y nos envía un correo.

Además, si se llama al script con los parametros -mac XX:XX:XX:XX:XX:XX -description "PC de Manel" se puede poner una descripción que nos ayude a identificar la MAC más adelante.

speedtext.py: este script utiliza iperf3 para medir la velocidad de conexión a internet, por defecto usa los servidores públicos, pero se puede apuntar a un servidor iperf3 autoalojado. Si la velocidad cae por debajo del humbral indicado, se nos envía un email para avisarnos.

reset-router.py: este script reinicia el router de movistar/o2 para prevenir caidas de rendimiento.
