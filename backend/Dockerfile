FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（opencv、编译、pdf 必备）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖
COPY requirements.txt .

# 安装依赖（阿里云源 + 超时配置，解决大文件失败）
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    --timeout 1000 \
    --retries 10

# 复制代码
COPY . .

# 暴露端口
EXPOSE 8000

# 启动
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]