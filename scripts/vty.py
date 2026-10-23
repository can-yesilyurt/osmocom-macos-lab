"""Bounded VTY command sessions for the local lab."""
import socket
import time


def ready(port):
    with socket.socket() as s:
        s.settimeout(.15)
        return s.connect_ex(("127.0.0.1",port)) == 0


def read_prompt(sock):
    data = b""
    end = time.monotonic() + 3
    while time.monotonic() < end:
        try: part = sock.recv(65536)
        except socket.timeout: continue
        if not part: break
        data += part
        if data.endswith((b"> ",b"# ")): return data
    raise RuntimeError("VTY did not return a prompt")


def query(port, commands):
    for command in commands:
        if any(c in command for c in "\r\n\0"):
            raise ValueError("VTY commands must be individual lines")
    with socket.create_connection(("127.0.0.1",port),timeout=2) as sock:
        sock.settimeout(.15)
        reply = read_prompt(sock)
        for command in ("enable","terminal length 0",*commands):
            sock.sendall(command.encode()+b"\r\n")
            reply += read_prompt(sock)
    text = reply.decode(errors="replace")
    if any(error in text for error in ("% Unknown command","% Command incomplete","% Ambiguous command")):
        raise RuntimeError("VTY rejected a command: " + text)
    return text
