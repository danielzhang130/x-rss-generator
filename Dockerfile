FROM python:3.9

COPY requirements.txt ./
RUN pip install -r requirements.txt
RUN apt-get update && apt-get install -y chromium-driver
RUN mkdir screenshot
COPY main.py .env ./
ENTRYPOINT python -u main.py
