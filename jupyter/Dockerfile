FROM quay.io/jupyter/datascience-notebook:2026-04-02

# 1. Install build dependencies
USER root
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*
USER jovyan

# 2. Install jupyter deptendecies
RUN pip install --no-cache-dir \
    jupyterlab-lsp \
    'python-lsp-server[all]' \
    jupyterlab-gruvbox-dark \
    jupyterthemes \
    sqlalchemy \
    mysqlclient

# Expose the work directory
WORKDIR /home/jovyan/work
