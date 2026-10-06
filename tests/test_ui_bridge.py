"""Fase 7: puente WebSocket con la app Electron. emit() nunca lanza y es
no-op sin servidor/cliente; start() no bloquea; el Origin se valida."""
import json
import os
import socket
import sys
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui_bridge  # noqa: E402
from websockets.exceptions import InvalidStatus  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

ORIGEN_ELECTRON = "file://"

EVENTOS = [
    ("hello",             {"state": "idle"}),
    ("state",             {"state": "listening"}),
    ("state",             {"state": "processing"}),
    ("state",             {"state": "not_understood"}),
    ("state",             {"state": "responding"}),
    ("state",             {"state": "interrupted"}),
    ("state",             {"state": "idle"}),
    ("user_message",      {"text": "¿Qué tiempo hace?"}),
    ("assistant_message", {"text": "Hace 21 grados."}),
    ("error",             {"message": "algo falló"}),
]


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestEmitSinServidor(unittest.TestCase):
    def test_emit_no_lanza_nunca(self):
        with patch.object(ui_bridge, "_loop", None):
            ui_bridge.emit("state", state="listening")
            ui_bridge.emit("user_message", text="hola")
            ui_bridge.emit("raro", dato=object())  # no serializable
            ui_bridge.emit("state")                # sin state

    def test_emit_no_lanza_aunque_falle_el_loop(self):
        loop = MagicMock()
        loop.call_soon_threadsafe.side_effect = RuntimeError("loop cerrado")
        with patch.object(ui_bridge, "_loop", loop), \
             patch.object(ui_bridge, "_clientes", {object()}):
            ui_bridge.emit("user_message", text="hola")
            ui_bridge.emit("raro", dato=object())

    def test_emit_noop_sin_cliente(self):
        loop = MagicMock()
        with patch.object(ui_bridge, "_loop", loop), \
             patch.object(ui_bridge, "_clientes", set()):
            ui_bridge.emit("user_message", text="hola")
        loop.call_soon_threadsafe.assert_not_called()

    def test_emit_state_recuerda_estado_para_hello(self):
        with patch.object(ui_bridge, "_loop", None), \
             patch.object(ui_bridge, "_estado", "idle"):
            ui_bridge.emit("state", state="processing")
            self.assertEqual(ui_bridge._estado, "processing")


class TestProtocolo(unittest.TestCase):
    def test_cada_evento_tiene_v_type_ts(self):
        for tipo, datos in EVENTOS:
            with self.subTest(tipo=tipo, **datos):
                msg = json.loads(ui_bridge._mensaje(tipo, **datos))
                self.assertEqual(msg["v"], 1)
                self.assertEqual(msg["type"], tipo)
                self.assertIsNotNone(datetime.fromisoformat(msg["ts"]).tzinfo)
                for k, v in datos.items():
                    self.assertEqual(msg[k], v)

    def test_datos_no_pisan_el_sobre(self):
        msg = json.loads(ui_bridge._mensaje("state", v=99, ts="x", state="idle"))
        self.assertEqual(msg["v"], 1)
        self.assertNotEqual(msg["ts"], "x")

    def test_utf8_sin_escapar(self):
        self.assertIn("¿Qué", ui_bridge._mensaje("user_message", text="¿Qué tal?"))


class TestServidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.puerto = _puerto_libre()
        cls.uri    = f"ws://127.0.0.1:{cls.puerto}"
        t0 = time.monotonic()
        ui_bridge.start(port=cls.puerto)
        cls.t_start = time.monotonic() - t0
        # espera a que el servidor acepte conexiones
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            try:
                with connect(cls.uri, origin=ORIGEN_ELECTRON):
                    break
            except (OSError, InvalidStatus):
                time.sleep(0.05)
        else:
            raise RuntimeError("el servidor no arrancó")

    def _esperar_sin_clientes(self):
        limite = time.monotonic() + 2
        while ui_bridge._clientes and time.monotonic() < limite:
            time.sleep(0.02)

    def test_start_no_bloquea(self):
        self.assertLess(self.t_start, 0.5)
        hilo = ui_bridge._hilo
        self.assertTrue(hilo.daemon)
        self.assertTrue(hilo.is_alive())
        self.assertIsNot(hilo, threading.main_thread())

    def test_start_repetido_no_crea_otro_hilo(self):
        hilo = ui_bridge._hilo
        ui_bridge.start(port=self.puerto)
        self.assertIs(ui_bridge._hilo, hilo)

    def test_hello_con_estado_actual(self):
        self._esperar_sin_clientes()
        ui_bridge.emit("state", state="processing")
        with connect(self.uri, origin=ORIGEN_ELECTRON) as ws:
            msg = json.loads(ws.recv(timeout=2))
        self.assertEqual(msg["type"], "hello")
        self.assertEqual(msg["state"], "processing")
        self.assertEqual(msg["v"], 1)

    def test_emit_llega_al_cliente(self):
        with connect(self.uri, origin=ORIGEN_ELECTRON) as ws:
            json.loads(ws.recv(timeout=2))  # hello
            ui_bridge.emit("user_message", text="hola")
            ui_bridge.emit("state", state="responding")
            m1 = json.loads(ws.recv(timeout=2))
            m2 = json.loads(ws.recv(timeout=2))
        self.assertEqual((m1["type"], m1["text"]), ("user_message", "hola"))
        self.assertEqual((m2["type"], m2["state"]), ("state", "responding"))

    def test_emit_desde_otro_hilo(self):
        with connect(self.uri, origin=ORIGEN_ELECTRON) as ws:
            json.loads(ws.recv(timeout=2))  # hello
            t = threading.Thread(target=ui_bridge.emit, args=("assistant_message",),
                                 kwargs={"text": "desde hilo"})
            t.start()
            t.join()
            msg = json.loads(ws.recv(timeout=2))
        self.assertEqual(msg["text"], "desde hilo")

    def test_origen_electron_aceptado(self):
        for origen in (ORIGEN_ELECTRON,):
            with self.subTest(origen=origen):
                with connect(self.uri, origin=origen) as ws:
                    self.assertEqual(json.loads(ws.recv(timeout=2))["type"], "hello")

    def test_origenes_no_electron_rechazados(self):
        # None = sin cabecera Origin; "null" = iframe sandbox de cualquier web
        for origen in (None, "null", "http://localhost:5173", "http://127.0.0.1",
                       "file:///C:/Users/x/evil.html", "https://evil.example",
                       "http://localhost.evil.com", "http://192.168.1.10:8765"):
            with self.subTest(origen=origen):
                with self.assertRaises(InvalidStatus) as ctx:
                    with connect(self.uri, origin=origen):
                        pass
                self.assertEqual(ctx.exception.response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
