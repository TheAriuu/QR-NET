"""
Demo de integración Capa 2/3 sobre un canal en memoria.

Simula dos nodos QRNetNode (A y B) comunicándose sin hardware:
  - Un LoopbackAdapter reemplaza al DispositivoLuzAdaptador
  - Nodo A descubre a Nodo B, negocia un circuito y le envía un mensaje
  - Nodo B recibe el mensaje

Cómo correrlo:
    python tests/integration_demo.py
"""
import sys, os, time, queue
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from transmision.interfaces import INetworkAdapter
from common.network_policies import AdapterType, SelectionPolicy
from red.implementation.node import QRNetNode
from red.implementation.routing import RoutingTable, CircuitTable
from red.implementation.directory import NodeDirectory
from transmision.implementation.selector import AdapterSelector
from red.packet import Packet, PacketType


# ── Adaptador loopback ─────────────────────────────────────────────────────────

class LoopbackAdapter(INetworkAdapter):
    """
    Canal bidireccional en memoria entre dos nodos.
    A.send() llega a B.receive(), y viceversa.
    """
    def __init__(self, mac: bytes, inbox: queue.Queue, outbox: queue.Queue):
        self._mac    = mac
        self._inbox  = inbox
        self._outbox = outbox

    def send(self, data: bytes) -> bool:
        self._outbox.put(data)
        return True

    def receive(self) -> bytes | None:
        try:
            return self._inbox.get_nowait()
        except queue.Empty:
            return None

    def is_available(self) -> bool:  return True
    def get_mac(self)      -> bytes: return self._mac
    def get_type(self):              return AdapterType.ETHERNET
    def get_cost(self)     -> int:   return 1


def make_loopback_pair():
    """Crea dos adaptadores conectados entre sí."""
    q_ab  = queue.Queue()       # paquetes A → B
    q_ba  = queue.Queue()       # paquetes B → A
    mac_a = b'\xAA' * 6
    mac_b = b'\xBB' * 6
    adapter_a = LoopbackAdapter(mac_a, inbox=q_ba, outbox=q_ab)
    adapter_b = LoopbackAdapter(mac_b, inbox=q_ab, outbox=q_ba)
    return adapter_a, adapter_b


def drain_until(q: queue.Queue, ptype: PacketType, timeout: float = 2.0) -> Packet:
    """
    Lee paquetes de la cola hasta encontrar uno del tipo pedido.
    Descarta paquetes intermedios (ej. HELLOs de respuesta).
    Lanza queue.Empty si se acaba el tiempo.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = q.get(timeout=0.1)
            pkt = Packet.from_bytes(raw)
            if pkt.packet_type == ptype:
                return pkt
            # paquete de otro tipo (ej. HELLO de vuelta) → descartarlo
        except queue.Empty:
            continue
    raise queue.Empty(f"No llegó ningún paquete de tipo {ptype.name} en {timeout}s")


# ── Demo ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  QR-NET — Demo integración Capa 2/3 (loopback)")
    print("=" * 55)

    # Crear canal en memoria
    adapter_a, adapter_b = make_loopback_pair()

    # Selectores
    selector_a = AdapterSelector(policy=SelectionPolicy.FIRST_AVAILABLE)
    selector_a.register(adapter_a)

    selector_b = AdapterSelector(policy=SelectionPolicy.FIRST_AVAILABLE)
    selector_b.register(adapter_b)

    # IDs y nodos
    id_a = b'\xAA' * 16
    id_b = b'\xBB' * 16

    node_a = QRNetNode(selector=selector_a, node_id=id_a)
    node_b = QRNetNode(selector=selector_b, node_id=id_b)

    # Marcar como activos (sin lanzar los hilos de fondo)
    node_a._running = True
    node_b._running = True

    print(f"\n[+] Nodo A: {id_a.hex()[:12]}...")
    print(f"[+] Nodo B: {id_b.hex()[:12]}...")

    # ── Paso 1: descubrimiento ────────────────────────────────────────────────
    print("\n[1] Intercambiando HELLOs...")

    # A envía HELLO → B lo recibe y responde
    hello_a = Packet.hello(src_id=id_a, payload=id_a)
    node_b._handle_incoming(hello_a, via_mac=adapter_a.get_mac())

    # La respuesta de B llega al outbox de B (q_ba → inbox de A)
    raw_reply = drain_until(adapter_b._outbox, PacketType.HELLO)
    node_a._handle_incoming(raw_reply, via_mac=adapter_b.get_mac())

    # Verificar tablas de ruteo
    route_a = node_a._routing.lookup(id_b)
    route_b = node_b._routing.lookup(id_a)
    assert route_a is not None, "A no encontro ruta hacia B"
    assert route_b is not None, "B no encontro ruta hacia A"

    print(f"    A conoce B -> next_hop: {route_a.next_hop_mac.hex()}, cost: {route_a.cost}")
    print(f"    B conoce A -> next_hop: {route_b.next_hop_mac.hex()}, cost: {route_b.cost}")
    print("    OK")

    # ── Paso 2: negociar circuito anónimo ─────────────────────────────────────
    print("\n[2] Negociando circuito anonimo A -> B...")

    cid = node_a.negotiate_circuit(id_b)

    # El CIRCUIT_SETUP sale por adapter_a._outbox (q_ab)
    # Puede haber HELLOs residuales antes — drain_until los salta
    setup_pkt = drain_until(adapter_a._outbox, PacketType.CIRCUIT_SETUP)

    assert setup_pkt.circuit_id == cid, \
        f"circuit_id no coincide: esperado {cid:#010x}, recibido {setup_pkt.circuit_id:#010x}"

    # B registra su extremo del circuito
    node_b._handle_incoming(setup_pkt, via_mac=adapter_a.get_mac())

    entry_b = node_b._circuits.lookup(cid)
    assert entry_b is not None, "B no registro el circuito"
    prev_b, next_b = entry_b

    print(f"    circuit_id : {cid:#010x}")
    print(f"    B registro : prev={prev_b.hex()}, next={next_b}")
    print("    OK")

    # ── Paso 3: envío anónimo ─────────────────────────────────────────────────
    print("\n[3] Enviando mensaje anonimo A -> B...")

    mensaje = b"Hola desde QR-NET!"
    node_a.send_anonymous(mensaje, id_b)

    # El DATA sale por adapter_a._outbox (q_ab)
    data_pkt = drain_until(adapter_a._outbox, PacketType.DATA)

    assert data_pkt.circuit_id == cid
    assert data_pkt.payload    == mensaje

    # B lo procesa y lo entrega a su buffer local
    node_b.route_packet(data_pkt)
    recibido = node_b.receive()

    assert recibido == mensaje, f"Mensaje incorrecto: {recibido!r}"
    print(f"    Enviado  : {mensaje}")
    print(f"    Recibido : {recibido}")
    print("    OK")

    # ── Paso 4: verificar anonimato ───────────────────────────────────────────
    print("\n[4] Verificando anonimato...")

    src_visible = data_pkt.src_id
    print(f"    src_id visible en paquete : {src_visible.hex()[:16]}...")
    print(f"    node_id real de A         : {id_a.hex()[:16]}...")
    assert src_visible != id_a, "ERROR: el node_id real de A es visible en el paquete"
    print("    El node_id real de A NO es visible. OK")

    # ── Resumen ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  Todas las verificaciones pasaron")
    print("=" * 55)


if __name__ == "__main__":
    main()