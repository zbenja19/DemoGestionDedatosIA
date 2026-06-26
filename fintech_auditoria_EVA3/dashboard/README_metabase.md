# 📊 Dashboard Metabase — Sistema de Auditoría Fintech

Guía paso a paso para conectar Metabase a PostgreSQL y crear los 5 paneles del dashboard de detección de transacciones sospechosas.

---

## 1. Levantar Metabase

```bash
docker compose up -d metabase
```

Esperar ~2 minutos y abrir: **http://localhost:3000**

---

## 2. Configuración inicial

Al abrir por primera vez Metabase te pedirá:

1. Idioma → **Español**
2. Nombre y correo del administrador → el tuyo
3. Contraseña → crea una segura
4. **Agregar datos** → seleccionar **PostgreSQL**

### Datos de conexión a PostgreSQL

| Campo             | Valor                    |
|-------------------|--------------------------|
| Nombre            | `Fintech Auditoría`      |
| Host              | `postgres`               |
| Puerto            | `5432`                   |
| Base de datos     | `fintech_auditoria`      |
| Usuario           | `metabase_reader`        |
| Contraseña        | `metabase_readonly_2025` |

Clic en **Guardar** → **Siguiente** → **Listo**.

---

## 3. Crear los 5 paneles del dashboard

Ve a **Nuevo → Dashboard** y nómbralo `Dashboard Auditoría Fintech EV3`.

### Panel 1 — Resumen del modelo

- Clic en **Agregar pregunta → SQL nativo**
- Pegar la siguiente consulta:

```sql
SELECT
    total_evaluadas        AS "Total transacciones evaluadas",
    total_alertas          AS "Total alertas generadas",
    tasa_alerta_pct        AS "Tasa de alerta (%)",
    tasa_falso_positivo_pct AS "Tasa de falso positivo (%)"
FROM auditoria.vista_resumen_modelo;
```

- Tipo de visualización: **Número** (para cada métrica como KPI)
- Guardar como: `Resumen del modelo`

---

### Panel 2 — Distribución diaria de predicciones

```sql
SELECT
    fecha,
    sospechosas,
    legitimas,
    total
FROM auditoria.vista_distribucion_diaria
ORDER BY fecha DESC
LIMIT 30;
```

- Tipo de visualización: **Gráfico de barras apiladas**
- Eje X: `fecha` | Eje Y: `sospechosas` y `legitimas`
- Guardar como: `Distribución diaria`

---

### Panel 3 — Alertas por rango de riesgo

```sql
SELECT
    rango_riesgo,
    cantidad,
    prob_promedio_pct AS "Probabilidad promedio (%)"
FROM auditoria.vista_alertas_por_rango
ORDER BY prob_promedio_pct DESC;
```

- Tipo de visualización: **Gráfico de barras horizontal**
- Guardar como: `Alertas por rango de riesgo`

---

### Panel 4 — Tendencia mensual de alertas

```sql
SELECT
    mes,
    total_evaluadas,
    alertas,
    tasa_alerta_pct AS "Tasa de alerta (%)"
FROM auditoria.vista_tendencia_mensual
ORDER BY mes DESC;
```

- Tipo de visualización: **Línea de tendencia**
- Eje X: `mes` | Eje Y: `tasa_alerta_pct`
- Guardar como: `Tendencia mensual`

---

### Panel 5 — Integridad de reportes regulatorios

```sql
SELECT
    reporte_id,
    periodo,
    fecha_generacion,
    estado_integridad
FROM auditoria.vista_integridad_reportes;
```

- Tipo de visualización: **Tabla**
- Guardar como: `Integridad de reportes`

---

## 4. Organizar el dashboard

1. Arrastrar los 5 paneles al dashboard
2. Ordenar sugerido:
   ```
   [ KPIs Resumen (ancho completo)      ]
   [ Distribución diaria | Tendencia    ]
   [ Alertas por riesgo | Integridad    ]
   ```
3. Clic en **Guardar**

---

## 5. Configurar actualización automática

- Clic en el ícono de reloj (esquina superior derecha del dashboard)
- Seleccionar **Actualizar cada 1 hora**

---

## 6. Exportar capturas para el informe

Para el informe EV3 se necesitan capturas de cada panel. Usar:

- **Chrome** → clic derecho → Capturar pantalla
- O en Metabase: `...` → **Compartir → Imagen PNG**

Guardar las capturas en `modelo/graficos/` con los nombres:
```
dashboard_resumen.png
dashboard_distribucion.png
dashboard_alertas_riesgo.png
dashboard_tendencia.png
dashboard_integridad.png
```