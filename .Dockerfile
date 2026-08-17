# syntax=docker/dockerfile:1

# 3.12 is the floor: pysage-tools (the game.dat patches behind /patch) requires it.
FROM python:3.13-slim

WORKDIR /python-docker

# git, because pysage-tools is installed from its repository rather than from PyPI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

CMD [ "python3", "-m" , "flask", "run", "--host=0.0.0.0"]
