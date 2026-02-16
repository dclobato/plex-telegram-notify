FROM python:3.13-slim

WORKDIR /usr/app/src

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY run.py ./

CMD ["python", "./run.py"]
