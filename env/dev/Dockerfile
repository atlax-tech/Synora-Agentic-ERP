# Synora Phase 1 开发环境 Dockerfile（Inc-1 / P1.1）
# 基于官方 Frappe v16 Docker 构建基线；最终版本基线由 versions.env 锁定。
ARG FDP_BASE_IMAGE=python:3.14-bookworm
FROM ${FDP_BASE_IMAGE}

ARG FDP_VER_NODE=24
ARG FDP_VER_BENCH=5.31.0
ENV DEBIAN_FRONTEND=noninteractive \
    NODE_VERSION=${FDP_VER_NODE}

# 构建工具链 + MariaDB/Redis client + 字体（WeasyPrint）
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common curl gnupg ca-certificates \
        build-essential pkg-config \
        default-libmysqlclient-dev libmariadb-dev \
        mariadb-client \
        libssl-dev libffi-dev libjpeg62-turbo-dev zlib1g-dev \
        libxml2-dev libxslt1-dev libldap2-dev libsasl2-dev \
        libblas-dev liblapack-dev libatlas-base-dev \
        fonts-dejavu redis-tools \
        cron \
    && rm -rf /var/lib/apt/lists/*

# Node ${FDP_VER_NODE}（version-16 要求 >=24）+ yarn
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g yarn \
    && rm -rf /var/lib/apt/lists/*

# 官方包名是 frappe-bench（bench 仅为 CLI）
RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 -m pip install --no-cache-dir "frappe-bench==${FDP_VER_BENCH}"

# frappe-bench 拒绝以 root 运行初始化/建站等命令，创建专用非 root 用户
RUN useradd -m -u 1000 frappe

WORKDIR /home/frappe
USER frappe
