"""启动入口：python -m quant.app"""

from __future__ import annotations

import uvicorn

from .settings import settings

if __name__ == "__main__":
    settings.ensure_dirs()
    uvicorn.run("quant.app.api:app", host=settings.host, port=settings.port,
                workers=1, log_level="info")
