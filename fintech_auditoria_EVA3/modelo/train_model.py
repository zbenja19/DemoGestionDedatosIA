"""
train_model.py: Entrenamiento del modelo de detección de transacciones sospechosas.
"""

import os
import time
import warnings
import joblib
import psycopg2
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")  # Sin interfaz gráfica (modo servidor)

from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, learning_curve, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    confusion_matrix, classification_report,
    roc_auc_score, roc_curve, accuracy_score,
    precision_score, recall_score, f1_score
)
from imblearn.over_sampling import SMOTE

warnings.filterwarnings("ignore")

# Configuración de conexión a PostgreSQL
DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "postgres"),
    "port":     os.getenv("DB_PORT",     "5432"),
    "dbname":   os.getenv("POSTGRES_DB", "fintech_auditoria"),
    "user":     os.getenv("POSTGRES_USER",     "auditoria_user"),
    "password": os.getenv("POSTGRES_PASSWORD", "auditoria_pass"),
}

RUTA_MODELO    = "modelo/modelo_rf_fintech.pkl"
RUTA_GRAFICOS  = "modelo/graficos"
RUTA_LOGS      = "modelo/logs/training_log.txt"

os.makedirs(RUTA_GRAFICOS, exist_ok=True)
os.makedirs("modelo/logs", exist_ok=True)


#Utilidades 
def log(mensaje):
    """Imprime en consola y guarda en el archivo de log."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{ts}] {mensaje}"
    print(linea)
    with open(RUTA_LOGS, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def calcular_gini(auc):
    """Coeficiente Gini a partir del AUC-ROC."""
    return 2 * auc - 1


#1. Lectura de datos

def leer_datos():
    """Lee la tabla transaccion desde PostgreSQL y retorna un DataFrame."""
    log("Conectando a PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        query = """
            SELECT
                t.event_id,
                t.monto,
                t.moneda,
                t.estado,
                t.timestamp,
                c.kyc_status,
                cu.tipo_cuenta
            FROM auditoria.transacciones_procesadas t
            LEFT JOIN auditoria.clientes c   ON t.cliente_id = c.cliente_id
            LEFT JOIN auditoria.cuentas  cu  ON t.cuenta_id  = cu.cuenta_id
            WHERE t.estado IS NOT NULL
        """
        df = pd.read_sql(query, conn)
        conn.close()
        log(f"Datos leídos correctamente: {len(df)} registros.")
        return df
    except Exception as e:
        log(f"ERROR al conectar a PostgreSQL: {e}")
        log("Generando datos sintéticos para demostración...")
        return generar_datos_sinteticos()


def generar_datos_sinteticos():
    """
    Genera un dataset sintético representativo cuando PostgreSQL
    no tiene datos suficientes o no está disponible.
    """
    np.random.seed(42)
    n = 2300

    estados    = np.random.choice(["APPROVED","REJECTED","REVERSED"],
                                  p=[0.92, 0.05, 0.03], size=n)
    monedas    = np.random.choice(["CLP","USD","EUR"], p=[0.87,0.11,0.02], size=n)
    kyc        = np.random.choice(["VERIFIED","PENDING","BLOCKED"],
                                  p=[0.75, 0.20, 0.05], size=n)
    tipo_cta   = np.random.choice(["corriente","vista","ahorro"],
                                  p=[0.55, 0.30, 0.15], size=n)
    montos     = np.abs(np.random.lognormal(mean=12.5, sigma=1.8, size=n))
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h")

    df = pd.DataFrame({
        "event_id":   [f"EVT-{i:05d}" for i in range(n)],
        "monto":      montos,
        "moneda":     monedas,
        "estado":     estados,
        "timestamp":  timestamps,
        "kyc_status": kyc,
        "tipo_cuenta": tipo_cta,
    })
    log(f"Dataset sintético generado: {len(df)} registros.")
    return df


# 2. Análisis de calidad de datos
def analizar_calidad(df):
    """Estadísticas descriptivas y análisis de nulos."""
    log("─── Análisis de calidad de datos ───")

    nulos = df.isnull().sum()
    log(f"Nulos por columna:\n{nulos[nulos > 0].to_string() or 'Ninguno'}")

    log(f"Estadísticas del campo monto:")
    log(f"  Media:    ${df['monto'].mean():>15,.0f} CLP")
    log(f"  Mediana:  ${df['monto'].median():>15,.0f} CLP")
    log(f"  P25:      ${df['monto'].quantile(0.25):>15,.0f} CLP")
    log(f"  P75:      ${df['monto'].quantile(0.75):>15,.0f} CLP")
    log(f"  Std:      ${df['monto'].std():>15,.0f} CLP")

    log(f"Distribución de estado:\n{df['estado'].value_counts().to_string()}")

    # Imputar nulos en tipo_cuenta con la moda
    if df["tipo_cuenta"].isnull().any():
        moda = df["tipo_cuenta"].mode()[0]
        df["tipo_cuenta"].fillna(moda, inplace=True)
        log(f"Imputados nulos en tipo_cuenta con moda: '{moda}'")

    return df


#3. Preprocesamiento
def preprocesar(df):
    """
    - Variable objetivo binaria
    - Features temporales
    - One-Hot Encoding
    - StandardScaler en monto
    """
    log("─── Preprocesamiento ───")

    # Variable objetivo
    df["es_sospechosa"] = df["estado"].isin(["REJECTED", "REVERSED"]).astype(int)
    log(f"Clases: {df['es_sospechosa'].value_counts().to_dict()}")

    # Features temporales
    df["timestamp"]      = pd.to_datetime(df["timestamp"])
    df["hora_del_dia"]   = df["timestamp"].dt.hour
    df["dia_semana"]     = df["timestamp"].dt.dayofweek
    df["es_fin_semana"]  = (df["dia_semana"] >= 5).astype(int)

    # Columnas a usar
    features_num  = ["monto", "hora_del_dia", "dia_semana", "es_fin_semana"]
    features_cat  = ["moneda", "kyc_status", "tipo_cuenta"]
    target        = "es_sospechosa"

    # One-Hot Encoding
    df_encoded = pd.get_dummies(df[features_cat], drop_first=False)
    log(f"Columnas tras One-Hot: {list(df_encoded.columns)}")

    # Normalización del monto
    scaler = StandardScaler()
    df["monto_norm"] = scaler.fit_transform(df[["monto"]])

    features_num_norm = ["monto_norm", "hora_del_dia", "dia_semana", "es_fin_semana"]
    X = pd.concat([df[features_num_norm].reset_index(drop=True),
                   df_encoded.reset_index(drop=True)], axis=1)
    y = df[target].values

    log(f"Shape final: X={X.shape}, y={y.shape}")
    return X, y, scaler


#4. Partición y Smote.
def partir_y_balancear(X, y):
    """División 80/20 estratificada + SMOTE sobre entrenamiento."""
    log("─── Partición 80/20 + SMOTE ───")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    log(f"Train: {X_train.shape[0]} muestras | Test: {X_test.shape[0]} muestras")
    log(f"Clases en train antes de SMOTE: {dict(zip(*np.unique(y_train, return_counts=True)))}")

    smote = SMOTE(random_state=42)
    X_train_bal, y_train_bal = smote.fit_resample(X_train, y_train)
    log(f"Clases en train tras SMOTE:     {dict(zip(*np.unique(y_train_bal, return_counts=True)))}")

    return X_train_bal, X_test, y_train_bal, y_test


#5. Entrenamiento del modelo
def entrenar_modelo(X_train, y_train):
    """Entrena Random Forest con los hiperparámetros definidos."""
    log("─── Entrenamiento Random Forest ───")
    t0 = time.time()

    modelo = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_split=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    modelo.fit(X_train, y_train)

    log(f"Entrenamiento completado en {time.time()-t0:.1f} segundos.")
    return modelo


#6. Métricas de evaluación
def evaluar_modelo(modelo, X_test, y_test):
    """Calcula y muestra todas las métricas requeridas."""
    log("─── Métricas del modelo ───")

    y_pred  = modelo.predict(X_test)
    y_proba = modelo.predict_proba(X_test)[:, 1]

    acc  = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec  = recall_score(y_test, y_pred, zero_division=0)
    f1   = f1_score(y_test, y_pred, zero_division=0)
    auc  = roc_auc_score(y_test, y_proba)
    gini = calcular_gini(auc)
    cm   = confusion_matrix(y_test, y_pred)

    log(f"Matriz de confusión:\n{cm}")
    log(f"Accuracy:   {acc:.4f}  ({acc*100:.1f}%)")
    log(f"Precisión:  {prec:.4f}")
    log(f"Recall:     {rec:.4f}")
    log(f"F1 Score:   {f1:.4f}")
    log(f"AUC-ROC:    {auc:.4f}")
    log(f"Gini:       {gini:.4f}")
    log(f"\n{classification_report(y_test, y_pred, target_names=['Legítima','Sospechosa'])}")

    return y_pred, y_proba, {"acc": acc, "prec": prec, "rec": rec,
                              "f1": f1, "auc": auc, "gini": gini}


#7. Gráficos
def graficar_roc(modelo, X_test, y_test, y_proba):
    """Curva ROC."""
    fpr, tpr, _ = roc_curve(y_test, y_proba)
    auc = roc_auc_score(y_test, y_proba)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(fpr, tpr, color="#1F4E79", lw=2, label=f"AUC = {auc:.3f}")
    ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5)
    ax.fill_between(fpr, tpr, alpha=0.08, color="#1F4E79")
    ax.set_xlabel("Tasa de Falsos Positivos")
    ax.set_ylabel("Tasa de Verdaderos Positivos")
    ax.set_title("Curva ROC — Random Forest")
    ax.legend(loc="lower right")
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1.02])
    plt.tight_layout()
    ruta = f"{RUTA_GRAFICOS}/curva_roc.png"
    plt.savefig(ruta, dpi=150)
    plt.close()
    log(f"Gráfico guardado: {ruta}")


def graficar_aprendizaje(modelo, X_train, y_train):
    """Curva de aprendizaje."""
    log("Generando curva de aprendizaje (puede tardar ~1 min)...")
    sizes, train_sc, val_sc = learning_curve(
        modelo, X_train, y_train,
        cv=3, n_jobs=-1,
        train_sizes=np.linspace(0.1, 1.0, 8),
        scoring="f1"
    )

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(sizes, train_sc.mean(axis=1), "o-", color="#1F4E79", label="Entrenamiento")
    ax.fill_between(sizes,
                    train_sc.mean(axis=1) - train_sc.std(axis=1),
                    train_sc.mean(axis=1) + train_sc.std(axis=1), alpha=0.1, color="#1F4E79")
    ax.plot(sizes, val_sc.mean(axis=1), "s-", color="#E85D24", label="Validación cruzada")
    ax.fill_between(sizes,
                    val_sc.mean(axis=1) - val_sc.std(axis=1),
                    val_sc.mean(axis=1) + val_sc.std(axis=1), alpha=0.1, color="#E85D24")
    ax.set_xlabel("Tamaño del conjunto de entrenamiento")
    ax.set_ylabel("F1 Score")
    ax.set_title("Curva de aprendizaje — Random Forest")
    ax.legend()
    plt.tight_layout()
    ruta = f"{RUTA_GRAFICOS}/curva_aprendizaje.png"
    plt.savefig(ruta, dpi=150)
    plt.close()
    log(f"Gráfico guardado: {ruta}")


def graficar_importancia(modelo, columnas):
    """Importancia de variables (top 15)."""
    importancias = pd.Series(modelo.feature_importances_, index=columnas)
    top15 = importancias.nlargest(15).sort_values()

    fig, ax = plt.subplots(figsize=(8, 6))
    bars = ax.barh(top15.index, top15.values, color="#1F4E79", alpha=0.85)
    ax.bar_label(bars, fmt="%.3f", padding=3, fontsize=9)
    ax.set_xlabel("Importancia")
    ax.set_title("Top 15 variables más relevantes — Random Forest")
    plt.tight_layout()
    ruta = f"{RUTA_GRAFICOS}/importancia_variables.png"
    plt.savefig(ruta, dpi=150)
    plt.close()
    log(f"Gráfico guardado: {ruta}")


#8. Guardar predicciones en PostgreSQL
def guardar_predicciones(df, y_pred, y_proba):
    """Guarda las predicciones del conjunto de prueba en tabla predicciones_modelo."""
    log("─── Guardando predicciones en PostgreSQL ───")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor()

        # Crear tabla si no existe
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auditoria.predicciones_modelo (
                id                   SERIAL PRIMARY KEY,
                transaction_id       TEXT,
                probabilidad_sospechosa FLOAT,
                prediccion_binaria   INTEGER,
                timestamp_prediccion TIMESTAMP DEFAULT NOW()
            );
        """)

        # Tomar índice del conjunto de prueba (últimos 20%)
        n_test = len(y_pred)
        ids    = df["event_id"].iloc[-n_test:].tolist()

        registros = [
            (str(ids[i]), float(y_proba[i]), int(y_pred[i]))
            for i in range(n_test)
        ]

        cur.executemany("""
            INSERT INTO auditoria.predicciones_modelo
                (transaction_id, probabilidad_sospechosa, prediccion_binaria)
            VALUES (%s, %s, %s)
        """, registros)

        conn.commit()
        cur.close()
        conn.close()
        log(f"{len(registros)} predicciones guardadas en auditoria.predicciones_modelo.")
    except Exception as e:
        log(f"ADVERTENCIA: No se pudieron guardar predicciones en PostgreSQL: {e}")


#9. Guardar modelo
def guardar_modelo(modelo):
    """Serializa el modelo entrenado con joblib."""
    os.makedirs(os.path.dirname(RUTA_MODELO), exist_ok=True)
    joblib.dump(modelo, RUTA_MODELO)
    size_mb = os.path.getsize(RUTA_MODELO) / 1_000_000
    log(f"Modelo guardado en {RUTA_MODELO} ({size_mb:.1f} MB)")


# MAIN
def main():
    log("=" * 60)
    log("  INICIO — Entrenamiento Modelo de Detección de Fraude")
    log("=" * 60)
    t_inicio = time.time()

    # 1. Leer datos
    df = leer_datos()

    # 2. Calidad
    df = analizar_calidad(df)

    # 3. Preprocesar
    X, y, scaler = preprocesar(df)

    # 4. Partir y balancear
    X_train, X_test, y_train, y_test = partir_y_balancear(X, y)

    # 5. Entrenar
    modelo = entrenar_modelo(X_train, y_train)

    # 6. Evaluar
    y_pred, y_proba, metricas = evaluar_modelo(modelo, X_test, y_test)

    # 7. Gráficos
    graficar_roc(modelo, X_test, y_test, y_proba)
    graficar_aprendizaje(modelo, X_train, y_train)
    graficar_importancia(modelo, list(X.columns))

    # 8. Guardar predicciones
    guardar_predicciones(df, y_pred, y_proba)

    # 9. Guardar modelo
    guardar_modelo(modelo)

    log("=" * 60)
    log(f"  RESUMEN FINAL")
    log(f"  Accuracy : {metricas['acc']*100:.1f}%")
    log(f"  F1 Score : {metricas['f1']:.3f}")
    log(f"  AUC-ROC  : {metricas['auc']:.3f}")
    log(f"  Gini     : {metricas['gini']:.3f}")
    log(f"  Tiempo total: {time.time()-t_inicio:.0f} segundos")
    log("=" * 60)


if __name__ == "__main__":
    main()