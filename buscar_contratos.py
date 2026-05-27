import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};SERVER=LorenaM;DATABASE=ZeusSalud_Neuro;Trusted_Connection=yes;'
)
cursor = conn.cursor()

print("=== COLUMNAS DE 'contratos' ===")
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'contratos'
    ORDER BY ORDINAL_POSITION
""")
for row in cursor.fetchall():
    print(f"  {row[0]}  ({row[1]})")

print()
print("=== MUESTRA: contratos ligados a EPS SANITAS / COMPENSAR / SALUD TOTAL ===")
cursor.execute("""
    SELECT c.id, c.codigo, c.nombre, c.alias, c.eps, se.nombre AS nombre_eps
    FROM contratos c
    LEFT JOIN sis_empre se ON LTRIM(RTRIM(c.eps)) = LTRIM(RTRIM(se.codigo))
    WHERE c.alias LIKE '%SANITAS%' OR c.alias LIKE '%COMPENSAR%' OR c.alias LIKE '%SALUD TOTAL%'
       OR c.alias LIKE '%CAPITAL%' OR c.alias LIKE '%FOMAG%' OR c.alias LIKE '%POLICIA%'
       OR c.alias LIKE '%MEDISANITAS%' OR c.alias LIKE '%COLSANITAS%'
    ORDER BY c.alias
""")
cols = [desc[0] for desc in cursor.description]
print("  " + " | ".join(cols))
print("  " + "-" * 100)
for row in cursor.fetchall():
    print("  " + " | ".join(str(v) if v is not None else 'NULL' for v in row))

conn.close()
