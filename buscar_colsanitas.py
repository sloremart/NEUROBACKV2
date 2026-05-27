import pyodbc
conn = pyodbc.connect('DRIVER={ODBC Driver 17 for SQL Server};SERVER=LorenaM;DATABASE=ZeusSalud_Neuro;Trusted_Connection=yes;')
cursor = conn.cursor()
cursor.execute("SELECT codigo, alias FROM contratos WHERE alias LIKE '%COLSANITAS%' OR alias LIKE '%MEDISANITAS%' ORDER BY alias")
print("=== CONTRATOS COLSANITAS / MEDISANITAS ===")
for row in cursor.fetchall():
    print(f"  codigo={row[0]}  alias={row[1]}")
conn.close()
