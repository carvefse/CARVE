import http.server
import os
import ssl
from functools import partial

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
server_ip = '127.0.0.1'
server_port = 4443

print(f"Running at https://{server_ip}:{server_port}")

server_address = (server_ip, server_port)
handler = partial(http.server.SimpleHTTPRequestHandler, directory=BASE_DIR)
httpd = http.server.HTTPServer(server_address, handler)

context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(certfile=os.path.join(BASE_DIR, 'cert.pem'), keyfile=os.path.join(BASE_DIR, 'key.pem'))

httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
httpd.serve_forever()
