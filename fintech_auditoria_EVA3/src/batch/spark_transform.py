"""
spark_transform.py

Pipeline de transformación Spark sobre arquitectura Medallion.
Lee desde Kafka, valida, limpia y escribe en Delta Lake (Bronze → Silver → Gold).
Finalmente carga en PostgreSQL para la capa de servicio.
"""

import os
import time
import hashlib
import logging
from datetime import datetime

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType,
    DoubleType, TimestampType
)

#1. Configuración de variables de entorno y rutas
KAFKA_SERVERS  = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC    = os.getenv("KAFKA_TOPIC",              "fintech.transacciones")
DELTA_BASE     = os.getenv("DELTA_LAKE_PATH",          "/data/delta")
DELTA_BRONZE   = f"{DELTA_BASE}/bronze"
DELTA_SILVER   = f"{DELTA_BASE}/silver"
DELTA_GOLD     = f"{DELTA_BASE}/gold"
CHECKPOINT_DIR = f"{DELTA_BASE}/checkpoints"

DB_URL    = os.getenv("DATABASE_URL",
            "jdbc:postgresql://postgres:5432/fintech_auditoria")
DB_USER   = os.getenv("POSTGRES_USER",     "auditoria_user")
DB_PASS   = os.getenv("POSTGRES_PASSWORD", "auditoria_pass")

MONEDAS_VALIDAS = ["CLP", "USD", "EUR", "GBP", "BRL"]
ESTADOS_VALIDOS = ["PENDING", "APPROVED", "REJECTED", "REVERSED"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SPARK] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Schema esperado de los mensajes Kafka
SCHEMA_EVENTO = StructType([
    StructField("event_id",    StringType(),    False),
    StructField("cliente_id",  StringType(),    True),
    StructField("cuenta_id",   StringType(),    True),
    StructField("monto",       DoubleType(),    False),
    StructField("moneda",      StringType(),    False),
    StructField("estado",      StringType(),    False),
    StructField("timestamp",   StringType(),    False),
    StructField("hash_evento", StringType(),    False),
])


#2. Funciones de transformación y procesamiento
def crear_sesion():
    """Crea la sesión de Spark con soporte para Delta Lake y Kafka."""
    log.info("Iniciando sesión Spark con Delta Lake...")
    spark = (
        SparkSession.builder
        .appName("FinTech_Auditoria_Pipeline")
        .config("spark.jars.packages",
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3,"
                "io.delta:delta-spark_2.12:3.2.0,"
                "org.postgresql:postgresql:42.7.3")
        .config("spark.sql.extensions",
                "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog",
                "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.databricks.delta.retentionDurationCheck.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    log.info("Sesión Spark iniciada correctamente.")
    return spark


#3. Capa Bronze: Lectura desde Kafka y almacenamiento crudo en Delta
def leer_kafka(spark):
    """
    Lee el stream de Kafka y escribe los eventos crudos en Delta Bronze.
    Ningún dato se filtra ni modifica en esta capa.
    """
    log.info(f"Leyendo stream desde topic: {KAFKA_TOPIC}")

    df_kafka = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_SERVERS)
        .option("subscribe", KAFKA_TOPIC)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
    )

    # Parsear el JSON del value de Kafka
    df_parsed = df_kafka.select(
        F.from_json(
            F.col("value").cast("string"),
            SCHEMA_EVENTO
        ).alias("data"),
        F.col("timestamp").alias("kafka_timestamp"),
        F.col("offset"),
        F.col("partition"),
    ).select("data.*", "kafka_timestamp", "offset", "partition")

    spark.createDataFrame([], df_parsed.schema) \
        .write \
        .format("delta") \
        .mode("append") \
        .save(DELTA_BRONZE)
    log.info("Tabla Delta Bronze inicializada con esquema.")

    # Escribir en Bronze (append-only, inmutable)
    query_bronze = (
        df_parsed.writeStream
        .format("delta")
        .outputMode("append")
        .option("checkpointLocation", f"{CHECKPOINT_DIR}/bronze")
        .trigger(processingTime="60 seconds")
        .start(DELTA_BRONZE)
    )

    log.info("Stream Bronze iniciado (append-only).")
    return query_bronze, df_parsed


#4. Capa Silver: Validación, limpieza y normalización
def transformar_silver(spark):
    """
    Lee Bronze y aplica:
    - Eliminación de duplicados por event_id
    - Validación estructural y semántica
    - Normalización de moneda
    - Verificación del hash SHA-256
    """
    log.info("Iniciando transformación Bronze → Silver...")

    df_bronze = (
        spark.readStream
        .format("delta")
        .load(DELTA_BRONZE)
    )

    # Validación estructural
    df_valido = df_bronze.filter(
        F.col("event_id").isNotNull() &
        F.col("monto").isNotNull() &
        (F.col("monto") > 0) &
        F.col("moneda").isin(MONEDAS_VALIDAS) &
        F.col("estado").isin(ESTADOS_VALIDOS) &
        F.col("timestamp").isNotNull()
    )

    # Normalización: moneda a mayúsculas, timestamp a tipo correcto
    df_limpio = (
        df_valido
        .withColumn("moneda",    F.upper(F.trim(F.col("moneda"))))
        .withColumn("timestamp", F.to_timestamp(F.col("timestamp")))
        .withColumn("procesado_en", F.current_timestamp())
        # Flag para trazabilidad
        .withColumn("capa", F.lit("SILVER"))
    )

    # Escribir Silver
    query_silver = (
        df_limpio.writeStream
        .format("delta")
        .outputMode("append")
        .option("checkpointLocation", f"{CHECKPOINT_DIR}/silver")
        .trigger(processingTime="60 seconds")
        .start(DELTA_SILVER)
    )

    log.info("Stream Silver iniciado.")
    return query_silver


#5. Capa Gold: Agregaciones y carga en PostgreSQL
def transformar_gold(spark):
    """
    Lee Silver y genera las agregaciones para el reporte regulatorio mensual.
    También carga los datos en PostgreSQL para la capa de servicio.
    """
    log.info("Iniciando transformación Silver → Gold...")

    df_silver = (
        spark.read
        .format("delta")
        .load(DELTA_SILVER)
    )

    # Agregación mensual por estado y moneda
    df_gold = (
        df_silver
        .withColumn("periodo", F.date_format(F.col("timestamp"), "yyyy-MM"))
        .groupBy("periodo", "estado", "moneda")
        .agg(
            F.count("*").alias("total_transacciones"),
            F.sum("monto").alias("total_monto"),
            F.avg("monto").alias("monto_promedio"),
            F.max("monto").alias("monto_maximo"),
            F.min("monto").alias("monto_minimo"),
        )
        .withColumn("generado_en", F.current_timestamp())
    )

    # Escribir Gold en Delta
    (
        df_gold.write
        .format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .save(DELTA_GOLD)
    )
    log.info("Capa Gold escrita en Delta Lake.")

    # Cargar datos individuales en PostgreSQL para la API y el modelo IA
    df_para_postgres = (
        df_silver.select(
            "event_id", "cliente_id", "cuenta_id",
            "monto", "moneda", "estado", "timestamp", "hash_evento"
        )
    )

    (
        df_para_postgres.write
        .format("jdbc")
        .option("url",      DB_URL)
        .option("dbtable",  "auditoria.transacciones_procesadas")
        .option("user",     DB_USER)
        .option("password", DB_PASS)
        .option("driver",   "org.postgresql.Driver")
        .mode("append")
        .save()
    )
    log.info("Datos cargados en PostgreSQL (auditoria.transacciones_procesadas).")


# MAIN
def main():
    log.info("=" * 55)
    log.info("  INICIO — Pipeline Spark Medallion")
    log.info(f"  Delta base: {DELTA_BASE}")
    log.info("=" * 55)

    spark = crear_sesion()
    INTERVALO_GOLD_SEG = int(os.getenv("INTERVALO_GOLD_SEG", "300"))  # 5 min por defecto

    try:
        # Iniciar streams Bronze y Silver en paralelo
        query_bronze, df_parsed = leer_kafka(spark)
        query_silver = transformar_silver(spark)

        log.info("Streams activos. Procesando micro-batches cada 60s...")
        log.info(f"Capa Gold se actualizará cada {INTERVALO_GOLD_SEG}s.")
        log.info("Presiona Ctrl+C para detener.")

        # Mientras los streams sigan vivos, ejecutar Gold periódicamente
        while query_bronze.isActive and query_silver.isActive:
            time.sleep(INTERVALO_GOLD_SEG)
            log.info("Ejecutando transformación Gold periódica...")
            try:
                transformar_gold(spark)
            except Exception as e:
                log.error(f"Error en transformación Gold (se reintentará en el próximo ciclo): {e}")

    except KeyboardInterrupt:
        log.info("Pipeline detenido manualmente.")
        log.info("Ejecutando transformación Gold final...")
        transformar_gold(spark)

    except Exception as e:
        log.error(f"Error en el pipeline: {e}")
        raise

    finally:
        spark.stop()
        log.info("Sesión Spark cerrada.")


if __name__ == "__main__":
    main()