FROM python:3.11

# Establecer directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema + ODBC Driver 17 para SQL Server
RUN apt-get update && apt-get install -y \
    smbclient \
    curl \
    gnupg \
    unixodbc \
    unixodbc-dev \
    libgssapi-krb5-2 \
 && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
 && curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y msodbcsql17 \
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
