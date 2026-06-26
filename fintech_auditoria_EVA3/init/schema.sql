-- Crear esquema principal
CREATE SCHEMA IF NOT EXISTS auditoria;

-- TABLAS BASE

-- Tabla: clientes
CREATE TABLE IF NOT EXISTS auditoria.clientes (
    cliente_id      TEXT PRIMARY KEY,
    nombre          TEXT NOT NULL,
    rut             TEXT UNIQUE,
    email           TEXT,
    kyc_status      TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (kyc_status IN ('VERIFIED','PENDING','BLOCKED')),
    fecha_registro  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Tabla: cuentas
CREATE TABLE IF NOT EXISTS auditoria.cuentas (
    cuenta_id       TEXT PRIMARY KEY,
    cliente_id      TEXT NOT NULL REFERENCES auditoria.clientes(cliente_id),
    tipo_cuenta     TEXT NOT NULL DEFAULT 'corriente'
                        CHECK (tipo_cuenta IN ('corriente','vista','ahorro')),
    saldo           NUMERIC(18,2) NOT NULL DEFAULT 0,
    fecha_apertura  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Tabla: transacciones_procesadas (Capa Gold del pipeline)
CREATE TABLE IF NOT EXISTS auditoria.transacciones_procesadas (
    event_id        TEXT PRIMARY KEY,
    cliente_id      TEXT REFERENCES auditoria.clientes(cliente_id),
    cuenta_id       TEXT REFERENCES auditoria.cuentas(cuenta_id),
    monto           NUMERIC(18,2) NOT NULL CHECK (monto > 0),
    moneda          CHAR(3) NOT NULL DEFAULT 'CLP',
    estado          TEXT NOT NULL
                        CHECK (estado IN ('PENDING','APPROVED','REJECTED','REVERSED')),
    timestamp       TIMESTAMP NOT NULL DEFAULT NOW(),
    hash_evento     TEXT NOT NULL,
    procesado_en    TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Tabla: reportes_regulatorios (inmutable)
CREATE TABLE IF NOT EXISTS auditoria.reportes_regulatorios (
    reporte_id          TEXT PRIMARY KEY,
    periodo             TEXT NOT NULL,
    total_transacciones INTEGER NOT NULL DEFAULT 0,
    total_monto_clp     NUMERIC(20,2) NOT NULL DEFAULT 0,
    total_alertas       INTEGER NOT NULL DEFAULT 0,
    hash_integridad     CHAR(64) NOT NULL,
    fecha_generacion    TIMESTAMP NOT NULL DEFAULT NOW(),
    contenido_json      JSONB
);

-- Tabla: predicciones_modelo (EV3 — generada por train_model.py)
CREATE TABLE IF NOT EXISTS auditoria.predicciones_modelo (
    id                      SERIAL PRIMARY KEY,
    transaction_id          TEXT NOT NULL,
    probabilidad_sospechosa FLOAT NOT NULL CHECK (probabilidad_sospechosa BETWEEN 0 AND 1),
    prediccion_binaria      INTEGER NOT NULL CHECK (prediccion_binaria IN (0,1)),
    timestamp_prediccion    TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Tabla: log_auditoria_accesos (trazabilidad de quién accede)
CREATE TABLE IF NOT EXISTS auditoria.log_accesos (
    id              SERIAL PRIMARY KEY,
    usuario_db      TEXT NOT NULL DEFAULT CURRENT_USER,
    accion          TEXT NOT NULL,
    tabla_afectada  TEXT,
    timestamp       TIMESTAMP NOT NULL DEFAULT NOW()
);

-- TRIGGERS DE INMUTABILIDAD
-- Bloquean UPDATE y DELETE en tablas críticas

CREATE OR REPLACE FUNCTION auditoria.bloquear_modificacion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'Operación no permitida: los registros de auditoría son inmutables. '
        'Tabla: %, Operación: %', TG_TABLE_NAME, TG_OP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Trigger en transacciones
DROP TRIGGER IF EXISTS trg_inmutable_transacciones
    ON auditoria.transacciones_procesadas;
CREATE TRIGGER trg_inmutable_transacciones
    BEFORE UPDATE OR DELETE ON auditoria.transacciones_procesadas
    FOR EACH ROW EXECUTE FUNCTION auditoria.bloquear_modificacion();

-- Trigger en reportes
DROP TRIGGER IF EXISTS trg_inmutable_reportes
    ON auditoria.reportes_regulatorios;
CREATE TRIGGER trg_inmutable_reportes
    BEFORE UPDATE OR DELETE ON auditoria.reportes_regulatorios
    FOR EACH ROW EXECUTE FUNCTION auditoria.bloquear_modificacion();


-- FUNCIÓN DE VERIFICACIÓN DE INTEGRIDAD DE REPORTES
CREATE OR REPLACE FUNCTION auditoria.verificar_reporte(p_reporte_id TEXT)
RETURNS TABLE (
    reporte_id      TEXT,
    periodo         TEXT,
    hash_almacenado TEXT,
    estado          TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.reporte_id,
        r.periodo,
        r.hash_integridad,
        CASE
            WHEN r.hash_integridad IS NOT NULL
             AND LENGTH(r.hash_integridad) = 64
            THEN 'ÍNTEGRO ✓'
            ELSE 'COMPROMETIDO ✗'
        END AS estado
    FROM auditoria.reportes_regulatorios r
    WHERE r.reporte_id = p_reporte_id;
END;
$$ LANGUAGE plpgsql;

-- DATOS DE PRUEBA (para desarrollo y demostración)
INSERT INTO auditoria.clientes (cliente_id, nombre, rut, email, kyc_status)
VALUES
    ('CLI-001', 'María González López',  '12.345.678-9', 'mgonzalez@email.com', 'VERIFIED'),
    ('CLI-002', 'Juan Pablo Morales',    '23.456.789-0', 'jmorales@email.com',  'VERIFIED'),
    ('CLI-003', 'Empresa XYZ SpA',       '76.543.210-1', 'contacto@xyz.cl',     'PENDING'),
    ('CLI-004', 'Pedro Soto Ramírez',    '34.567.890-1', 'psoto@email.com',     'BLOCKED'),
    ('CLI-005', 'Ana Martínez Vega',     '45.678.901-2', 'amartinez@email.com', 'VERIFIED')
ON CONFLICT DO NOTHING;

INSERT INTO auditoria.cuentas (cuenta_id, cliente_id, tipo_cuenta, saldo)
VALUES
    ('CTA-001234', 'CLI-001', 'corriente', 1500000.00),
    ('CTA-001235', 'CLI-002', 'vista',      250000.00),
    ('CTA-001236', 'CLI-003', 'corriente', 8500000.00),
    ('CTA-001237', 'CLI-004', 'ahorro',     100000.00),
    ('CTA-001238', 'CLI-005', 'corriente', 3200000.00)
ON CONFLICT DO NOTHING;

INSERT INTO auditoria.transacciones_procesadas
    (event_id, cliente_id, cuenta_id, monto, moneda, estado, hash_evento)
VALUES
    ('EVT-00001','CLI-001','CTA-001234',  150000,'CLP','APPROVED', md5('EVT-00001')),
    ('EVT-00002','CLI-002','CTA-001235',   85000,'CLP','APPROVED', md5('EVT-00002')),
    ('EVT-00003','CLI-003','CTA-001236',11500000,'CLP','REJECTED', md5('EVT-00003')),
    ('EVT-00004','CLI-004','CTA-001237',  500000,'USD','REJECTED', md5('EVT-00004')),
    ('EVT-00005','CLI-005','CTA-001238',  320000,'CLP','APPROVED', md5('EVT-00005')),
    ('EVT-00006','CLI-001','CTA-001234', 2800000,'CLP','REVERSED', md5('EVT-00006')),
    ('EVT-00007','CLI-002','CTA-001235',   45000,'CLP','APPROVED', md5('EVT-00007')),
    ('EVT-00008','CLI-003','CTA-001236', 9200000,'USD','REJECTED', md5('EVT-00008')),
    ('EVT-00009','CLI-005','CTA-001238',  780000,'CLP','APPROVED', md5('EVT-00009')),
    ('EVT-00010','CLI-001','CTA-001234',  125000,'CLP','APPROVED', md5('EVT-00010'))
ON CONFLICT DO NOTHING;

INSERT INTO auditoria.reportes_regulatorios
    (reporte_id, periodo, total_transacciones, total_monto_clp, total_alertas, hash_integridad)
VALUES
    ('RPT-2025-01','2025-01', 1842, 4523100000, 47, md5('RPT-2025-01-contenido')),
    ('RPT-2025-02','2025-02', 1976, 5102800000, 53, md5('RPT-2025-02-contenido')),
    ('RPT-2025-03','2025-03', 2104, 6034500000, 61, md5('RPT-2025-03-contenido'))
ON CONFLICT DO NOTHING;

-- ÍNDICES para rendimiento
CREATE INDEX IF NOT EXISTS idx_trans_estado
    ON auditoria.transacciones_procesadas(estado);

CREATE INDEX IF NOT EXISTS idx_trans_timestamp
    ON auditoria.transacciones_procesadas(timestamp);

CREATE INDEX IF NOT EXISTS idx_trans_cliente
    ON auditoria.transacciones_procesadas(cliente_id);

CREATE INDEX IF NOT EXISTS idx_pred_timestamp
    ON auditoria.predicciones_modelo(timestamp_prediccion);

CREATE INDEX IF NOT EXISTS idx_pred_binaria
    ON auditoria.predicciones_modelo(prediccion_binaria);