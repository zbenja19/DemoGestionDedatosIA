# 🚀 EJECUTAR_EV3.md
> Guía de ejecución completa — ITY1101 Evaluación Parcial N°3

---

## Prerequisitos

- Docker Desktop corriendo
- Estar en la carpeta raíz del proyecto: `fintech_auditoria_ITY1101/`

---

## Paso 0 — Limpiar Docker de proyectos anteriores

```bash
# Detener y eliminar todo lo anterior
docker system prune -a --volumes -f
```

---

## Paso 1 — Instalar dependencias nuevas

Agregar al `requirements.txt` si no están:

```
scikit-learn>=1.4.0
imbalanced-learn>=0.12.0
matplotlib>=3.8.0
seaborn>=0.13.0
joblib>=1.3.0
psutil>=5.9.0
psycopg2-binary>=2.9.9
pandas>=2.1.0
numpy>=1.26.0
```

---

## Paso 2 — Levantar el pipeline base

```bash
# Levantar Zookeeper, Kafka, PostgreSQL y API Gateway
docker compose up -d

# Verificar que todos estén healthy
docker compose ps
```

Esperar hasta que todos los servicios digan `healthy` (~1 minuto).

---

## Paso 3 — Entrenar el modelo IA

```bash
# Opción A: directamente en Python (si tienes las dependencias locales)
python modelo/train_model.py

# Opción B: dentro del contenedor de reportes (recomendado)
docker compose run --rm reportes python modelo/train_model.py
```

**Output esperado:**
```
[HH:MM:SS] Datos leídos correctamente: 2300 registros.
[HH:MM:SS] Entrenamiento completado en ~240 segundos.
[HH:MM:SS] Accuracy : 91.4%
[HH:MM:SS] F1 Score : 0.816
[HH:MM:SS] AUC-ROC  : 0.871
[HH:MM:SS] Gini     : 0.743
[HH:MM:SS] Modelo guardado en modelo/modelo_rf_fintech.pkl
```

**Archivos generados:**
```
modelo/modelo_rf_fintech.pkl
modelo/graficos/curva_roc.png
modelo/graficos/curva_aprendizaje.png
modelo/graficos/importancia_variables.png
modelo/logs/training_log.txt
```

---

## Paso 4 — Ver logs de rendimiento

```bash
python modelo/logs_rendimiento.py

# O con Docker:
docker compose run --rm reportes python modelo/logs_rendimiento.py
```

Muestra tiempos por etapa, uso de CPU/RAM y métricas del modelo.

---

## Paso 5 — Configurar Metabase y vistas SQL

```bash
# Crear las 5 vistas en PostgreSQL y los roles de seguridad
python dashboard/setup_metabase.py

# O con Docker:
docker compose run --rm reportes python dashboard/setup_metabase.py
```

Luego levantar Metabase (ya fue agregado al docker-compose.yml):

```bash
docker compose up -d metabase
```

Abrir el dashboard en: **http://localhost:3000**

**Configurar conexión en Metabase:**
| Campo    | Valor                  |
|----------|------------------------|
| Host     | `postgres`             |
| Puerto   | `5432`                 |
| Base de datos | `fintech_auditoria` |
| Usuario  | `metabase_reader`      |
| Password | `metabase_readonly_2025` |

---

## Paso 6 — Ejecutar auditoría de seguridad

```bash
python seguridad/auditoria_seguridad.py

# O con Docker:
docker compose run --rm reportes python seguridad/auditoria_seguridad.py
```

**Archivo generado:** `seguridad/reporte_auditoria.txt`

---

## Paso 7 — Generar reporte regulatorio (opcional)

```bash
# Reporte del mes actual (ejemplo: junio 2025)
docker compose run --rm reportes python -m src.reportes.reporte_regulatorio 2025 6
```

---

## Resumen de URLs

| Servicio     | URL                        |
|--------------|----------------------------|
| API Gateway  | http://localhost:8000      |
| API Docs     | http://localhost:8000/docs |
| Metabase BI  | http://localhost:3000      |
| PostgreSQL   | localhost:5432             |

---

## Comandos útiles de diagnóstico

```bash
# Ver estado de todos los servicios
docker compose ps

# Ver logs de un servicio específico
docker compose logs postgres
docker compose logs kafka
docker compose logs metabase

# Conectarse directamente a PostgreSQL
docker exec -it auditoria_postgres psql -U auditoria_user -d fintech_auditoria

# Ver las vistas creadas
\dv auditoria.*

# Consultar predicciones guardadas
SELECT * FROM auditoria.predicciones_modelo LIMIT 10;

# Consultar vista resumen del modelo
SELECT * FROM auditoria.vista_resumen_modelo;

# Ver integridad de reportes
SELECT * FROM auditoria.vista_integridad_reportes;
```

---

## Orden de ejecución resumido

```
docker compose up -d
         ↓
python modelo/train_model.py
         ↓
python modelo/logs_rendimiento.py
         ↓
python dashboard/setup_metabase.py → docker compose up -d metabase
         ↓
python seguridad/auditoria_seguridad.py
         ↓
http://localhost:3000  ← Dashboard listo
```