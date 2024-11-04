# 使用官方 Python 3.12.2 镜像作为基础镜像
FROM python:3.12.2

# 设置工作目录
WORKDIR /app

# 将 requirements.txt 文件复制到工作目录
COPY requirements.txt .

# 确保 pip 是最新版本并安装 Python 依赖
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

# 将项目代码复制到工作目录
COPY . .

# 设置默认命令
ENTRYPOINT ["python", "main.py"]