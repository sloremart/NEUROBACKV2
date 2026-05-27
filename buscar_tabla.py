import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};SERVER=LorenaM;DATABASE=ZeusSalud_Neuro;Trusted_Connection=yes;'
)
cursor = conn.cursor()

print("=== TABLAS CON COLUMNA 'alias' ===")
cursor.execute("""
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS
    WHERE COLUMN_NAME = 'alias'
    ORDER BY TABLE_NAME
""")
for row in cursor.fetchall():
    print(" ", row[0])

print()
print("=== BUSCANDO 'SANITAS EVENTO' EN TABLAS CON COLUMNAS VARCHAR ===")
cursor.execute("""
    SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
    WHERE DATA_TYPE IN ('varchar','nvarchar','char','nchar')
    AND TABLE_NAME NOT LIKE 'sys%'
    ORDER BY TABLE_NAME, COLUMN_NAME
""")
tabla_cols = cursor.fetchall()

encontrados = {}
for tabla, col in tabla_cols:
    try:
        cursor2 = conn.cursor()
        cursor2.execute(f"SELECT TOP 1 [{col}] FROM [{tabla}] WHERE [{col}] LIKE '%SANITAS EVENTO%'")
        row = cursor2.fetchone()
        if row:
            if tabla not in encontrados:
                encontrados[tabla] = []
            encontrados[tabla].append((col, row[0]))
    except:
        pass

if encontrados:
    for tabla, hits in encontrados.items():
        print(f"\n  TABLA: {tabla}")
        for col, val in hits:
            print(f"    columna [{col}] = {val}")
else:
    print("  No encontrado en ninguna tabla de ZeusSalud_Neuro")

conn.close()
