FROM python:3.11

# Establecer directorio de trabajo
WORKDIR /app

# Instalar smbclient y dependencias del sistema
RUN apt-get update && apt-get install -y smbclient

# Copiar e instalar requerimientos de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Exponer el puerto del servidor Django
EXPOSE 8000

# Comando de inicio
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
