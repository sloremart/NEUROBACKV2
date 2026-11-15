FROM python:3.11-slim-bookworm

# Evitar prompts interactivos durante la instalación de paquetes
ENV DEBIAN_FRONTEND=noninteractive

# Instalar dependencias del sistema requeridas para pyodbc y SQL Server
RUN apt-get update && apt-get install -y \
    curl \
    gnupg2 \
    apt-transport-https \
    build-essential \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17 unixodbc-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Copiar el archivo de dependencias
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && playwright install chromium --with-deps && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copiar el código fuente
COPY . .

# El parámetro -u permite ver los prints (logs) en tiempo real en la consola de Docker
CMD ["python", "-u", "main.py"]
