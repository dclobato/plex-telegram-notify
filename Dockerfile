FROM python:3.13-slim

WORKDIR /usr/app/src

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY run.py ./

CMD ["python", "./run.py"]
