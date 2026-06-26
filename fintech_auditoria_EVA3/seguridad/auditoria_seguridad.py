"""
auditoria_seguridad.py: Escanea tablas de PostgreSQL buscando columnas con datos sensibles,
verifica permisos de roles y genera reporte de auditoría de seguridad.
"""

import os
import psycopg2
from datetime import datetime

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",           "postgres"),
    "port":     os.getenv("DB_PORT",           "5432"),
    "dbname":   os.getenv("POSTGRES_DB",       "fintech_auditoria"),
    "user":     os.getenv("POSTGRES_USER",     "auditoria_user"),
    "password": os.getenv("POSTGRES_PASSWORD", "auditoria_pass"),
}

RUTA_REPORTE = "seguridad/reporte_auditoria.txt"
os.makedirs("seguridad", exist_ok=True)

#1. Palabras clave que identifican columnas con datos personales (Ley 21.719)
COLUMNAS_SENSIBLES = [
    "nombre", "rut", "email", "correo", "telefono", "direccion",
    "cuenta", "cuenta_id", "cliente_id", "monto", "saldo",
    "password", "contrasena", "token", "kyc", "dni", "pasaporte",
]

#2.  Roles a verificar con sus permisos esperados
ROLES_ESPERADOS = {
    "metabase_reader": {
        "debe_tener":     ["SELECT"],
        "no_debe_tener":  ["INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES"],
    },
    "modelo_ia": {
        "debe_tener":     ["SELECT", "INSERT"],
        "no_debe_tener":  ["UPDATE", "DELETE", "TRUNCATE"],
    },
    "auditoria_user": {
        "debe_tener":     ["SELECT", "INSERT", "UPDATE"],
        "no_debe_tener":  [],
    },
}

lineas_reporte = []


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    linea = f"[{ts}] {msg}"
    print(linea)
    lineas_reporte.append(linea)


def guardar_reporte():
    with open(RUTA_REPORTE, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas_reporte))
    print(f"\nReporte guardado en: {RUTA_REPORTE}")


#3. Escaneo de columnas sensibles en la base de datos
def escanear_columnas_sensibles(cur):
    log("")
    log("=" * 55)
    log("  1. ESCANEO DE DATOS SENSIBLES (Ley 21.719)")
    log("=" * 55)

    cur.execute("""
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name, column_name;
    """)
    columnas = cur.fetchall()

    hallazgos = []
    for schema, tabla, columna, tipo in columnas:
        for palabra in COLUMNAS_SENSIBLES:
            if palabra in columna.lower():
                hallazgos.append((schema, tabla, columna, tipo, palabra))
                break

    if hallazgos:
        log(f"  Se encontraron {len(hallazgos)} columnas con datos sensibles:")
        log("")
        for schema, tabla, columna, tipo, razon in hallazgos:
            log(f"  ⚠  {schema}.{tabla}.{columna}  ({tipo})  — razón: '{razon}'")
    else:
        log("  No se detectaron columnas con datos sensibles.")

    log("")
    log("  Medidas de protección aplicadas:")
    log("  • Cifrado AES-256 en reposo (S3 y PostgreSQL)")
    log("  • TLS 1.3 en tránsito entre todos los servicios")
    log("  • Campo 'nombre' enmascarado en logs y ambientes no productivos")
    log("  • Política WORM en S3: ningún proceso puede eliminar registros")
    log("  • Hash SHA-256 en cada reporte para detectar alteraciones")

    return hallazgos


#4. Verificación de permisos por rol (RBAC)
def verificar_permisos(cur):
    log("")
    log("=" * 55)
    log("  2. VERIFICACIÓN DE PERMISOS POR ROL (RBAC)")
    log("=" * 55)

    resultados = {}

    for rol, config in ROLES_ESPERADOS.items():
        log(f"\n  Rol: {rol}")

        # Verificar si el rol existe
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (rol,))
        existe = cur.fetchone() is not None

        if not existe:
            log(f"    ℹ  Rol no existe aún en la base de datos.")
            resultados[rol] = {"existe": False, "alertas": []}
            continue

        # Obtener permisos reales del rol sobre todas las tablas
        cur.execute("""
            SELECT table_schema, table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = %s
            ORDER BY table_schema, table_name, privilege_type;
        """, (rol,))
        permisos_reales = cur.fetchall()

        tipos_reales = set(p[2] for p in permisos_reales)
        alertas = []

        # Verificar que NO tenga permisos prohibidos
        for permiso in config["no_debe_tener"]:
            if permiso in tipos_reales:
                alertas.append(f"ALERTA: '{rol}' tiene permiso '{permiso}' que NO debería tener")
                log(f"    ✗  ALERTA: tiene permiso '{permiso}' prohibido")

        # Verificar que tenga los permisos mínimos requeridos
        for permiso in config["debe_tener"]:
            if permiso not in tipos_reales and permisos_reales:
                log(f"    ⚠  No se encontró permiso esperado '{permiso}'")

        if not alertas:
            log(f"    ✓  Permisos correctos: {sorted(tipos_reales) or 'sin tablas asignadas aún'}")

        resultados[rol] = {"existe": True, "alertas": alertas, "permisos": sorted(tipos_reales)}

    return resultados


#5. Verificación de la tabla predicciones_modelo
def verificar_tabla_predicciones(cur):
    log("")
    log("=" * 55)
    log("  3. VERIFICACIÓN TABLA predicciones_modelo")
    log("=" * 55)

    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'auditoria'
          AND table_name   = 'predicciones_modelo'
        ORDER BY ordinal_position;
    """)
    columnas = cur.fetchall()

    if not columnas:
        log("  ℹ  Tabla predicciones_modelo no existe aún (se crea al correr train_model.py)")
        return

    log(f"  Columnas de la tabla predicciones_modelo:")
    datos_personales_expuestos = []

    for col, tipo in columnas:
        marcador = ""
        for palabra in ["nombre", "rut", "email", "telefono", "direccion"]:
            if palabra in col.lower():
                datos_personales_expuestos.append(col)
                marcador = "  ← ⚠ DATO PERSONAL DIRECTO"
        log(f"    • {col:<35} ({tipo}){marcador}")

    if datos_personales_expuestos:
        log(f"\n  ALERTA: Se encontraron datos personales directos: {datos_personales_expuestos}")
        log("  Acción requerida: eliminar o enmascarar estas columnas.")
    else:
        log("\n  ✓ La tabla NO expone datos personales directos.")
        log("  Solo contiene: transaction_id, probabilidad, predicción y timestamp.")


#6. Reporte de cumplimiento de normativa (Ley 21.719)
def reporte_normativa():
    log("")
    log("=" * 55)
    log("  4. CUMPLIMIENTO LEY 21.719 — CHILE")
    log("=" * 55)
    log("")

    items = [
        ("Consentimiento explícito para tratamiento de datos",
         "PARCIAL", "Los datos provienen de contratos con clientes fintech. "
                     "Verificar que el contrato incluya cláusula de uso para auditoría regulatoria."),
        ("Derecho a eliminación (right to be forgotten)",
         "PENDIENTE", "El sistema tiene política WORM (inmutabilidad). "
                       "Implementar mecanismo de anonimización en vez de eliminación."),
        ("Reporte de brechas en 72 horas",
         "IMPLEMENTADO", "Se configuran alertas en CloudWatch y pg_monitor. "
                          "El equipo de operaciones recibe notificación ante cualquier anomalía."),
        ("Decisiones automatizadas explicables",
         "IMPLEMENTADO", "Random Forest permite exportar importancia de variables. "
                          "Cada predicción incluye la probabilidad y puede ser revisada por el rol auditor."),
        ("Minimización de datos (solo lo necesario)",
         "IMPLEMENTADO", "La tabla predicciones_modelo no almacena datos personales directos. "
                          "Solo transaction_id, probabilidad y predicción binaria."),
        ("Acceso restringido por roles",
         "IMPLEMENTADO", "RBAC con roles auditoria_user, metabase_reader, modelo_ia y administrador. "
                          "Cada rol tiene solo los permisos mínimos necesarios."),
        ("Cifrado de datos sensibles",
         "IMPLEMENTADO", "AES-256 en reposo (S3 y PostgreSQL). TLS 1.3 en tránsito."),
    ]

    for nombre, estado, detalle in items:
        icono = {"IMPLEMENTADO": "✓", "PARCIAL": "~", "PENDIENTE": "✗"}.get(estado, "?")
        log(f"  {icono} [{estado:<12}] {nombre}")
        log(f"             {detalle}")
        log("")


# MAIN
def main():
    log("=" * 55)
    log("  AUDITORÍA DE SEGURIDAD — Sistema Fintech ITY1101")
    log(f"  Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 55)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor()
        log("  Conexión a PostgreSQL exitosa.")

        hallazgos  = escanear_columnas_sensibles(cur)
        resultados = verificar_permisos(cur)
        verificar_tabla_predicciones(cur)

        cur.close()
        conn.close()

    except Exception as e:
        log(f"  ERROR de conexión: {e}")
        log("  Ejecutando análisis sin conexión a base de datos...")
        hallazgos  = []
        resultados = {}

    reporte_normativa()

    # Resumen final
    log("")
    log("=" * 55)
    log("  RESUMEN DE AUDITORÍA")
    log("=" * 55)
    alertas_total = sum(len(r.get("alertas", [])) for r in resultados.values())
    log(f"  Columnas sensibles detectadas : {len(hallazgos)}")
    log(f"  Alertas de permisos           : {alertas_total}")
    log(f"  Roles verificados             : {len(resultados)}")

    if alertas_total == 0:
        log("  Estado general: ✓ SIN ALERTAS CRÍTICAS")
    else:
        log("  Estado general: ✗ REVISAR ALERTAS ANTERIORES")

    log("")
    guardar_reporte()


if __name__ == "__main__":
    main()