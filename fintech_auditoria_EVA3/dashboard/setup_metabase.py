"""
setup_metabase.py: Crea las vistas SQL en PostgreSQL que alimentan el dashboard de Metabase.
Agrega el servicio Metabase al docker-compose.yml.
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


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


#1. Vistas de PostgreSQL para el dashboard de Metabase.
VISTAS = {

    # Panel 1 — Resumen general del modelo
    "vista_resumen_modelo": """
        CREATE OR REPLACE VIEW auditoria.vista_resumen_modelo AS
        SELECT
            COUNT(*)                                            AS total_evaluadas,
            SUM(prediccion_binaria)                            AS total_alertas,
            ROUND(
                AVG(CASE WHEN prediccion_binaria = 1 THEN 1.0 ELSE 0.0 END) * 100, 2
            )                                                   AS tasa_alerta_pct,
            ROUND(
                SUM(CASE WHEN prediccion_binaria = 0 AND probabilidad_sospechosa > 0.3
                         THEN 1 ELSE 0 END)::NUMERIC
                / NULLIF(COUNT(*), 0) * 100, 2
            )                                                   AS tasa_falso_positivo_pct,
            MIN(timestamp_prediccion)                          AS primera_prediccion,
            MAX(timestamp_prediccion)                          AS ultima_prediccion
        FROM auditoria.predicciones_modelo;
    """,

    # Panel 2 — Distribución diaria de predicciones
    "vista_distribucion_diaria": """
        CREATE OR REPLACE VIEW auditoria.vista_distribucion_diaria AS
        SELECT
            DATE(timestamp_prediccion)  AS fecha,
            SUM(prediccion_binaria)     AS sospechosas,
            SUM(1 - prediccion_binaria) AS legitimas,
            COUNT(*)                    AS total
        FROM auditoria.predicciones_modelo
        GROUP BY DATE(timestamp_prediccion)
        ORDER BY fecha DESC;
    """,

    # Panel 3 — Alertas por rango de probabilidad
    "vista_alertas_por_rango": """
        CREATE OR REPLACE VIEW auditoria.vista_alertas_por_rango AS
        SELECT
            CASE
                WHEN probabilidad_sospechosa >= 0.9 THEN '90-100% (Crítico)'
                WHEN probabilidad_sospechosa >= 0.7 THEN '70-90%  (Alto)'
                WHEN probabilidad_sospechosa >= 0.5 THEN '50-70%  (Medio)'
                ELSE                                     '0-50%   (Bajo)'
            END                        AS rango_riesgo,
            COUNT(*)                   AS cantidad,
            ROUND(AVG(probabilidad_sospechosa) * 100, 1) AS prob_promedio_pct
        FROM auditoria.predicciones_modelo
        WHERE prediccion_binaria = 1
        GROUP BY rango_riesgo
        ORDER BY prob_promedio_pct DESC;
    """,

    # Panel 4 — Tendencia mensual de alertas
    "vista_tendencia_mensual": """
        CREATE OR REPLACE VIEW auditoria.vista_tendencia_mensual AS
        SELECT
            TO_CHAR(timestamp_prediccion, 'YYYY-MM')    AS mes,
            COUNT(*)                                     AS total_evaluadas,
            SUM(prediccion_binaria)                      AS alertas,
            ROUND(
                SUM(prediccion_binaria)::NUMERIC
                / NULLIF(COUNT(*), 0) * 100, 2
            )                                            AS tasa_alerta_pct
        FROM auditoria.predicciones_modelo
        GROUP BY TO_CHAR(timestamp_prediccion, 'YYYY-MM')
        ORDER BY mes DESC;
    """,

    # Panel 5 — Estado de integridad de los últimos 12 reportes regulatorios
    "vista_integridad_reportes": """
        CREATE OR REPLACE VIEW auditoria.vista_integridad_reportes AS
        SELECT
            reporte_id,
            periodo,
            fecha_generacion,
            hash_integridad,
            CASE
                WHEN hash_integridad IS NOT NULL
                 AND LENGTH(hash_integridad) = 64 THEN 'OK ✓'
                ELSE 'ALERTA ✗'
            END AS estado_integridad
        FROM auditoria.reportes_regulatorios
        ORDER BY fecha_generacion DESC
        LIMIT 12;
    """,
}

# Rol de solo lectura para Metabase
SQL_ROLES = """
    DO $$
    BEGIN
        -- Crear rol metabase_reader si no existe
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metabase_reader') THEN
            CREATE ROLE metabase_reader WITH LOGIN PASSWORD 'metabase_readonly_2025';
        END IF;

        -- Crear rol modelo_ia si no existe
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'modelo_ia') THEN
            CREATE ROLE modelo_ia WITH LOGIN PASSWORD 'modelo_ia_2025';
        END IF;
    END
    $$;

    -- Permisos metabase_reader: solo SELECT en vistas y reportes
    GRANT CONNECT ON DATABASE fintech_auditoria TO metabase_reader;
    GRANT USAGE   ON SCHEMA auditoria           TO metabase_reader;
    GRANT SELECT  ON auditoria.vista_resumen_modelo      TO metabase_reader;
    GRANT SELECT  ON auditoria.vista_distribucion_diaria TO metabase_reader;
    GRANT SELECT  ON auditoria.vista_alertas_por_rango   TO metabase_reader;
    GRANT SELECT  ON auditoria.vista_tendencia_mensual   TO metabase_reader;
    GRANT SELECT  ON auditoria.vista_integridad_reportes TO metabase_reader;

    -- Permisos modelo_ia: lectura datos + escritura predicciones
    GRANT CONNECT ON DATABASE fintech_auditoria TO modelo_ia;
    GRANT USAGE   ON SCHEMA auditoria           TO modelo_ia;
    GRANT SELECT  ON auditoria.transacciones_procesadas TO modelo_ia;
    GRANT SELECT  ON auditoria.clientes                 TO modelo_ia;
    GRANT SELECT  ON auditoria.cuentas                  TO modelo_ia;
    GRANT INSERT  ON auditoria.predicciones_modelo      TO modelo_ia;
    GRANT USAGE, SELECT ON SEQUENCE auditoria.predicciones_modelo_id_seq TO modelo_ia;

    -- Revocar permisos peligrosos de metabase_reader
    REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auditoria FROM metabase_reader;
"""

#2. Bloque de configuración de Metabase para docker-compose.yml
BLOQUE_METABASE = """
  # Metabase — Dashboard BI interactivo (EV3)
  metabase:
    image: metabase/metabase:latest
    container_name: auditoria_metabase
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "3000:3000"
    environment:
      MB_DB_TYPE: postgres
      MB_DB_DBNAME: fintech_auditoria
      MB_DB_PORT: 5432
      MB_DB_USER: metabase_reader
      MB_DB_PASS: metabase_readonly_2025
      MB_DB_HOST: postgres
      JAVA_TIMEZONE: America/Santiago
    networks:
      - auditoria_net
    restart: unless-stopped
"""


#3. Funciones principales del script
def crear_vistas():
    """Crea todas las vistas en PostgreSQL."""
    log("Conectando a PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()

        for nombre, sql in VISTAS.items():
            try:
                cur.execute(sql)
                log(f"  ✓ Vista creada: {nombre}")
            except Exception as e:
                log(f"  ✗ Error en {nombre}: {e}")

        log("Creando roles y permisos...")
        try:
            cur.execute(SQL_ROLES)
            log("  ✓ Roles metabase_reader y modelo_ia configurados.")
        except Exception as e:
            log(f"  ✗ Error en roles: {e}")

        cur.close()
        conn.close()
        log("Vistas y roles creados exitosamente.")
    except Exception as e:
        log(f"ERROR de conexión a PostgreSQL: {e}")
        log("Verifica que PostgreSQL esté corriendo con: docker compose ps")


def agregar_metabase_a_compose():
    """Inserta el servicio Metabase en docker-compose.yml si no existe."""
    ruta = "docker-compose.yml"
    if not os.path.exists(ruta):
        log(f"No se encontró {ruta}. Ejecuta este script desde la raíz del proyecto.")
        return

    with open(ruta, "r", encoding="utf-8") as f:
        contenido = f.read()

    if "metabase" in contenido:
        log("El servicio Metabase ya existe en docker-compose.yml.")
        return

    # Insertar antes de la sección "networks:"
    if "networks:" in contenido:
        contenido = contenido.replace("networks:", BLOQUE_METABASE + "\nnetworks:")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(contenido)
        log("✓ Servicio Metabase agregado a docker-compose.yml")
    else:
        log("No se pudo insertar Metabase automáticamente. Agrega el bloque manualmente.")
        print(BLOQUE_METABASE)


def main():
    log("=" * 55)
    log("  SETUP — Dashboard Metabase + Vistas PostgreSQL")
    log("=" * 55)

    log("1. Creando vistas SQL en PostgreSQL...")
    crear_vistas()

    log("2. Actualizando docker-compose.yml con Metabase...")
    agregar_metabase_a_compose()

    log("")
    log("=" * 55)
    log("  SIGUIENTE PASO:")
    log("  docker compose up -d metabase")
    log("  Luego abrir: http://localhost:3000")
    log("  Usuario inicial de Metabase: configúralo al abrir")
    log("  Conexión a PostgreSQL en Metabase:")
    log("    Host:     postgres")
    log("    Puerto:   5432")
    log("    DB:       fintech_auditoria")
    log("    Usuario:  metabase_reader")
    log("    Password: metabase_readonly_2025")
    log("=" * 55)


if __name__ == "__main__":
    main()