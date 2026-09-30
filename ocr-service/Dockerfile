FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install --upgrade paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/ \
    && pip install -r requirements.txt
COPY app.py .
EXPOSE 8010
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
