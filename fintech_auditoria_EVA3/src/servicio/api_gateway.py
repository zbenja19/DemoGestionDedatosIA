"""
api_gateway.py

API Gateway REST de solo lectura sobre PostgreSQL.
Expone endpoints para transacciones, cuentas, reportes y alertas.
"""

import os
import logging
from datetime import datetime
from typing import Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

#1. Configuración de la base de datos y logging
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://auditoria_user:auditoria_pass@postgres:5432/fintech_auditoria"
)
API_VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [API] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

#2. Inicialización de FastAPI y CORS
app = FastAPI(
    title="API Gateway — Sistema de Auditoría Fintech",
    description=(
        "API REST de solo lectura para consultar transacciones, "
        "reportes regulatorios, alertas y predicciones del modelo IA. "
        "Cumple con normativa CMF, Ley 19.628 y Ley 21.719."
    ),
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Pool de conexiones (se crea al iniciar la app)
db_pool = None


@app.on_event("startup")
async def startup():
    global db_pool
    log.info("Conectando al pool de PostgreSQL...")
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    log.info("Pool de conexiones creado.")


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()
        log.info("Pool de conexiones cerrado.")


#3. Endpoints de la API
@app.get("/health", tags=["Sistema"])
async def health_check():
    """Verifica el estado del servicio y la conexión a PostgreSQL."""
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {
            "status":    "healthy",
            "version":   API_VERSION,
            "timestamp": datetime.now().isoformat(),
            "database":  "connected",
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Base de datos no disponible: {e}")


@app.get("/transacciones", tags=["Transacciones"])
async def listar_transacciones(
    cuenta_id:  Optional[str]  = Query(None, description="Filtrar por cuenta"),
    estado:     Optional[str]  = Query(None, description="APPROVED | REJECTED | REVERSED | PENDING"),
    fecha_desde: Optional[str] = Query(None, description="Formato: YYYY-MM-DD"),
    fecha_hasta: Optional[str] = Query(None, description="Formato: YYYY-MM-DD"),
    limite:     int            = Query(50,   ge=1, le=500),
    offset:     int            = Query(0,    ge=0),
):
    """Lista transacciones con filtros opcionales. Máximo 500 por consulta."""
    condiciones = ["1=1"]
    params      = []
    i           = 1

    if cuenta_id:
        condiciones.append(f"cuenta_id = ${i}")
        params.append(cuenta_id)
        i += 1
    if estado:
        condiciones.append(f"estado = ${i}")
        params.append(estado.upper())
        i += 1
    if fecha_desde:
        condiciones.append(f"timestamp >= ${i}::date")
        params.append(fecha_desde)
        i += 1
    if fecha_hasta:
        condiciones.append(f"timestamp < (${i}::date + interval '1 day')")
        params.append(fecha_hasta)
        i += 1

    where = " AND ".join(condiciones)
    query = f"""
        SELECT event_id, cliente_id, cuenta_id, monto, moneda,
               estado, timestamp, hash_evento
        FROM auditoria.transacciones_procesadas
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT ${i} OFFSET ${i+1}
    """
    params += [limite, offset]

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return {
        "total":         len(rows),
        "limite":        limite,
        "offset":        offset,
        "transacciones": [dict(r) for r in rows],
    }


@app.get("/transacciones/{event_id}", tags=["Transacciones"])
async def detalle_transaccion(event_id: str):
    """Retorna el detalle completo de una transacción por su event_id."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT t.*, c.nombre, c.kyc_status, cu.tipo_cuenta, cu.saldo
            FROM auditoria.transacciones_procesadas t
            LEFT JOIN auditoria.clientes c  ON t.cliente_id = c.cliente_id
            LEFT JOIN auditoria.cuentas  cu ON t.cuenta_id  = cu.cuenta_id
            WHERE t.event_id = $1
        """, event_id)

    if not row:
        raise HTTPException(status_code=404, detail=f"Transacción '{event_id}' no encontrada.")
    return dict(row)


@app.get("/cuentas/{cuenta_id}/estadisticas", tags=["Cuentas"])
async def estadisticas_cuenta(cuenta_id: str):
    """Estadísticas agregadas de una cuenta: totales, promedios y alertas."""
    async with db_pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*)                                        AS total_transacciones,
                COALESCE(SUM(monto), 0)                        AS monto_total,
                COALESCE(AVG(monto), 0)                        AS monto_promedio,
                COALESCE(MAX(monto), 0)                        AS monto_maximo,
                COUNT(CASE WHEN estado = 'APPROVED'  THEN 1 END) AS aprobadas,
                COUNT(CASE WHEN estado = 'REJECTED'  THEN 1 END) AS rechazadas,
                COUNT(CASE WHEN estado = 'REVERSED'  THEN 1 END) AS revertidas,
                MIN(timestamp)                                  AS primera_transaccion,
                MAX(timestamp)                                  AS ultima_transaccion
            FROM auditoria.transacciones_procesadas
            WHERE cuenta_id = $1
        """, cuenta_id)

        cuenta = await conn.fetchrow("""
            SELECT cu.cuenta_id, cu.tipo_cuenta, cu.saldo, c.nombre, c.kyc_status
            FROM auditoria.cuentas cu
            LEFT JOIN auditoria.clientes c ON cu.cliente_id = c.cliente_id
            WHERE cu.cuenta_id = $1
        """, cuenta_id)

    if not cuenta:
        raise HTTPException(status_code=404, detail=f"Cuenta '{cuenta_id}' no encontrada.")

    return {
        "cuenta":       dict(cuenta),
        "estadisticas": dict(stats),
    }


@app.get("/reportes", tags=["Reportes Regulatorios"])
async def listar_reportes(limite: int = Query(12, ge=1, le=50)):
    """Lista los últimos reportes regulatorios generados con su estado de integridad."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                reporte_id, periodo, total_transacciones,
                total_monto_clp, total_alertas, hash_integridad,
                fecha_generacion,
                CASE
                    WHEN hash_integridad IS NOT NULL
                     AND LENGTH(hash_integridad) = 64 THEN 'ÍNTEGRO'
                    ELSE 'COMPROMETIDO'
                END AS estado_integridad
            FROM auditoria.reportes_regulatorios
            ORDER BY fecha_generacion DESC
            LIMIT $1
        """, limite)
    return {"total": len(rows), "reportes": [dict(r) for r in rows]}


@app.get("/reportes/{reporte_id}", tags=["Reportes Regulatorios"])
async def detalle_reporte(reporte_id: str):
    """Retorna el detalle completo de un reporte regulatorio."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM auditoria.reportes_regulatorios
            WHERE reporte_id = $1
        """, reporte_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Reporte '{reporte_id}' no encontrado.")
    return dict(row)


@app.get("/alertas", tags=["Alertas"])
async def listar_alertas(
    limite: int = Query(50, ge=1, le=200)
):
    """Lista transacciones que superan el umbral CMF (>= 10M CLP) o son de clientes BLOCKED."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                t.event_id, t.cliente_id, t.cuenta_id,
                t.monto, t.moneda, t.estado, t.timestamp,
                c.kyc_status,
                CASE
                    WHEN t.monto >= 10000000      THEN 'UMBRAL_CMF'
                    WHEN c.kyc_status = 'BLOCKED' THEN 'CLIENTE_BLOQUEADO'
                    ELSE 'RECHAZO_ALTO_MONTO'
                END AS tipo_alerta
            FROM auditoria.transacciones_procesadas t
            LEFT JOIN auditoria.clientes c ON t.cliente_id = c.cliente_id
            WHERE t.monto >= 10000000
               OR c.kyc_status = 'BLOCKED'
               OR (t.estado = 'REJECTED' AND t.monto >= 1000000)
            ORDER BY t.monto DESC
            LIMIT $1
        """, limite)
    return {"total": len(rows), "alertas": [dict(r) for r in rows]}


@app.get("/predicciones", tags=["Modelo IA"])
async def listar_predicciones(
    solo_sospechosas: bool = Query(False, description="Filtrar solo predicciones = 1"),
    limite:           int  = Query(50, ge=1, le=200),
):
    """Lista predicciones del modelo de detección de transacciones sospechosas."""
    condicion = "WHERE prediccion_binaria = 1" if solo_sospechosas else ""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT id, transaction_id, probabilidad_sospechosa,
                   prediccion_binaria, timestamp_prediccion
            FROM auditoria.predicciones_modelo
            {condicion}
            ORDER BY probabilidad_sospechosa DESC
            LIMIT $1
        """, limite)
    return {"total": len(rows), "predicciones": [dict(r) for r in rows]}


@app.get("/modelo/resumen", tags=["Modelo IA"])
async def resumen_modelo():
    """Retorna las métricas de resumen del modelo desde la vista de Metabase."""
    async with db_pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT * FROM auditoria.vista_resumen_modelo"
            )
            return dict(row) if row else {"mensaje": "Vista no disponible aún. Ejecuta setup_metabase.py"}
        except Exception:
            return {"mensaje": "Vista no disponible aún. Ejecuta setup_metabase.py"}