import os
import sys

# Ensure current directory is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tornado.wsgi import WSGIContainer
from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop
from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Tornado WSGI Server for PIA Viewer on 127.0.0.1:{port}...")
    server = HTTPServer(WSGIContainer(app))
    server.listen(port, address="127.0.0.1")
    print("Server is listening.")
    try:
        IOLoop.current().start()
    except (KeyboardInterrupt, SystemExit):
        print("Server stopped.")
