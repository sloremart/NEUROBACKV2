import pyodbc
conn = pyodbc.connect('DRIVER={ODBC Driver 17 for SQL Server};SERVER=LorenaM;DATABASE=ZeusSalud_Neuro;Trusted_Connection=yes;')
cursor = conn.cursor()
cursor.execute("""
    SELECT c.codigo, c.alias, c.empresa AS empresa_contrato,
           se.codigo AS codigo_eps, se.nombre AS nombre_eps
    FROM contratos c
    LEFT JOIN sis_empre se ON LTRIM(RTRIM(c.empresa)) = LTRIM(RTRIM(se.codigo))
    WHERE c.alias IS NOT NULL AND LTRIM(RTRIM(c.alias)) != ''
    ORDER BY c.alias
""")
print(f"{'COD_CONT':<10} {'ALIAS':<45} {'EMPRESA':<10} {'COD_EPS':<12} {'NOMBRE EPS'}")
print("-" * 130)
for row in cursor.fetchall():
    print(f"{str(row[0]):<10} {str(row[1]):<45} {str(row[2] or ''):<10} {str(row[3] or ''):<12} {str(row[4] or '')}")
conn.close()
