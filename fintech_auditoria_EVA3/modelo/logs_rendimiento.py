"""
logs_rendimiento.py: Mide tiempos y uso de recursos durante el entrenamiento del modelo.
"""

import os
import time
import psutil
import threading
import numpy as np
import pandas as pd
from datetime import datetime

# Importamos las funciones del módulo principal
from train_model import (
    leer_datos, analizar_calidad, preprocesar,
    partir_y_balancear, entrenar_modelo, evaluar_modelo,
    graficar_roc, graficar_aprendizaje, graficar_importancia,
    guardar_predicciones, guardar_modelo, log, calcular_gini
)

RUTA_LOG = "modelo/logs/training_log.txt"
os.makedirs("modelo/logs", exist_ok=True)


#1. Monitor de recursos (CPU y RAM) en segundo plano.
class MonitorRecursos:
    """Hilo que registra CPU y RAM cada segundo en segundo plano."""

    def __init__(self):
        self.corriendo   = False
        self.muestras_cpu = []
        self.muestras_ram = []
        self._hilo = None

    def iniciar(self):
        self.corriendo = True
        self._hilo = threading.Thread(target=self._medir, daemon=True)
        self._hilo.start()

    def detener(self):
        self.corriendo = False
        if self._hilo:
            self._hilo.join(timeout=3)

    def _medir(self):
        while self.corriendo:
            self.muestras_cpu.append(psutil.cpu_percent(interval=1))
            self.muestras_ram.append(psutil.virtual_memory().percent)

    def resumen(self):
        if not self.muestras_cpu:
            return {"cpu_max": 0, "cpu_prom": 0, "ram_max": 0, "ram_prom": 0}
        return {
            "cpu_max":  max(self.muestras_cpu),
            "cpu_prom": sum(self.muestras_cpu) / len(self.muestras_cpu),
            "ram_max":  max(self.muestras_ram),
            "ram_prom": sum(self.muestras_ram) / len(self.muestras_ram),
        }


#2. Cronometro para medir tiempos de ejecución de cada etapa.
class Cronometro:
    def __init__(self):
        self.etapas = {}
        self._inicio = None
        self._etapa_actual = None

    def iniciar(self, nombre):
        self._etapa_actual = nombre
        self._inicio = time.time()
        log(f"▶ Iniciando: {nombre}")

    def detener(self):
        if self._etapa_actual and self._inicio:
            dur = time.time() - self._inicio
            self.etapas[self._etapa_actual] = dur
            log(f"✓ {self._etapa_actual}: {dur:.2f} s")
            self._etapa_actual = None

    def total(self):
        return sum(self.etapas.values())


#3. Función principal que ejecuta todo el flujo de entrenamiento y registro de logs.
def main():
    log("=" * 60)
    log("  INICIO — Logs de Rendimiento del Entrenamiento")
    log(f"  Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    monitor = MonitorRecursos()
    crono   = Cronometro()
    monitor.iniciar()

    # ── Etapa 1: Lectura ──────────────────────────────────────────
    crono.iniciar("1. Lectura de datos desde PostgreSQL")
    df = leer_datos()
    crono.detener()

    # ── Etapa 2: Calidad ──────────────────────────────────────────
    crono.iniciar("2. Análisis de calidad")
    df = analizar_calidad(df)
    crono.detener()

    # ── Etapa 3: Preprocesamiento ─────────────────────────────────
    crono.iniciar("3. Preprocesamiento (encoding + scaling)")
    X, y, scaler = preprocesar(df)
    crono.detener()

    # ── Etapa 4: SMOTE ────────────────────────────────────────────
    crono.iniciar("4. Partición 80/20 + SMOTE")
    X_train, X_test, y_train, y_test = partir_y_balancear(X, y)
    crono.detener()

    # ── Etapa 5: Entrenamiento ────────────────────────────────────
    crono.iniciar("5. Entrenamiento Random Forest (200 árboles)")
    modelo = entrenar_modelo(X_train, y_train)
    crono.detener()

    # ── Etapa 6: Evaluación ───────────────────────────────────────
    crono.iniciar("6. Evaluación de métricas")
    y_pred, y_proba, metricas = evaluar_modelo(modelo, X_test, y_test)
    crono.detener()

    # ── Etapa 7: Gráficos ─────────────────────────────────────────
    crono.iniciar("7. Generación de gráficos")
    graficar_roc(modelo, X_test, y_test, y_proba)
    graficar_aprendizaje(modelo, X_train, y_train)
    graficar_importancia(modelo, list(X.columns))
    crono.detener()

    # ── Etapa 8: Guardado ─────────────────────────────────────────
    crono.iniciar("8. Guardado predicciones + modelo serializado")
    guardar_predicciones(df, y_pred, y_proba)
    guardar_modelo(modelo)
    crono.detener()

    # ── Detener monitor ───────────────────────────────────────────
    monitor.detener()
    recursos = monitor.resumen()

    # ── Resumen final ─────────────────────────────────────────────
    log("")
    log("=" * 60)
    log("  RESUMEN DE RENDIMIENTO")
    log("=" * 60)
    log("")
    log("  Tiempos por etapa:")
    for nombre, dur in crono.etapas.items():
        log(f"    {nombre:<45} {dur:>7.2f} s")
    log(f"  {'TOTAL':<45} {crono.total():>7.2f} s")
    log("")
    log("  Uso de recursos:")
    log(f"    CPU máximo:     {recursos['cpu_max']:.1f}%")
    log(f"    CPU promedio:   {recursos['cpu_prom']:.1f}%")
    log(f"    RAM máxima:     {recursos['ram_max']:.1f}%")
    log(f"    RAM promedio:   {recursos['ram_prom']:.1f}%")
    log("")
    log("  Métricas del modelo:")
    log(f"    Accuracy:       {metricas['acc']*100:.1f}%")
    log(f"    Precisión:      {metricas['prec']:.3f}")
    log(f"    Recall:         {metricas['rec']:.3f}")
    log(f"    F1 Score:       {metricas['f1']:.3f}")
    log(f"    AUC-ROC:        {metricas['auc']:.3f}")
    log(f"    Gini:           {metricas['gini']:.3f}")
    log("")
    log(f"  Log guardado en: {RUTA_LOG}")
    log("=" * 60)


if __name__ == "__main__":
    main()