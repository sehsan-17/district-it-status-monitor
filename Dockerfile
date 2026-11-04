FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config ./config
COPY scripts ./scripts
COPY sql ./sql
COPY cloud ./cloud
RUN useradd --create-home collector && mkdir -p /app/data && chown collector /app/data
USER collector
CMD ["python", "cloud/cloud_run_entry.py"]
