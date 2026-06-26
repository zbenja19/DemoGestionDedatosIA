"""
kafka_producer.py

Productor Kafka que captura transacciones financieras como eventos
inmutables con hash SHA-256 para garantizar integridad.
"""

import os
import json
import uuid
import time
import hashlib
import random
import logging
from datetime import datetime
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

#1. Configuración de variables de entorno con valores por defecto
KAFKA_SERVERS  = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC_NOMBRE   = os.getenv("KAFKA_TOPIC", "fintech.transacciones")
INTERVALO_SEG  = float(os.getenv("INTERVALO_PRODUCCION", "2"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INGESTA] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Datos de ejemplo para generación de transacciones
CLIENTES  = ["CLI-001", "CLI-002", "CLI-003", "CLI-004", "CLI-005"]
CUENTAS   = {
    "CLI-001": "CTA-001234",
    "CLI-002": "CTA-001235",
    "CLI-003": "CTA-001236",
    "CLI-004": "CTA-001237",
    "CLI-005": "CTA-001238",
}
MONEDAS   = ["CLP", "USD", "EUR"]
ESTADOS   = ["APPROVED", "APPROVED", "APPROVED", "APPROVED",
             "APPROVED", "APPROVED", "APPROVED", "APPROVED",
             "REJECTED", "REVERSED"]  # 80% APPROVED


#2. Utilidades para generación de transacciones y cálculo de hash
def calcular_hash(payload: dict) -> str:
    """
    Calcula el hash SHA-256 del payload de la transacción.
    El hash garantiza la inmutabilidad del evento desde el origen.
    """
    contenido = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def generar_transaccion() -> dict:
    """Genera una transacción simulada con todos los campos requeridos."""
    cliente_id = random.choice(CLIENTES)
    cuenta_id  = CUENTAS[cliente_id]
    monto      = round(random.lognormvariate(12.5, 1.8), 2)
    moneda     = random.choices(MONEDAS, weights=[0.87, 0.11, 0.02])[0]
    estado     = random.choice(ESTADOS)
    timestamp  = datetime.now().isoformat()

    payload = {
        "event_id":   str(uuid.uuid4()),
        "cliente_id": cliente_id,
        "cuenta_id":  cuenta_id,
        "monto":      monto,
        "moneda":     moneda,
        "estado":     estado,
        "timestamp":  timestamp,
    }

    # Agregar hash SHA-256 al payload antes de publicar
    payload["hash_evento"] = calcular_hash(payload)
    return payload


def conectar_kafka(retries=10, delay=5) -> KafkaProducer:
    """Conecta al broker Kafka con reintentos."""
    for intento in range(1, retries + 1):
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                acks="all",           # Confirmación de todos los brokers
                retries=3,
                max_block_ms=30000,
            )
            log.info(f"Conectado a Kafka en {KAFKA_SERVERS}")
            return producer
        except NoBrokersAvailable:
            log.warning(f"Intento {intento}/{retries}: Kafka no disponible. "
                        f"Reintentando en {delay}s...")
            time.sleep(delay)

    raise RuntimeError("No se pudo conectar a Kafka después de varios intentos.")


#3. Función principal del productor Kafka
def main():
    log.info("=" * 55)
    log.info("  INICIO — Productor Kafka Fintech")
    log.info(f"  Topic: {TOPIC_NOMBRE}")
    log.info(f"  Broker: {KAFKA_SERVERS}")
    log.info("=" * 55)

    producer = conectar_kafka()
    contador = 0

    try:
        while True:
            transaccion = generar_transaccion()
            event_id    = transaccion["event_id"]
            estado      = transaccion["estado"]
            monto       = transaccion["monto"]

            # Publicar al topic Kafka
            future = producer.send(TOPIC_NOMBRE, value=transaccion)
            record = future.get(timeout=10)

            contador += 1
            log.info(
                f"[#{contador:05d}] event_id={event_id[:8]}... "
                f"estado={estado:<10} monto=${monto:>12,.0f} "
                f"offset={record.offset}"
            )

            time.sleep(INTERVALO_SEG)

    except KeyboardInterrupt:
        log.info("Producción detenida manualmente.")
    except Exception as e:
        log.error(f"Error inesperado: {e}")
    finally:
        producer.flush()
        producer.close()
        log.info(f"Total de transacciones publicadas: {contador}")


if __name__ == "__main__":
    main()