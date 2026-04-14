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

# 强制 CPU 版 Torch（仅作为额外源，不参与普通包主索引解析）
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV FORCE_CUDA=0

# 复制依赖
COPY requirements.txt .

# 大包与全量依赖分步安装，并提供镜像回退，降低 Broken pipe 失败概率
RUN set -eux; \
    install_with_fallback () { \
      args="$1"; \
      for index in "https://mirrors.aliyun.com/pypi/simple/" "https://pypi.org/simple"; do \
        for attempt in 1 2 3; do \
          echo "pip install attempt=${attempt} index=${index} args=${args}"; \
          if PIP_INDEX_URL="${index}" pip install --no-cache-dir --progress-bar off ${args} --trusted-host mirrors.aliyun.com --timeout 7200 --retries 20; then \
            return 0; \
          fi; \
          sleep $((attempt * 5)); \
        done; \
      done; \
      return 1; \
    }; \
    install_with_fallback "chromadb~=1.1.0 --only-binary=:all:"; \
    install_with_fallback "-r requirements.txt --prefer-binary"

# 构建期校验：DashScope 等需 streamable_http_client（mcp>=1.25，1.15 仅有 streamablehttp_client）
RUN python -c "from importlib.metadata import version as _v; print('mcp', _v('mcp')); from mcp.client.streamable_http import streamable_http_client; print('streamable_http_client ok')"

# 复制代码
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]