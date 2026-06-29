FROM python:3.11

# Establecer directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema + ODBC Driver 18 para SQL Server (Debian 12)
RUN apt-get update && apt-get install -y smbclient curl gnupg2 \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
    > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev \
 && curl -L "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb" -o /tmp/wkhtmltox.deb \
 && apt-get install -y /tmp/wkhtmltox.deb \
 && rm /tmp/wkhtmltox.deb \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copiar e instalar requerimientos de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Exponer el puerto del servidor Django
EXPOSE 8000

# Comando de inicio
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
