CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS core.dim_cliente (
    cliente_id TEXT PRIMARY KEY,
    razon_social BYTEA NOT NULL,
    nit BYTEA NOT NULL,
    email_contacto BYTEA NOT NULL,
    celular_contacto BYTEA NOT NULL,
    direccion BYTEA NOT NULL,
    ciudad TEXT NOT NULL,
    departamento TEXT NOT NULL,
    representante_legal BYTEA NOT NULL,
    observaciones BYTEA NOT NULL
);

CREATE TABLE IF NOT EXISTS core.dim_frontera (
    frontera_id TEXT PRIMARY KEY,
    codigo_sic TEXT NOT NULL,
    cliente_id TEXT NOT NULL REFERENCES core.dim_cliente(cliente_id),
    mercado TEXT NOT NULL,
    operador_red TEXT NOT NULL,
    nivel_tension INTEGER NOT NULL,
    fecha_registro DATE NOT NULL,
    estado TEXT NOT NULL,
    min_kwh NUMERIC(18, 3),
    max_kwh NUMERIC(18, 3)
);

CREATE TABLE IF NOT EXISTS core.fact_consumo_horario (
    frontera_id TEXT NOT NULL REFERENCES core.dim_frontera(frontera_id),
    ts_local TIMESTAMPTZ NOT NULL,
    consumo_kwh NUMERIC(18, 3) NOT NULL CHECK (consumo_kwh >= 0 AND consumo_kwh <= 1000000),
    version_lectura INTEGER NOT NULL,
    calidad_lectura TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    PRIMARY KEY (frontera_id, ts_local)
);

CREATE TABLE IF NOT EXISTS core.facturacion (
    factura_id TEXT PRIMARY KEY,
    frontera_id TEXT NOT NULL REFERENCES core.dim_frontera(frontera_id),
    periodo TEXT NOT NULL,
    consumo_facturado_kwh NUMERIC(18, 3) NOT NULL,
    tarifa_cop_kwh NUMERIC(18, 4) NOT NULL,
    valor_total_cop NUMERIC(20, 2) NOT NULL,
    source_file TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS core.cuarentena (
    source_file TEXT NOT NULL,
    record_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_file, record_key, reason)
);

CREATE TABLE IF NOT EXISTS core.audit_log (
    run_id UUID PRIMARY KEY,
    actor TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    rows_read BIGINT NOT NULL,
    rows_loaded BIGINT NOT NULL,
    rows_rejected BIGINT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('SUCCESS', 'FAILED')),
    details JSONB NOT NULL
);

CREATE OR REPLACE FUNCTION core.reject_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'core.audit_log is append-only';
END;
$$;

DROP TRIGGER IF EXISTS audit_log_no_mutation ON core.audit_log;
CREATE TRIGGER audit_log_no_mutation
BEFORE UPDATE OR DELETE ON core.audit_log
FOR EACH ROW EXECUTE FUNCTION core.reject_audit_mutation();

CREATE OR REPLACE VIEW analytics.vw_consumo_mensual AS
SELECT
    c.frontera_id,
    date_trunc('month', c.ts_local AT TIME ZONE 'America/Bogota')::date AS mes,
    round(sum(c.consumo_kwh), 3) AS consumo_kwh,
    count(*)::BIGINT AS horas_con_dato,
    f.mercado,
    f.operador_red,
    f.estado
FROM core.fact_consumo_horario AS c
JOIN core.dim_frontera AS f USING (frontera_id)
GROUP BY c.frontera_id, date_trunc('month', c.ts_local AT TIME ZONE 'America/Bogota')::date,
         f.mercado, f.operador_red, f.estado;

REVOKE ALL ON SCHEMA core, analytics FROM PUBLIC;
GRANT USAGE ON SCHEMA core TO rol_ingesta, rol_auditor;
GRANT USAGE ON SCHEMA analytics TO rol_analista;

REVOKE ALL ON ALL TABLES IN SCHEMA core FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON
    core.dim_cliente, core.dim_frontera, core.fact_consumo_horario,
    core.facturacion, core.cuarentena
TO rol_ingesta;
GRANT INSERT ON core.audit_log TO rol_ingesta;
GRANT SELECT ON analytics.vw_consumo_mensual TO rol_analista;
GRANT SELECT ON core.audit_log TO rol_auditor;

REVOKE ALL ON core.dim_cliente, core.dim_frontera, core.fact_consumo_horario,
    core.facturacion, core.cuarentena, core.audit_log FROM rol_analista;
REVOKE ALL ON core.dim_cliente, core.dim_frontera, core.fact_consumo_horario,
    core.facturacion, core.cuarentena FROM rol_auditor;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON core.audit_log
    FROM rol_ingesta, rol_auditor;
