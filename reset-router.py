import re
import requests
import hashlib

### Definición de credenciales ###
ip = "192.168.1.1" 
user = "1234"
password = "xxxxxx" # Si no se ha cambiado es la que aparece bajo el router

### Inicio de sesión ###
session = requests.Session()
url_login = "http://" + ip + "/cgi-bin/login_advance.cgi"
# Se extrae el SID del JS de la página de login
response = session.get(url_login)
html = response.text
sid_match  = re.search(r"var\s+sid\s*=\s*'([0-9a-fA-F]+)';", html)
sid = sid_match.group(1)
# Se saca el valor de LoginPasswordValue al unir: contraseña + SID en hexadecimal y encriptarlo en MD5
password_and_sid = f"{password}:{sid}"
login_password_value = hashlib.md5(password_and_sid.encode("utf-8")).hexdigest()
# Se envía el formulario de login con los valores esperados
post = {
    "Loginuser": user,
    "LoginPasswordValue": login_password_value,
    "submitValue": "1",
    "LoginSidValue": sid,
    "Prestige_Login": "Login",
}
session.post(url_login, data=post)

### Reinicio del router ###
url_reinicio = "http://" + ip + "/cgi-bin/reboot.cgi"
post = {
    "restoreFlag":"1",
    "RestartBtn":"RESTART"
}
try:
    session.post(url_reinicio, data=post)
except Exception as e:
    pass # Da error al perder la comunicación con el router antes de recibir respuesta  
print("Reiniciando el router...")
exit()
