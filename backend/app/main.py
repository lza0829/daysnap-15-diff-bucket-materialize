from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import seed
from app.routers import api

app = FastAPI(title="日镜账", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 导入即确保表结构就绪：兼容未用 with TestClient(app) 触发 startup 的调用/测试。
seed.初始化库()


@app.on_event("startup")
def _启动():
    seed.初始化库()


app.include_router(api)
