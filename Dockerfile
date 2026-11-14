# Prediction service for the automated competition rounds (see model/service.py).
#   docker build -t pcom-predict .
#   docker run -d --name pcom-predict --restart unless-stopped -p 8000:8000 pcom-predict
FROM python:3.14-slim

# LightGBM needs the OpenMP runtime, which the slim image does not have
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt

COPY npmdata/ npmdata/
COPY model/ model/

EXPOSE 8000
CMD ["uvicorn", "model.service:app", "--host", "0.0.0.0", "--port", "8000"]
