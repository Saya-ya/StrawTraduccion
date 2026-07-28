#!/usr/bin/env python3
"""Launch the Strawberry Panic Translation Manager web app."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("webapp.main:app", host="0.0.0.0", port=8080, reload=True,
                reload_dirs=["webapp"])
