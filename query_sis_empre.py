import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};SERVER=LorenaM;DATABASE=ZeusSalud_Neuro;Trusted_Connection=yes;'
)
cursor = conn.cursor()

print("=== ENTIDADES CON SANITAS / EPS SIMILARES (codigo, nombre, alias, tipoRegimen, LineaNegocio) ===")
cursor.execute("""
    SELECT codigo, nombre, alias, tipoRegimen, LineaNegocio
    FROM sis_empre
    WHERE nombre LIKE '%SANITAS%' OR nombre LIKE '%COMPENSAR%' OR nombre LIKE '%SALUD TOTAL%'
       OR nombre LIKE '%CAPITAL%' OR nombre LIKE '%FOMAG%' OR nombre LIKE '%POLICIA%'
       OR nombre LIKE '%MEDISANITAS%' OR nombre LIKE '%COLSANITAS%'
    ORDER BY nombre
""")
cols = [desc[0] for desc in cursor.description]
print("  |  ".join(cols))
print("-" * 100)
for row in cursor.fetchall():
    print("  |  ".join(str(v) if v is not None else 'NULL' for v in row))

print()
print("=== VALORES DISTINTOS DE tipoRegimen ===")
cursor.execute("SELECT DISTINCT tipoRegimen, COUNT(*) as cant FROM sis_empre GROUP BY tipoRegimen ORDER BY tipoRegimen")
for row in cursor.fetchall():
    print(f"  tipoRegimen={row[0]}  ->  {row[1]} entidades")

conn.close()
