"""
reporte_regulatorio.py

Genera reportes regulatorios mensuales sellados con hash SHA-256.
Cumple con los requisitos de la CMF y la Ley 19.913.
"""

import os
import sys
import json
import hashlib
import logging
import psycopg2
from datetime import datetime
from decimal import Decimal

#1. Configuración de la base de datos desde variables de entorno
DB_CONFIG = {
    "host":     os.getenv("DB_HOST",           "postgres"),
    "port":     os.getenv("DB_PORT",           "5432"),
    "dbname":   os.getenv("POSTGRES_DB",       "fintech_auditoria"),
    "user":     os.getenv("POSTGRES_USER",     "auditoria_user"),
    "password": os.getenv("POSTGRES_PASSWORD", "auditoria_pass"),
}

UMBRAL_CMF_CLP = 10_000_000  # Transacciones >= 10M CLP deben reportarse a CMF

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [REPORTES] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


#2. Funciones de consulta a PostgreSQL
def obtener_transacciones_periodo(cur, anio: int, mes: int) -> list:
    """Obtiene todas las transacciones del período desde PostgreSQL."""
    cur.execute("""
        SELECT
            event_id, cliente_id, cuenta_id,
            monto, moneda, estado, timestamp, hash_evento
        FROM auditoria.transacciones_procesadas
        WHERE EXTRACT(YEAR  FROM timestamp) = %s
          AND EXTRACT(MONTH FROM timestamp) = %s
        ORDER BY timestamp ASC
    """, (anio, mes))
    return cur.fetchall()


def obtener_estadisticas_periodo(cur, anio: int, mes: int) -> dict:
    """Calcula estadísticas agregadas del período."""
    cur.execute("""
        SELECT
            COUNT(*)                                    AS total_transacciones,
            COALESCE(SUM(monto), 0)                    AS total_monto,
            COALESCE(AVG(monto), 0)                    AS monto_promedio,
            COUNT(CASE WHEN estado = 'APPROVED'  THEN 1 END) AS aprobadas,
            COUNT(CASE WHEN estado = 'REJECTED'  THEN 1 END) AS rechazadas,
            COUNT(CASE WHEN estado = 'REVERSED'  THEN 1 END) AS revertidas,
            COUNT(CASE WHEN moneda = 'USD'       THEN 1 END) AS en_usd,
            COUNT(CASE WHEN monto >= %s           THEN 1 END) AS sobre_umbral_cmf
        FROM auditoria.transacciones_procesadas
        WHERE EXTRACT(YEAR  FROM timestamp) = %s
          AND EXTRACT(MONTH FROM timestamp) = %s
    """, (UMBRAL_CMF_CLP, anio, mes))
    row = cur.fetchone()
    columnas = [desc[0] for desc in cur.description]
    return dict(zip(columnas, row))


def obtener_alertas_regulatorias(cur, anio: int, mes: int) -> list:
    """
    Obtiene transacciones que deben reportarse a la CMF:
    - Montos >= 10M CLP (Ley 19.913)
    - Clientes con KYC BLOCKED que tuvieron actividad
    - Transacciones en estado REJECTED de alto monto
    """
    cur.execute("""
        SELECT
            t.event_id,
            t.cliente_id,
            t.monto,
            t.moneda,
            t.estado,
            t.timestamp,
            c.kyc_status,
            CASE
                WHEN t.monto >= %s           THEN 'UMBRAL_CMF'
                WHEN c.kyc_status = 'BLOCKED' THEN 'CLIENTE_BLOQUEADO'
                WHEN t.estado = 'REJECTED'
                 AND t.monto >= 1000000       THEN 'RECHAZO_ALTO_MONTO'
                ELSE 'OTRO'
            END AS tipo_alerta
        FROM auditoria.transacciones_procesadas t
        LEFT JOIN auditoria.clientes c ON t.cliente_id = c.cliente_id
        WHERE EXTRACT(YEAR  FROM t.timestamp) = %s
          AND EXTRACT(MONTH FROM t.timestamp) = %s
          AND (
              t.monto >= %s
              OR c.kyc_status = 'BLOCKED'
              OR (t.estado = 'REJECTED' AND t.monto >= 1000000)
          )
        ORDER BY t.monto DESC
    """, (UMBRAL_CMF_CLP, anio, mes, UMBRAL_CMF_CLP))
    columnas = [desc[0] for desc in cur.description]
    return [dict(zip(columnas, row)) for row in cur.fetchall()]


#3. Funciones de generación del reporte

def serializar_decimal(obj):
    """Convierte Decimal a float para serialización JSON."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Tipo no serializable: {type(obj)}")


def construir_reporte(anio: int, mes: int, stats: dict,
                      transacciones: list, alertas: list) -> dict:
    """Construye el diccionario completo del reporte regulatorio."""
    periodo = f"{anio}-{mes:02d}"
    reporte_id = f"RPT-{periodo}-{datetime.now().strftime('%H%M%S')}"

    reporte = {
        "reporte_id":    reporte_id,
        "periodo":       periodo,
        "generado_en":   datetime.now().isoformat(),
        "generado_por":  "Sistema Auditoría Fintech v1.0",
        "normativa":     ["CMF Chile", "Ley 19.913", "Ley 19.628", "Ley 21.719"],
        "estadisticas":  {
            "total_transacciones":  int(stats["total_transacciones"]),
            "total_monto_clp":      float(stats["total_monto"]),
            "monto_promedio":       float(stats["monto_promedio"]),
            "aprobadas":            int(stats["aprobadas"]),
            "rechazadas":           int(stats["rechazadas"]),
            "revertidas":           int(stats["revertidas"]),
            "en_usd":               int(stats["en_usd"]),
            "sobre_umbral_cmf":     int(stats["sobre_umbral_cmf"]),
        },
        "total_alertas_regulatorias": len(alertas),
        "alertas_regulatorias": [
            {
                "event_id":   str(a["event_id"]),
                "cliente_id": str(a["cliente_id"]),
                "monto":      float(a["monto"]),
                "moneda":     str(a["moneda"]),
                "estado":     str(a["estado"]),
                "timestamp":  a["timestamp"].isoformat() if isinstance(a["timestamp"], datetime) else str(a["timestamp"]),
                "tipo_alerta": str(a["tipo_alerta"]),
            }
            for a in alertas
        ],
    }

    # Sellar el reporte con hash SHA-256
    contenido_json = json.dumps(reporte, sort_keys=True, default=serializar_decimal)
    reporte["hash_integridad"] = hashlib.sha256(
        contenido_json.encode("utf-8")
    ).hexdigest()

    return reporte


def persistir_reporte(cur, reporte: dict):
    """Guarda el reporte en PostgreSQL de forma inmutable."""
    cur.execute("""
        INSERT INTO auditoria.reportes_regulatorios
            (reporte_id, periodo, total_transacciones,
             total_monto_clp, total_alertas, hash_integridad,
             fecha_generacion, contenido_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (reporte_id) DO NOTHING
    """, (
        reporte["reporte_id"],
        reporte["periodo"],
        reporte["estadisticas"]["total_transacciones"],
        reporte["estadisticas"]["total_monto_clp"],
        reporte["total_alertas_regulatorias"],
        reporte["hash_integridad"],
        datetime.now(),
        json.dumps(reporte, default=serializar_decimal),
    ))


def exportar_json(reporte: dict, anio: int, mes: int):
    """Exporta el reporte como archivo JSON."""
    ruta_dir = os.getenv("REPORTES_OUTPUT_PATH", "/data/reportes")
    os.makedirs(ruta_dir, exist_ok=True)
    ruta = f"{ruta_dir}/reporte_{anio}_{mes:02d}.json"
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(reporte, f, ensure_ascii=False, indent=2, default=serializar_decimal)
    log.info(f"Reporte exportado: {ruta}")
    return ruta


# MAIN
def main():
    # Recibir año y mes como argumentos (ej: python reporte_regulatorio.py 2025 6)
    if len(sys.argv) < 3:
        anio = datetime.now().year
        mes  = datetime.now().month
        log.info(f"Sin argumentos: usando período actual {anio}-{mes:02d}")
    else:
        anio = int(sys.argv[1])
        mes  = int(sys.argv[2])

    periodo = f"{anio}-{mes:02d}"
    log.info("=" * 55)
    log.info(f"  GENERANDO REPORTE REGULATORIO — {periodo}")
    log.info("=" * 55)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor()
        log.info("Conexión a PostgreSQL exitosa.")

        log.info("Consultando estadísticas del período...")
        stats         = obtener_estadisticas_periodo(cur, anio, mes)
        transacciones = obtener_transacciones_periodo(cur, anio, mes)
        alertas       = obtener_alertas_regulatorias(cur, anio, mes)

        log.info(f"Total transacciones: {stats['total_transacciones']}")
        log.info(f"Total alertas CMF:   {len(alertas)}")

        log.info("Construyendo reporte...")
        reporte = construir_reporte(anio, mes, stats, transacciones, alertas)

        log.info("Persistiendo reporte en PostgreSQL (inmutable)...")
        persistir_reporte(cur, reporte)
        conn.commit()

        log.info("Exportando JSON...")
        ruta_json = exportar_json(reporte, anio, mes)

        cur.close()
        conn.close()

        log.info("=" * 55)
        log.info(f"  REPORTE GENERADO EXITOSAMENTE")
        log.info(f"  ID:              {reporte['reporte_id']}")
        log.info(f"  Período:         {periodo}")
        log.info(f"  Transacciones:   {stats['total_transacciones']}")
        log.info(f"  Alertas CMF:     {len(alertas)}")
        log.info(f"  Hash SHA-256:    {reporte['hash_integridad'][:32]}...")
        log.info(f"  Archivo:         {ruta_json}")
        log.info("=" * 55)

    except Exception as e:
        log.error(f"ERROR al generar el reporte: {e}")
        raise


if __name__ == "__main__":
    main()