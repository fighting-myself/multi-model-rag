FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 先升级 pip（必须）
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 强制 CPU 版 Torch（关键）
ENV PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV FORCE_CUDA=0

# 复制依赖
COPY requirements.txt .

# 正常安装所有依赖（去掉 --no-deps！！！）
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    --timeout 7200 \
    --retries 20

# 构建期校验：DashScope 等需 mcp.client.streamable_http；若缺失说明依赖未装全或镜像过旧
RUN python -c "from importlib.metadata import version as _v; print('mcp', _v('mcp')); from mcp.client.streamable_http import streamable_http_client; print('streamable_http_client ok')"

# 复制代码
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]