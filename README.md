## Sistema de Detección de Fraude Financiero — ITY1101
  Proyecto de gestión de datos para IA desarrollado en Python con pipeline completo de ingesta, preprocesamiento, entrenamiento y auditoría sobre datos de transacciones financieras.

## Descripción general
El sistema procesa transacciones bancarias almacenadas en PostgreSQL, entrena un modelo de Random Forest para clasificar operaciones como legítimas o sospechosas, y expone los resultados en un dashboard interactivo. Todo el stack corre sobre Docker con los servicios de base de datos, procesamiento y visualización orquestados mediante docker-compose.

## Estructura del proyecto
```bash
fintech_auditoria_EVA3/
├── modelo/
│   ├── train_model.py          Pipeline principal de entrenamiento
│   ├── logs_rendimiento.py     Registro de tiempos y uso de recursos
│   └── logs/training_log.txt   Log generado en cada ejecucion
├── seguridad/
│   ├── auditoria_seguridad.py  Escaneo de datos sensibles y roles RBAC
│   └── reporte_auditoria.txt   Reporte generado por la auditoria
├── src/
│   ├── ingesta/kafka_producer.py     Productor de eventos Kafka
│   ├── batch/spark_transform.py      Transformacion batch con Spark
│   ├── reportes/reporte_regulatorio.py  Generacion de reportes
│   └── servicio/api_gateway.py       API REST de inferencia
├── dashboard/index.html        Dashboard interactivo de metricas
├── init/schema.sql             Esquema inicial de la base de datos
├── docker-compose.yml          Orquestacion de servicios
└── requirements.txt            Dependencias del proyecto
  ```

## Como funciona el pipeline

El archivo principal es modelo/train_model.py. Al ejecutarse realiza ocho etapas en secuencia:

1. Lee los datos desde PostgreSQL (2.300 registros de transacciones)
2. Analiza la calidad de los datos, detectando nulos y calculando estadisticas del campo monto
3. Aplica preprocesamiento con One-Hot Encoding para variables categoricas y StandardScaler para variables numericas, resultando en 13 features
4. Divide los datos en 80% entrenamiento y 20% test, y aplica SMOTE para balancear la clase minoritaria (fraude)
5. Entrena un modelo Random Forest de 200 arboles
6. Evalua el modelo calculando accuracy, precision, recall, F1 score, AUC-ROC y coeficiente Gini
7. Genera graficos de curva ROC, curva de aprendizaje e importancia de variables
8. Guarda las predicciones en la tabla auditoria.predicciones_modelo y serializa el modelo entrenado

## Auditoria de seguridad

El archivo seguridad/auditoria_seguridad.py escanea la base de datos en busca de columnas con datos sensibles segun la Ley 21.719 de Chile, verifica los permisos de cada rol RBAC y genera un reporte de cumplimiento. Los roles definidos son metabase_reader, modelo_ia y auditoria_user, cada uno con permisos minimos necesarios.

## Dashboard

Los resultados del modelo se visualizan en un dashboard HTML disponible en:

https://zbenja19.github.io/DemoGestionDedatosIA/fintech_auditoria_EVA3/dashboard/index.html

## Resultados del modelo

| Metrica   | Valor  |
|-----------|--------|
| Accuracy  | 93.3%  |
| Recall    | 86.5%  |
| AUC-ROC   | 0.871  |
| Gini      | 0.743  |
| F1 Score  | 0.688  |

## Requisitos
```bash
pip install -r requirements.txt
docker-compose up -d
python modelo/train_model.py
```
