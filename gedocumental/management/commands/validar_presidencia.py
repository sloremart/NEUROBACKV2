"""
Management command: valida pacientes del reporte de Presidencia contra ZeusSalud.

Uso (desde el servidor, dentro del contenedor):
  docker compose exec web python manage.py validar_presidencia \
      --excel /neuro/gdocumental/PRESIDENCIA.xlsx \
      --salida /neuro/gdocumental/PRESIDENCIA_VALIDADO.xlsx

O ejecutar directamente en el servidor (fuera del contenedor, si manage.py es accesible):
  python manage.py validar_presidencia --excel /ruta/archivo.xlsx

Salida: Excel con columnas agregadas:
  FECHA_SOLICITUD_CITA - c.FechaSolicitud: cuando se solicitó/agendó la cita en ZeusSalud
  FECHA_CITA           - c.FeCita: fecha real de la cita (cuando se va a atender)
  ESTADO_CITA          - estado (AT=atendida, SE=sin estado, CA=cancelada...)
  CANTIDAD_CITA        - cantidad en pxcita
  FECHA_FACTURA        - FechaFact de la factura asociada (cuando se generó)
  ADMISION_NO          - número de admisión
  SIN_CITA             - TRUE si no se encontró ninguna cita

También crea una hoja "SIN_CITA" con solo los pacientes sin cita.
"""

import sys
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connections

try:
    import pandas as pd
    import openpyxl  # noqa: F401 - validación de importación
    PANDAS_OK = True
except ImportError:
    PANDAS_OK = False


class Command(BaseCommand):
    help = "Valida citas en ZeusSalud para el reporte de Presidencia de la República"

    def add_arguments(self, parser):
        parser.add_argument(
            "--excel",
            required=True,
            help="Ruta al archivo Excel de entrada (el reporte de Presidencia)",
        )
        parser.add_argument(
            "--salida",
            default="",
            help="Ruta del Excel de salida (por defecto: mismo directorio con _VALIDADO)",
        )

    def handle(self, *args, **options):
        if not PANDAS_OK:
            self.stderr.write("ERROR: faltan paquetes. Instala: pip install pandas openpyxl")
            sys.exit(1)

        ruta_entrada = Path(options["excel"])
        if not ruta_entrada.exists():
            self.stderr.write(f"ERROR: no existe el archivo: {ruta_entrada}")
            sys.exit(1)

        if options["salida"]:
            ruta_salida = Path(options["salida"])
        else:
            ruta_salida = ruta_entrada.parent / (ruta_entrada.stem + "_VALIDADO.xlsx")

        self.stdout.write(f"Leyendo: {ruta_entrada}")
        df = pd.read_excel(ruta_entrada, dtype={"ID_DOCUMENTO": str, "CUPS": str})
        total = len(df)
        self.stdout.write(f"  {total} filas encontradas")

        # Columnas de resultado
        df["FECHA_SOLICITUD_CITA"] = ""   # c.FechaSolicitud — cuando se agendó
        df["FECHA_CITA"]           = ""   # c.FeCita         — fecha de la cita
        df["ESTADO_CITA"]          = ""
        df["CANTIDAD_CITA"]        = ""
        df["FECHA_FACTURA"]        = ""   # facturas.FechaFact
        df["ADMISION_NO"]          = ""
        df["SIN_CITA"]             = False

        sin_cita_count = 0

        with connections["zeussalud"].cursor() as cursor:
            for i, row in df.iterrows():
                num_doc = str(row.get("ID_DOCUMENTO", "")).strip()
                cups    = str(row.get("CUPS", "")).strip()

                if not num_doc or not cups or num_doc == "nan":
                    df.at[i, "SIN_CITA"] = True
                    sin_cita_count += 1
                    continue

                # Buscar citas por documento del paciente + CUPS
                cursor.execute("""
                    SELECT
                        c.IdCita,
                        c.AdmisionNo,
                        CONVERT(varchar(10), c.FeCita, 120)          AS FeCita,
                        LTRIM(RTRIM(ISNULL(c.estado, 'SE')))         AS Estado,
                        p.CUPS,
                        p.Cantidad,
                        CONVERT(varchar(19), c.FechaSolicitud, 120)  AS FechaSolicitud
                    FROM citas c
                    INNER JOIN pxcita p ON c.IdCita = p.IdCita
                    WHERE c.NumeroPaciente = %s
                      AND p.CUPS = %s
                    ORDER BY c.FeCita DESC
                """, [num_doc, cups])
                citas = cursor.fetchall()

                if not citas:
                    df.at[i, "SIN_CITA"] = True
                    sin_cita_count += 1
                    continue

                # Preferir cita atendida; si no, la más reciente
                cita = next(
                    (c for c in citas if (c[3] or "").strip().upper() in ("AT", "PR")),
                    citas[0]
                )
                id_cita, admision_no, fe_cita, estado, _, cantidad, fecha_solicitud = cita

                # Fecha factura asociada
                fecha_fact = ""
                if admision_no:
                    cursor.execute("""
                        SELECT TOP 1
                            CONVERT(varchar(10), FechaFact, 120)
                        FROM facturas
                        WHERE AdmisionNo = %s
                          AND (FacturaAnulada IS NULL OR FacturaAnulada != -1)
                        ORDER BY FechaFact DESC
                    """, [admision_no])
                    ff_row = cursor.fetchone()
                    fecha_fact = ff_row[0] if ff_row else ""

                df.at[i, "FECHA_SOLICITUD_CITA"] = fecha_solicitud or ""
                df.at[i, "FECHA_CITA"]           = fe_cita or ""
                df.at[i, "ESTADO_CITA"]          = estado or ""
                df.at[i, "CANTIDAD_CITA"]        = cantidad or ""
                df.at[i, "FECHA_FACTURA"]        = fecha_fact
                df.at[i, "ADMISION_NO"]          = admision_no or ""

                if (i + 1) % 100 == 0:
                    self.stdout.write(f"  Procesados {i + 1}/{total}...")

        # ── Guardar resultado ─────────────────────────────────────────────────
        with pd.ExcelWriter(str(ruta_salida), engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Validado")

            # Hoja resumen: solo los SIN CITA
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
            f"  Total procesados : {total}\n"
            f"  Con cita         : {con_cita}\n"
            f"  Sin cita         : {sin_cita_count}\n"
            f"{'='*50}\n"
            f"Archivo guardado: {ruta_salida}"
        ))
