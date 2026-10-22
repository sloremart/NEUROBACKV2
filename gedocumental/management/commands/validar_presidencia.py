"""
Management command: valida pacientes del reporte de Presidencia contra ZeusSalud.

Uso (desde el servidor, dentro del contenedor):
  docker compose exec web python manage.py validar_presidencia \
      --excel /neuro/gdocumental/PRESIDENCIA.xlsx \
      --salida /neuro/gdocumental/PRESIDENCIA_VALIDADO.xlsx

Schema ZeusSalud_Neuro (real, confirmado):
  citas              : id, fecha, autoid, estado, fecha_solicitud, empresa, contrato
  citas_procedimientos: id_cita, Cantidad, id_procedimiento, Servicio
  sis_proc           : codigo, cups, nombreve
  sis_paci           : autoid, num_id (CC/TI), primer_nom, primer_ape
  sis_maes           : con_estudio, autoid, Prefijo, fecha_usuario, nro_factura
                       Facturas = registros con Prefijo = 'FES'
  sis_deta           : estudio (FK sis_maes.con_estudio), cups, cantidad

Salida: Excel con columnas agregadas:
  FECHA_AGENDAMIENTO - citas.fecha_solicitud: cuando se agendó la cita en ZeusSalud
  FECHA_CITA         - citas.fecha: fecha real de la cita
  ESTADO_CITA        - estado de la cita (P=pendiente, CA=cancelada, C=cancelada, X=excluida)
  CANTIDAD_CITA      - citas_procedimientos.Cantidad
  FECHA_FACTURA      - sis_maes.fecha_usuario donde Prefijo='FES' (cuando se generó la factura)
  NRO_FACTURA        - sis_maes.nro_factura
  ADMISION_NO        - sis_maes.con_estudio
  SIN_CITA           - TRUE si no tiene cita para ese CUPS

También crea hoja "SIN_CITA" con resumen de pacientes sin cita.
"""

import sys
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connections

try:
    import pandas as pd
    import openpyxl  # noqa: F401
    PANDAS_OK = True
except ImportError:
    PANDAS_OK = False

# Estados que se consideran NO atendidos (cancelados/excluidos)
ESTADOS_CANCELADOS = ("C", "CC", "CA", "I", "X")


class Command(BaseCommand):
    help = "Valida citas en ZeusSalud para el reporte de Presidencia de la Republica"

    def add_arguments(self, parser):
        parser.add_argument(
            "--excel",
            required=True,
            help="Ruta al archivo Excel de entrada (reporte de Presidencia)",
        )
        parser.add_argument(
            "--salida",
            default="",
            help="Ruta del Excel de salida (por defecto: mismo dir con sufijo _VALIDADO)",
        )

    def handle(self, *args, **options):
        if not PANDAS_OK:
            self.stderr.write("ERROR: pip install pandas openpyxl")
            sys.exit(1)

        ruta_entrada = Path(options["excel"])
        if not ruta_entrada.exists():
            self.stderr.write(f"ERROR: no existe: {ruta_entrada}")
            sys.exit(1)

        ruta_salida = (
            Path(options["salida"]) if options["salida"]
            else ruta_entrada.parent / (ruta_entrada.stem + "_VALIDADO.xlsx")
        )

        self.stdout.write(f"Leyendo: {ruta_entrada}")
        df = pd.read_excel(ruta_entrada, dtype={"ID_DOCUMENTO": str, "CUPS": str})
        total = len(df)
        self.stdout.write(f"  {total} filas")

        # Columnas de salida
        df["FECHA_AGENDAMIENTO"] = ""   # citas.fecha_solicitud
        df["FECHA_CITA"]         = ""   # citas.fecha
        df["ESTADO_CITA"]        = ""
        df["CANTIDAD_CITA"]      = ""
        df["FECHA_FACTURA"]      = ""   # sis_maes.fecha_usuario (Prefijo='FES')
        df["NRO_FACTURA"]        = ""
        df["ADMISION_NO"]        = ""
        df["SIN_CITA"]           = False

        sin_cita_count = 0

        with connections["zeussalud"].cursor() as cursor:
            for i, row in df.iterrows():
                num_doc = str(row.get("ID_DOCUMENTO", "")).strip()
                cups    = str(row.get("CUPS", "")).strip()

                if not num_doc or not cups or num_doc == "nan":
                    df.at[i, "SIN_CITA"] = True
                    sin_cita_count += 1
                    continue

                # ── 1. Buscar cita: citas → citas_procedimientos → sis_proc → sis_paci ──
                cursor.execute("""
                    SELECT
                        c.id,
                        CONVERT(varchar(10), c.fecha, 120)           AS fecha_cita,
                        CONVERT(varchar(19), c.fecha_solicitud, 120) AS fecha_agendamiento,
                        LTRIM(RTRIM(ISNULL(c.estado, 'P')))          AS estado,
                        cp.Cantidad
                    FROM citas c
                    JOIN sis_paci sp           ON sp.autoid = c.autoid
                    JOIN citas_procedimientos cp ON cp.id_cita = c.id
                    JOIN sis_proc proc          ON proc.codigo = cp.id_procedimiento
                    WHERE sp.num_id = %s
                      AND proc.cups = %s
                    ORDER BY c.fecha DESC
                """, [num_doc, cups])
                citas = cursor.fetchall()

                if not citas:
                    df.at[i, "SIN_CITA"] = True
                    sin_cita_count += 1
                    continue

                # Preferir cita activa (no cancelada) más reciente
                citas_activas = [
                    c for c in citas
                    if (c[3] or "").strip().upper() not in ESTADOS_CANCELADOS
                ]
                cita = citas_activas[0] if citas_activas else citas[0]
                id_cita, fecha_cita, fecha_agend, estado, cantidad = cita

                df.at[i, "FECHA_AGENDAMIENTO"] = fecha_agend or ""
                df.at[i, "FECHA_CITA"]         = fecha_cita or ""
                df.at[i, "ESTADO_CITA"]        = estado or ""
                df.at[i, "CANTIDAD_CITA"]      = cantidad or ""

                # ── 2. Buscar factura en sis_maes (Prefijo='FES') por paciente + CUPS ──
                cursor.execute("""
                    SELECT TOP 1
                        sm.con_estudio,
                        CONVERT(varchar(10), sm.fecha_usuario, 120) AS fecha_factura,
                        sm.nro_factura
                    FROM sis_maes sm
                    JOIN sis_paci sp  ON sp.autoid  = sm.autoid
                    JOIN sis_deta sd  ON sd.estudio = sm.con_estudio
                    WHERE sp.num_id = %s
                      AND sd.cups   = %s
                      AND sm.Prefijo = 'FES'
                    ORDER BY sm.fecha_usuario DESC
                """, [num_doc, cups])
                fact_row = cursor.fetchone()
                if fact_row:
                    df.at[i, "ADMISION_NO"]  = fact_row[0] or ""
                    df.at[i, "FECHA_FACTURA"] = fact_row[1] or ""
                    df.at[i, "NRO_FACTURA"]  = fact_row[2] or ""

                if (i + 1) % 100 == 0:
                    self.stdout.write(f"  Procesados {i + 1}/{total}...")

        # ── Guardar Excel ──────────────────────────────────────────────────────
        with pd.ExcelWriter(str(ruta_salida), engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Validado")

            df_sin = df[df["SIN_CITA"] == True].copy()
            cols_resumen = [
                "ID_DOCUMENTO", "TIPO_DOCUMENTO",
                "Primer Nombre del Afiliado", "Primer Apellido del Afiliado",
                "CUPS", "DESCRIPCION_CUPS",
                "ID_ORDEN_SERVICIO", "FECHA_ORDEN",
                "CANTIDAD_ORDENADA",
            ]
            cols_ok = [c for c in cols_resumen if c in df_sin.columns]
            df_sin[cols_ok].to_excel(writer, index=False, sheet_name="SIN_CITA")

        con_cita = total - sin_cita_count
        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*50}\n"
            f"  Total    : {total}\n"
            f"  Con cita : {con_cita}\n"
            f"  Sin cita : {sin_cita_count}\n"
            f"{'='*50}\n"
            f"Guardado en: {ruta_salida}"
        ))
